-- aeroflow-jwks — Kong plugin
--
-- Replaces: JWT plugin + jwks-refresher service
--
-- On every request carrying an `sk-` API key (X-API-Key, or Bearer sk-...):
--   verify it at auth_api (cached for api_key_cache_ttl) and inject the same
--   identity headers as for a JWT. Everything else goes the JWT way below.
--
-- On every request:
--   1. Extract Bearer token
--   2. Decode JWT header → get kid + alg
--   3. Fetch JWKS from Keycloak (kong.cache, TTL = jwks_refresh_interval)
--   4. Verify RS256 signature using resty.openssl (bundled in Kong 3.6)
--   5. Validate exp / nbf / iss claims
--   6. Inject X-User-Id, X-User-Roles, X-User-Email, X-Kong-Verified headers

local http  = require "resty.http"
local cjson = require "cjson.safe"
local pkey  = require "resty.openssl.pkey"
local digest = require "resty.openssl.digest"
local to_hex = require("resty.string").to_hex

local AeroflowJwks   = {}
AeroflowJwks.PRIORITY = 1010   -- runs before pre-function (1000) is irrelevant; after rate-limit etc.
AeroflowJwks.VERSION  = "1.0.0"

-- ── helpers ────────────────────────────────────────────────────────────

local function b64url_decode(s)
  s = s:gsub("%-", "+"):gsub("_", "/")
  s = s .. ("="):rep((4 - #s % 4) % 4)
  return ngx.decode_base64(s)
end

local function encode_len(l)
  if     l < 128 then return string.char(l)
  elseif l < 256 then return string.char(0x81, l)
  else               return string.char(0x82, math.floor(l / 256), l % 256)
  end
end

local function encode_int(b)
  if b:byte(1) >= 128 then b = "\x00" .. b end
  return "\x02" .. encode_len(#b) .. b
end

-- Convert RSA JWK {n, e} to PEM SPKI string
local function jwk_to_pem(jwk)
  local n = b64url_decode(jwk.n)
  local e = b64url_decode(jwk.e)
  if not n or not e then return nil, "invalid JWK n/e" end

  local ni, ei  = encode_int(n), encode_int(e)
  local rsa_seq = "\x30" .. encode_len(#ni + #ei) .. ni .. ei
  local oid     = "\x30\x0d\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01\x05\x00"
  local bs      = "\x03" .. encode_len(#rsa_seq + 1) .. "\x00" .. rsa_seq
  local spki    = "\x30" .. encode_len(#oid + #bs) .. oid .. bs

  local b64   = ngx.encode_base64(spki)
  local lines = {"-----BEGIN PUBLIC KEY-----"}
  for i = 1, #b64, 64 do lines[#lines + 1] = b64:sub(i, i + 63) end
  lines[#lines + 1] = "-----END PUBLIC KEY-----"
  return table.concat(lines, "\n")
end

-- ── JWKS cache loader ──────────────────────────────────────────────────

local function load_jwks(jwks_uri)
  local httpc = http.new()
  httpc:set_timeout(5000)
  -- ssl_verify=true rejects self-signed certs; set ssl_capath to the system bundle
  -- or a custom CA file if Keycloak uses an internal PKI.
  local res, err = httpc:request_uri(jwks_uri, {
    ssl_verify = true,
    ssl_capath = "/etc/ssl/certs",
  })
  if not res then return nil, "JWKS HTTP error: " .. (err or "?") end
  if res.status ~= 200 then return nil, "JWKS status: " .. res.status end

  local data, jerr = cjson.decode(res.body)
  if not data then return nil, "JWKS JSON error: " .. (jerr or "") end

  local keys = {}
  for _, k in ipairs(data.keys or {}) do
    if k.kty == "RSA" and (not k.use or k.use == "sig") then
      local pem, perr = jwk_to_pem(k)
      if pem then
        keys[k.kid or "default"] = pem
      else
        kong.log.warn("jwk_to_pem failed kid=", k.kid, ": ", perr)
      end
    end
  end
  if not next(keys) then return nil, "no RSA sig keys in JWKS" end
  return keys
end

local function fetch_keys(conf)
  -- Cache TTL = grace_period so old keys stay available after Keycloak rotates.
  -- Without this, the cache TTL would track refresh_interval and a rotation
  -- mid-cycle could invalidate keys signing tokens that are still in flight.
  return kong.cache:get(
    "aeroflow_jwks|" .. conf.jwks_uri,
    { ttl = conf.grace_period or 600 },
    load_jwks, conf.jwks_uri
  )
end

-- Lazy merge: fetch fresh JWKS and union it with the keys we already have
-- in cache, then atomically replace the cache entry. The point is that the
-- OLD keys do NOT get evicted just because Keycloak rolled a new kid — any
-- token signed with the old key remains verifiable up to grace_period.
--
-- Returns the merged set (caller should look up the kid in it). Returns the
-- input `current_keys` unchanged if the fresh fetch fails (graceful
-- degradation: better to trust the slightly-stale cache than reject all
-- traffic when Keycloak is briefly down).
local function refresh_and_merge(conf, current_keys)
  local fresh, err = load_jwks(conf.jwks_uri)
  if not fresh then
    kong.log.warn("JWKS refresh failed during grace merge: ", err)
    return current_keys
  end

  local merged = {}
  if current_keys then
    for k, v in pairs(current_keys) do merged[k] = v end
  end
  -- Fresh keys overwrite stale entries with the same kid (defensive — kid
  -- is supposed to be unique per key, but if Keycloak ever reissues a kid
  -- with new material we prefer the new one).
  for k, v in pairs(fresh) do merged[k] = v end

  -- Replace cache entry by invalidate + re-warm. kong.cache doesn't expose
  -- a direct `set`, so we use `:get` with a fetcher that returns the merged
  -- set immediately.
  local cache_key = "aeroflow_jwks|" .. conf.jwks_uri
  kong.cache:invalidate(cache_key)
  kong.cache:get(cache_key, { ttl = conf.grace_period or 600 }, function()
    return merged
  end)
  return merged
end

-- ── RS256 verify ────────────────────────────────────────────────────────

local function verify_rs256(signing_input, sig_b64, pem_str)
  local sig_bytes = b64url_decode(sig_b64)
  if not sig_bytes then return false, "base64url decode failed" end

  local pub, err = pkey.new(pem_str, { format = "PEM" })
  if not pub then return false, "pkey load: " .. (err or "") end

  -- RS256 = RSASSA-PKCS1-v1_5 + SHA-256
  local ok, verr = pub:verify(sig_bytes, signing_input, "sha256", pub.RSASSA_PKCS1_PADDING)
  return ok, verr
end

-- ── API keys ────────────────────────────────────────────────────────────

-- Returns the identity table, nil (key rejected — cached for neg_ttl), or
-- nil + err (auth_api unreachable — not cached, so the next request retries).
local function load_api_key_identity(verify_url, gateway_key, key)
  local httpc = http.new()
  httpc:set_timeout(5000)
  local res, err = httpc:request_uri(verify_url, {
    method  = "POST",
    body    = cjson.encode({ key = key }),
    headers = { ["Content-Type"] = "application/json", ["X-Gateway-Key"] = gateway_key },
  })
  if not res then return nil, "API key verify HTTP error: " .. (err or "?") end
  if res.status == 401 then return nil end
  if res.status ~= 200 then return nil, "API key verify status: " .. res.status end
  local identity, jerr = cjson.decode(res.body)
  if not identity then return nil, "API key verify JSON error: " .. (jerr or "") end
  return identity
end

local function authenticate_api_key(conf, key)
  if not conf.api_key_verify_url or not conf.gateway_key or conf.gateway_key == "" then
    return kong.response.exit(401, { message = "API keys are not enabled" })
  end
  -- The cache key is a hash so the raw key never sits in the shared cache.
  -- It includes the verify URL: the cache is node-wide, and an answer from one
  -- plugin instance's auth_api must not stand in for another's.
  local sha = digest.new("sha256")
  local cache_key = "aeroflow_apikey|" .. conf.api_key_verify_url .. "|" .. to_hex(sha:final(key))
  local identity, err = kong.cache:get(
    cache_key,
    { ttl = conf.api_key_cache_ttl or 60, neg_ttl = 5 },
    load_api_key_identity, conf.api_key_verify_url, conf.gateway_key, key
  )
  if err then
    kong.log.err("API key verification unavailable: ", err)
    return kong.response.exit(503, { message = "Authentication service unavailable" })
  end
  if not identity then
    return kong.response.exit(401, { message = "Invalid API key" })
  end

  local roles = identity.roles or {}
  kong.service.request.set_header("X-User-Id",      identity.user_id   or "")
  kong.service.request.set_header("X-User-Email",   "")
  kong.service.request.set_header("X-User-Name",    identity.user_name or "")
  kong.service.request.set_header("X-User-Roles",   table.concat(roles, ","))
  kong.service.request.set_header("X-Tenant-Id",    identity.tenant_id or "")
  kong.service.request.set_header("X-Role-Id",      roles[1]           or "")
  kong.service.request.set_header("X-Kong-Verified", "true")
  kong.service.request.set_header("X-Gateway-Key",  conf.gateway_key)
  -- The key is a credential for the gateway only, whatever hide_credentials says.
  kong.service.request.clear_header("X-API-Key")
  kong.service.request.clear_header("Authorization")
end

-- ── access handler ──────────────────────────────────────────────────────

function AeroflowJwks:access(conf)
  -- Clear any client-supplied identity headers to prevent spoofing on routes
  -- that don't go through this plugin.
  kong.service.request.clear_header("X-User-Id")
  kong.service.request.clear_header("X-User-Email")
  kong.service.request.clear_header("X-User-Name")
  kong.service.request.clear_header("X-User-Roles")
  kong.service.request.clear_header("X-Tenant-Id")
  kong.service.request.clear_header("X-Role-Id")
  kong.service.request.clear_header("X-Kong-Verified")
  -- Proof that X-Kong-Verified came from this plugin and not from a client
  -- that reached the upstream directly. Cleared here for the same reason as
  -- the identity headers, and re-set below only after the JWT verifies.
  kong.service.request.clear_header("X-Gateway-Key")
  -- A service asserting an end user's identity (workflow_runtime ->
  -- kb-backend) is a backend-only path; a browser must never be able to
  -- present one through the gateway.
  kong.service.request.clear_header("X-Service-Assertion")

  -- 0. API key. Keys start with sk- and have no dots, so neither form can be
  -- mistaken for a JWT.
  local auth = kong.request.get_header("Authorization")
  local api_key = kong.request.get_header("X-API-Key")
    or (auth and auth:match("^[Bb]earer%s+(sk%-%S+)$"))
  if api_key then
    return authenticate_api_key(conf, api_key)
  end

  -- 1. Bearer token
  if not auth then
    return kong.response.exit(401, { message = "Authorization header missing" })
  end
  local token = auth:match("^[Bb]earer%s+(.+)$")
  if not token then
    return kong.response.exit(401, { message = "Bearer token required" })
  end

  -- 2. Split JWT
  local h_b64, p_b64, s_b64 = token:match("^([^.]+)%.([^.]+)%.([^.]+)$")
  if not h_b64 then
    return kong.response.exit(401, { message = "Malformed JWT" })
  end

  -- 3. Decode header
  local header, herr = cjson.decode(b64url_decode(h_b64) or "")
  if not header then
    return kong.response.exit(401, { message = "JWT header decode: " .. (herr or "") })
  end
  if header.alg ~= "RS256" then
    return kong.response.exit(401, { message = "Unsupported alg: " .. (header.alg or "?") })
  end

  -- 4. Fetch JWKS (cached)
  local keys, kerr = fetch_keys(conf)
  if not keys then
    kong.log.err("JWKS unavailable: ", kerr)
    return kong.response.exit(503, { message = "Authentication service unavailable" })
  end

  -- 5. Find key by kid. If we don't have it, the most likely cause is that
  -- Keycloak rotated since our last cache fill. Merge fresh keys into the
  -- cache rather than invalidating — that preserves any old kids still in
  -- flight (grace_period covers them).
  local kid = header.kid
  local pem = (kid and keys[kid]) or keys["default"]
  if not pem then
    keys = refresh_and_merge(conf, keys)
    pem = keys and ((kid and keys[kid]) or keys["default"])
  end
  if not pem then
    return kong.response.exit(401, { message = "No JWK for kid: " .. (kid or "none") })
  end

  -- 6. Verify signature
  local ok, verr = verify_rs256(h_b64 .. "." .. p_b64, s_b64, pem)
  if not ok then
    return kong.response.exit(401, { message = "Invalid signature: " .. (verr or "") })
  end

  -- 7. Claims
  local payload, perr = cjson.decode(b64url_decode(p_b64) or "")
  if not payload then
    return kong.response.exit(401, { message = "JWT payload decode: " .. (perr or "") })
  end
  local now = ngx.time()
  if payload.exp and payload.exp < now then
    return kong.response.exit(401, { message = "Token expired" })
  end
  if payload.nbf and payload.nbf > now + 10 then
    return kong.response.exit(401, { message = "Token not yet valid" })
  end
  if conf.issuer and payload.iss ~= conf.issuer then
    return kong.response.exit(401, { message = "Issuer mismatch" })
  end

  -- 8. Audience validation (optional — only enforced when conf.audience is set)
  if conf.audience then
    local aud = payload.aud
    local ok_aud = false
    if type(aud) == "string" then
      ok_aud = aud == conf.audience
    elseif type(aud) == "table" then
      for _, v in ipairs(aud) do
        if v == conf.audience then ok_aud = true; break end
      end
    end
    if not ok_aud then
      return kong.response.exit(401, { message = "Audience mismatch" })
    end
  end

  -- 9. Inject headers for backend; headers were cleared above so these are authoritative.
  -- X-User-Roles: prefer custom role_id claim (single role, from user attribute mapper),
  -- fall back to realm_access.roles (standard Keycloak JWT claim, comma-joined).
  local roles_str = payload.role_id or ""
  if roles_str == "" and payload.realm_access and type(payload.realm_access.roles) == "table" then
    local app_roles = {}
    for _, r in ipairs(payload.realm_access.roles) do
      -- Filter out Keycloak system roles
      if not r:match("^default%-roles%-") and r ~= "offline_access" and r ~= "uma_authorization" then
        app_roles[#app_roles + 1] = r
      end
    end
    roles_str = table.concat(app_roles, ",")
  end

  kong.service.request.set_header("X-User-Id",      payload.sub               or "")
  kong.service.request.set_header("X-User-Email",   payload.email             or "")
  kong.service.request.set_header("X-User-Name",    payload.preferred_username or "")
  kong.service.request.set_header("X-User-Roles",   roles_str)
  kong.service.request.set_header("X-Tenant-Id",    payload.tenant_id         or "")
  kong.service.request.set_header("X-Role-Id",      payload.role_id           or "")
  kong.service.request.set_header("X-Kong-Verified", "true")
  -- Upstreams compare this against their own KB_GATEWAY_KEY. Without it,
  -- X-Kong-Verified is only as trustworthy as the network: any caller able to
  -- reach the upstream port could send it and be believed. Optional so a
  -- gateway configured before this field existed keeps working.
  if conf.gateway_key and conf.gateway_key ~= "" then
    kong.service.request.set_header("X-Gateway-Key", conf.gateway_key)
  end

  -- Strip the Authorization header from the upstream request when hide_credentials is enabled.
  if conf.hide_credentials then
    kong.service.request.clear_header("Authorization")
  end
end

return AeroflowJwks
