local typedefs = require "kong.db.schema.typedefs"

return {
  name = "aeroflow-jwks",
  fields = {
    { protocols = typedefs.protocols_http },
    { config = {
      type = "record",
      fields = {
        { jwks_uri              = typedefs.url({ required = true })            },
        { issuer                = { type = "string",  required = false }       },
        { audience              = { type = "string",  required = false }       },
        { jwks_refresh_interval = { type = "number",  default = 300  }        },
        -- How long an old signing key stays acceptable AFTER Keycloak rotates.
        -- Live tokens in flight when rotation happens were signed with the
        -- old key; without a grace period they would all get 401 instantly.
        -- Default 600s (10 min) covers token-lifetime windows that are
        -- typically <=15 min in Keycloak's default profile.
        { grace_period          = { type = "number",  default = 600  }        },
        { hide_credentials      = { type = "boolean", default = true  }       },
        -- Shared secret injected as X-Gateway-Key on verified requests, so an
        -- upstream can tell "Kong verified a JWT for this request" apart from
        -- "someone reached my port and typed X-Kong-Verified: true". Must match
        -- KB_GATEWAY_KEY on the upstream. Marked encrypted so it is not echoed
        -- back in plaintext by the Kong Admin API.
        { gateway_key           = { type = "string",  required = false, encrypted = true } },
      },
    }},
  },
}
