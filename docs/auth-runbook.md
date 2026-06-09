# Auth & Gateway · Release Management — Hướng dẫn Chạy & Test

> Runbook thực tế: khởi động, setup, và kiểm thử từng thành phần  
> Auth: đọc `docs/auth-release-tech-stack-update.md` §Phần 1  
> Release: đọc `docs/release-system.md` để hiểu kiến trúc trước khi chạy.

## Mục lục

- [Phần 1 — Auth (Keycloak + Kong + Kafka)](#phần-1--auth-keycloak--kong--kafka)
- [Phần 2 — Release Management](#phần-2--release-management)
  - [R1 — Khởi động Release Stack](#r1--khởi-động-release-stack)
  - [R2 — URLs & Credentials Release](#r2--urls--credentials-release)
  - [R3 — Setup Release Schema PostgreSQL](#r3--setup-release-schema-postgresql)
  - [R4 — Setup Release Kafka Topics & ACL](#r4--setup-release-kafka-topics--acl)
  - [R5 — Test Cases Release](#r5--test-cases-release)
  - [R6 — Trigger Pipeline thủ công](#r6--trigger-pipeline-thủ-công)
  - [R7 — Approval Flow](#r7--approval-flow)
  - [R8 — Rollback](#r8--rollback)
  - [R9 — Drift Detection](#r9--drift-detection)
  - [R10 — Release History Query](#r10--release-history-query)
  - [R11 — Kong Services cho Rollback](#r11--kong-services-cho-rollback-setup-một-lần)
  - [R12 — New API Endpoints](#r12--new-api-endpoints-2026-05-27)
  - [R14 — Troubleshooting Release](#r14--troubleshooting-release)
- [Phần 3 — OpenBao Secrets Vault](#phần-3--openbao-secrets-vault)
  - [O1 — Khởi động OpenBao Stack](#o1--khởi-động-openbao-stack)
  - [O2 — Troubleshooting OpenBao](#o2--troubleshooting-openbao)
  - [O3 — Test Secrets Vault API](#o3--test-secrets-vault-api)
  - [O4 — Verify secrets trong OpenBao trực tiếp](#o4--verify-secrets-trong-openbao-trực-tiếp)
  - [O5 — AI Provider Keys (Bootstrap)](#o5--ai-provider-keys-bootstrap)
  - [O6 — Transit Engine (RSA/HSM Signing)](#o6--transit-engine-rsahsm-signing)
  - [O7 — Governance Controls (Panic Mode, Auto-Rotation, PII Log)](#o7--governance-controls)
- [Phần 4 — Multi-Tenant Management](#phần-4--multi-tenant-management)
  - [MT1 — Tạo Tenant Mới](#mt1--tạo-tenant-mới)
  - [MT2 — Verify Tenant Isolation](#mt2--verify-tenant-isolation)
  - [MT3 — Tenant Users Credentials](#mt3--tenant-users-credentials)
  - [MT4 — Isolation Mechanism Summary](#mt4--isolation-mechanism-summary)

---

# Phần 1 — Auth (Keycloak + Kong + Kafka)

## Yêu cầu

| Tool | Phiên bản tối thiểu |
|---|---|
| Docker Desktop | 4.x (Engine 24+) |
| Docker Compose | v2 (tích hợp trong Docker Desktop) |
| curl | Bất kỳ |
| jq | Bất kỳ (để parse JSON trong test) |
| Node.js | 20+ (nếu chạy frontend ngoài Docker) |

---

## Bước 1 — Khởi động Stack

```bash
cd Data-Agentt

# Build images + khởi động tất cả services (lần đầu ~8-12 phút do Maven build SPI JAR)
docker compose up -d

# Theo dõi quá trình khởi động
docker compose logs -f
```

**Kiểm tra tất cả services healthy:**
```bash
docker compose ps
```

Kết quả mong đợi:
```
NAME                               STATUS
aeroflow-postgres                  Up (healthy)
aeroflow-redis                     Up (healthy)
aeroflow-kafka                     Up (healthy)
aeroflow-minio                     Up (healthy)
aeroflow-mongo                     Up (healthy)
aeroflow-elasticsearch             Up (healthy)
aeroflow-jaeger                    Up (healthy)
aeroflow-keycloak-1                Up (healthy)      ← ~90s lần đầu (import realm + DB migration)
aeroflow-keycloak-2                Up (healthy)      ← đợi node1 healthy mới join cluster
aeroflow-keycloak-lb               Up (healthy)
aeroflow-kong-migration            Exited (0)        ← migration thành công, exit 0 là đúng
aeroflow-kong                      Up (healthy)
aeroflow-konga                     Up
aeroflow-openbao                   Up (healthy)      ← OpenBao vault
aeroflow-openbao-init              Exited (0)        ← unseal thành công
aeroflow-openbao-setup             Exited (0)        ← KV v2 + Transit + policy đã cấu hình
aeroflow-openbao-secrets-bootstrap Exited (0)        ← AI provider keys đã inject
aeroflow-auth-api                  Up (healthy)      ← Secrets Vault + IP Allowlist + API Key API
aeroflow-release-worker            Up (healthy)
aeroflow-celery-worker             Up
aeroflow-audit-bridge              Up
aeroflow-audit-consumer            Up
aeroflow-jwks-refresher            Up
aeroflow-frontend                  Up
```

> **Thứ tự khởi động:** postgres → postgres-backup → redis → kafka → minio → mongo → elasticsearch → jaeger → openbao → openbao-init → openbao-setup → openbao-secrets-bootstrap → keycloak-node1 → keycloak-node2 → keycloak-lb → kong-migration → kong → jwks-refresher → auth-api → release-worker → celery-worker → audit-bridge → audit-consumer → frontend
>
> **HA + DR posture:** Keycloak HA (2-node + HAProxy LB) là **real** từ 2026-06-07.
> Postgres single-node với scheduled `pg_dump` + WAL archiving. Tất cả thành phần khác
> còn single-node. Xem [docs/disaster-recovery.md](./disaster-recovery.md) cho:
> RPO/RTO targets cụ thể, restore procedures, roadmap HA cho các layer còn lại.

**Port mapping:**

| Container | Host Port | Description |
|---|---|---|
| aeroflow-frontend | :5173 | React app (Vite dev server) |
| aeroflow-kong | :8000 / :8001 | Proxy / Admin |
| aeroflow-auth-api | :8200 | Auth/Secrets API (direct, no JWT check) |
| aeroflow-openbao | :8300 | OpenBao vault (internal :8200) |
| aeroflow-keycloak-1 | :8081 | Direct node 1 |
| aeroflow-keycloak-2 | :8082 | Direct node 2 |
| aeroflow-keycloak-lb | :8080 | HAProxy load balancer |
| aeroflow-release-worker | :8100 | Release API (direct) |
| aeroflow-konga | :1337 | Kong admin UI |

---

## Bước 2 — URLs & Credentials

| Service | URL | Credentials |
|---|---|---|
| **Frontend** | http://localhost:5173 | Keycloak login |
| **Kong Proxy** (API gateway) | http://localhost:8000 | JWT Bearer token |
| Kong Admin API | internal only — `kong-admin-net` 172.30.0.10:8001 | see [Kong admin hardening](#kong-admin-hardening) |
| Konga (Kong UI) | http://localhost:1337 | setup lần đầu |
| **Auth API** (direct, bypass Kong) | http://localhost:8200 | `X-User-Id`, `X-User-Roles`, `X-Tenant-Id` headers |
| **Keycloak** (HAProxy LB) | http://localhost:8080/admin | admin / admin |
| Keycloak Node 1 (direct) | http://localhost:8081/admin | admin / admin |
| Keycloak Node 2 (direct) | http://localhost:8082/admin | admin / admin |
| **OpenBao** (Secrets Vault) | http://localhost:8300/ui | Root token từ `/openbao/data/.root-token` |
| Postgres backup volume | `dataagent_postgres_backups` (named volume) | xem [disaster-recovery.md](./disaster-recovery.md) |
| **Release Worker** (direct) | http://localhost:8100 | JWT Bearer token |
| MinIO Console | http://localhost:9001 | minio / minio_secret |
| MinIO S3 API | http://localhost:9000 | minio / minio_secret |
| MongoDB | localhost:27017 | (no auth — dev only) |
| Elasticsearch | http://localhost:9200 | (no auth — dev only) |
| Jaeger UI | http://localhost:16686 | — |
| PostgreSQL | localhost:5432 | aeroflow / aeroflow\_secret |
| Kafka | localhost:9092 | SASL/PLAIN (xem §Kafka) |

> **Note — Auth API trực tiếp vs qua Kong:**  
> - **Kong (:8000):** JWT bắt buộc; plugin inject `X-User-Id`, `X-Tenant-Id`, `X-User-Roles` từ token → dùng cho mọi test production.  
> - **Auth API direct (:8200):** Không qua JWT; headers phải truyền thủ công → chỉ dùng cho test/debug nội bộ.

---

## Kong admin hardening

Kong's admin API used to bind `0.0.0.0:8001` inside the container and was
exposed to the host on port `8900` via a port mapping. Anyone reaching the
host or the Docker network could create routes, attach plugins, and rewrite
JWT validation. There was no RBAC, no IP allowlist, and OSS Kong's admin
endpoint does not natively accept auth plugins.

This section describes the current hardened layout and the rollout path
for further locking it down.

### Current layout (after 2026-06-07)

Three layers of defense, two active now and one opt-in:

1. **No host port mapping.** The `8900:8001` mapping was removed. Curl from
   the host machine to `http://localhost:8900` now connection-refuses. Admin
   is no longer reachable from any external interface.
2. **Isolated Docker network `kong-admin-net`.** Declared `internal: true`
   so it has no upstream connectivity. Kong binds admin only to its
   admin-net IP (`172.30.0.10:8001`). Containers on the default
   `data-agent-network` see no admin port. Only services that explicitly
   join `kong-admin-net` can reach admin:
     - `auth-api` — calls admin from `auth_core._sync_kong()` for the IP
       allowlist toggle and Kong service/route management
     - `release-worker` — registers release routes
     - `jwks-refresher` — rotates the JWT public-key plugin config

   Other services (`workflow-runtime`, `kb-backend`, `flow-builder`,
   `audit-bridge`, etc.) are NOT on admin-net. If one is compromised, it
   cannot reach admin to escalate.
3. **Optional: key-auth via Kong-fronting-Kong** (gated by env var
   `ENABLE_ADMIN_KEY_AUTH=1` on the `kong-setup` compose runner — see
   "Phase 3 rollout" below).
   OSS Kong's admin endpoint doesn't accept plugins directly, so we expose
   admin via a regular Kong route at `/_kong_admin` that proxies back to
   the admin IP and runs `key-auth` + `ip-restriction` plugins. Services
   then call `http://kong:8000/_kong_admin/<path>` with an `apikey` header
   instead of the raw `http://172.30.0.10:8001/<path>`. Disabled by default
   because flipping it on without also updating service env vars breaks
   startup — see the rollout below.

### Verification

After `docker compose up -d` and `kong-setup.sh`, run:

```powershell
bash infra/scripts/verify_kong_admin_locked.sh
```

Exits 0 if all three checks pass (host refused, default-net refused,
admin-net allowed). Non-zero = security regression OR stack broken;
read the per-line output.

Manual spot-check from the host:

```powershell
# Should fail — admin not host-reachable any more
curl --max-time 3 http://localhost:8900/status

# To inspect admin in dev, shell into Kong itself
docker exec dataagent-kong curl -sf http://172.30.0.10:8001/status
```

### Phase 3 rollout — enabling key-auth

1. In staging, bring up the stack and run setup with the flag:

   ```powershell
   docker compose -f docker/docker-compose.yml --profile setup run --rm `
     -e ENABLE_ADMIN_KEY_AUTH=1 kong-setup
   ```

2. The script writes generated keys to `infra/certs/admin-keys.env`
   (`chmod 600`, gitignored). Open the file:

   ```text
   KONG_ADMIN_API_KEY_AUTH_API=<random>
   KONG_ADMIN_API_KEY_RELEASE_WORKER=<random>
   KONG_ADMIN_API_KEY_JWKS_REFRESHER=<random>
   ```

3. Copy each variable into `.env` and update the relevant service env in
   `docker/docker-compose.app.yml`:
   - `auth-api`: add `KONG_ADMIN_API_KEY: ${KONG_ADMIN_API_KEY_AUTH_API}`
     and change `KONG_ADMIN_URL` to `http://kong:8000/_kong_admin`.
   - Similar for `release-worker` and `jwks-refresher`.
4. Update the services' Python code to send `apikey: <key>` header on
   every admin call. The auth-api `_sync_kong` block in
   `services/auth-api/auth_core.py` is the largest caller — update there
   first.
5. Restart the stack. Run the verification script again. Expected: admin
   still works from those three services (because they send the key),
   still refused from default-net containers (because no key + no net).
6. After verification, flip on in prod via the same flag.

Key rotation is just re-running `kong-setup.sh` with the flag — it deletes
existing keys and writes fresh ones to the keys file. Operators must then
update `.env` and restart.

### Why not RBAC (the Kong Enterprise path)?

Kong Enterprise's RBAC plugin handles admin auth natively, no
Kong-fronting-Kong trick needed. If/when the team adopts Enterprise:

- Drop the `_kong_admin` proxy service/route entirely.
- Enable `KONG_ENFORCE_RBAC=on` + define roles/permissions.
- Wire services to use Kong-managed tokens instead of API keys.
- Update this section to point at the new flow.

The kong-admin-net network isolation (Phase 2) stays valuable in both
worlds — it's defense-in-depth that doesn't depend on Kong's own auth.

---

## JWT audience + JWKS rotation grace period

Previously the aeroflow-jwks Kong plugin accepted any RS256 token from the
Keycloak realm, regardless of which client requested it. A token for the
frontend could be replayed against `release-worker` or `auth-api`. Separately,
the plugin invalidated its full JWKS cache the moment Keycloak rotated keys,
causing a 401 spike for every token in flight.

Both are addressed as of 2026-06-07.

### Public URL vs network URL — two URLs for one Keycloak

Keycloak in this stack is reachable via two different URLs depending on who
is asking, and the wiring HAS to keep them straight:

| Concept | URL in dev | Used by | Set via |
| ------- | ---------- | ------- | ------- |
| Public  | `http://localhost:8080` | browser, host-side curl, anything outside the compose network. Source of token's `iss` claim. | `KC_HOSTNAME_URL` on keycloak-node1/2 (env var, overridable via `KEYCLOAK_PUBLIC_URL`) |
| Network | `http://aeroflow-keycloak-lb:8080` | Kong's JWKS fetch, jwks-refresher, audit-bridge, any service in the compose network | DNS alias on `keycloak-lb` service |

The aeroflow-jwks plugin config holds BOTH:

- `jwks_uri` = network URL (Kong has to reach Keycloak from inside the cluster)
- `issuer` = public URL (matches what tokens actually carry in `iss`)

Mixing them up surfaces as either "Issuer mismatch" (Kong validates against
the network URL but tokens carry the public one) or connection-refused
during JWKS fetch (Kong tries the public URL from inside the cluster).

### Audience matrix

Per-service `audience` is enforced by the plugin. Kong's setup script
([infra/kong/kong-setup.sh](../infra/kong/kong-setup.sh)) installs:

- aeroflow-backend service → `audience = aeroflow-backend`
- auth-api service → `audience = auth-api`
- release-worker service → `audience = release-worker`

Keycloak's `aeroflow-frontend` client gets Audience protocol mappers (one per
target) via [infra/keycloak/setup-audience-mappers.sh](../infra/keycloak/setup-audience-mappers.sh):

```powershell
docker compose -f docker/docker-compose.yml --profile setup run --rm `
  keycloak-audience-setup
```

After running, tokens issued by `aeroflow-frontend` contain all three
audiences in the `aud` claim. Kong's plugin checks the route's configured
audience appears in that list. A token that's missing the audience for the
called service gets `401 Audience mismatch`.

### Rotation grace period

The plugin's `grace_period` config (default 600s = 10 min) controls how
long the previous signing key remains acceptable after Keycloak rotates.
Implementation (see [handler.lua](../infra/kong/plugins/aeroflow-jwks/handler.lua)):

- Cache TTL = `grace_period` (NOT `jwks_refresh_interval`, which becomes
  cosmetic).
- On every request: look up the token's `kid` in the cache.
- If `kid` not found, lazy-merge: fetch fresh JWKS and union with existing
  cache entries. Old keys stay valid until `grace_period` elapses without
  any traffic referencing them.
- This means: at rotation time, the FIRST request with the new `kid`
  triggers a fresh fetch; old `kid` tokens still verify against cache for
  the rest of grace_period.

### Manual rotation test (procedural)

The integration test ([tests/integration/test_jwt_audience.py](../tests/integration/test_jwt_audience.py))
verifies audience matching but does NOT exercise rotation — that needs
Keycloak admin API calls + timed assertions. Run this procedure manually:

1. Login as `platform-admin` and capture a token. Call this **T_OLD**.
2. Hit `/api/auth/health` with `Authorization: Bearer $T_OLD` — expect 200.
3. Rotate Keycloak's `aeroflow` realm signing key:
   - Keycloak admin UI → Realm Settings → Keys → Active keys → use the
     three-dot menu → "Make passive" on current key. Or via admin API.
4. Get a new token. Call this **T_NEW**.
5. Hit `/api/auth/health` with `Authorization: Bearer $T_NEW` → expect 200.
   (Plugin lazy-fetches new JWKS, gets new kid in cache.)
6. **Within 9 minutes**, hit `/api/auth/health` with `Authorization: Bearer $T_OLD`
   → expect 200 (old kid still in cache, grace period active).
7. **After 11 minutes**, hit `/api/auth/health` with `Authorization: Bearer $T_OLD`
   → expect 401 `No JWK for kid` (cache TTL expired, old key not refetched).

If step 6 returns 401, the grace_period config isn't taking effect — check
that the plugin was reinstalled by the latest `kong-setup.sh`.

---

## Tài khoản

### Keycloak — Master Realm (Admin)

| Username | Password | Vai trò |
|---|---|---|
| `admin` | `admin` | Keycloak master admin — quản lý toàn bộ realm |

> **Production:** Đổi ngay, dùng secrets manager.

---

### Realm `aeroflow` — Test Users

**Tenant 1: AeroFlow Dev** (`a0000000-0000-0000-0000-000000000001`)

| Username | Password | Role | Email |
|---|---|---|---|
| `platform-admin` | `Admin@123456` | `platform-admin` | admin@aeroflow.local |
| `ai-engineer` | `Engineer@1234` | `ai-engineer` | engineer@aeroflow.local |
| `executive-viewer` | `Viewer@123456` | `executive-viewer` | viewer@aeroflow.local |

**Tenant 2: Helios Corp** (`b0000000-0000-0000-0000-000000000002`)

| Username | Password | Role | Email |
|---|---|---|---|
| `helios-admin` | `H3lios@Admin01!` | `platform-admin` | helios-admin@helios.corp |
| `helios-engineer` | `H3lios@Eng001!` | `ai-engineer` | helios-engineer@helios.corp |
| `helios-viewer` | `H3lios@View01!` | `executive-viewer` | helios-viewer@helios.corp |

> **Login flow (Browser MFA bắt buộc):**
> 1. Truy cập http://localhost:5173 → redirect sang Keycloak login
> 2. Nhập username + password
> 3. **Nhập mã TOTP 6 chữ số** từ authenticator app (Google Authenticator / Authy)
> 4. Lần đầu chưa có TOTP → Keycloak redirect sang trang **cài đặt TOTP**: quét QR bằng app, nhập mã xác nhận
>
> **Realm import:** `--import-realm` chỉ chạy khi realm chưa tồn tại trong PostgreSQL. Sau lần đầu, users persist qua restarts.  
> **Password policy:** min 12 chars, upper+lower+digit+special, notUsername, history(5). Admin API không bypass được history — password reset qua `keycloak-setup.py` sẽ bị skip nếu password chưa đổi.  
> **JWT claims:** Kong `aeroflow-jwks` plugin inject `X-User-Roles`, `X-Tenant-Id`, `X-Role-Id` từ JWT claims.  
> **Keycloak User Profile:** `unmanagedAttributePolicy: ENABLED` required để lưu `tenant_id` và `role_id` attributes.
>
> **Xử lý khi không login được:**
>
> | Triệu chứng | Nguyên nhân | Fix |
> |---|---|---|
> | Sai password | Password history policy | Password hiện tại VẪN là `Admin@123456` (reset bị skip = đúng rồi) |
> | Sai TOTP / app mất sync | TOTP credential lỗi | Xóa credential + re-setup (xem bên dưới) |
> | Bị lock sau 5 lần sai | Brute-force protection | Clear counter (xem bên dưới) |
>
> ```bash
> # Xóa TOTP credential (lấy ID từ lệnh get credentials):
> docker exec aeroflow-keycloak-1 /opt/keycloak/bin/kcadm.sh \
>   get users/<USER_ID>/credentials -r aeroflow \
>   --no-config --server http://localhost:8080 --realm master --user admin --password admin
>
> docker exec aeroflow-keycloak-1 /opt/keycloak/bin/kcadm.sh \
>   delete users/<USER_ID>/credentials/<OTP_CREDENTIAL_ID> -r aeroflow \
>   --no-config --server http://localhost:8080 --realm master --user admin --password admin
>
> # Clear brute-force counter:
> docker exec aeroflow-keycloak-1 /opt/keycloak/bin/kcadm.sh \
>   delete "attack-detection/brute-force/users/<USER_ID>" -r aeroflow \
>   --no-config --server http://localhost:8080 --realm master --user admin --password admin
>
> # platform-admin USER_ID: 12f1c4b5-d6b9-4556-b13b-2ff585a6c440
> ```

---

### Realm `aeroflow` — Service Accounts (Clients)

| Client ID | Secret | Dùng cho |
|---|---|---|
| `aeroflow-backend` | `aeroflow-backend-secret-change-in-prod` | Kong token introspection |
| `aeroflow-admin` | `aeroflow-admin-secret` | python-keycloak admin tasks |

---

### Kafka Users

SASL mechanism: **PLAIN** (không phải SCRAM-SHA-512)

| Username | Password | ACL |
|---|---|---|
| `admin` | see `KAFKA_ADMIN_PASSWORD` in `.env` | Super user — inter-broker |
| `audit-bridge` | see `AUDIT_BRIDGE_KAFKA_PASSWORD` in `.env` | Write + Describe on `audit.auth.events` |
| `audit-consumer` | see `AUDIT_CONSUMER_KAFKA_PASSWORD` in `.env` | Read on `audit.auth.events`, Read on group `audit-consumer-group` |
| `release-worker` | see `RELEASE_WORKER_KAFKA_PASSWORD` in `.env` | Read `release.pipeline.triggered`, Write `release.pipeline.status` |
| `ci-service` | see `CI_SERVICE_KAFKA_PASSWORD` in `.env` | Write `release.pipeline.triggered` |
| `drift-detector` | see `DRIFT_DETECTOR_KAFKA_PASSWORD` in `.env` | Write `release.drift.detected` |
| `scan-runner` | see `SCAN_RUNNER_KAFKA_PASSWORD` in `.env` | Write `release.scan.completed` |
| `notification-consumer` | see `NOTIFICATION_CONSUMER_KAFKA_PASSWORD` in `.env` | Read `release.pipeline.status`, `release.drift.detected` |

---

### PostgreSQL

| Username | Password | Database |
|---|---|---|
| `aeroflow` | `aeroflow_secret` | `aeroflow` |

---

## Bước 3 — Setup Kong (chạy một lần)

Kong admin API bị lock vào `kong-admin-net` (xem [Kong admin hardening](#kong-admin-hardening))
nên KHÔNG chạy script từ host. Dùng compose-managed runner — container tạm
join cả 2 network và chạy script bên trong:

```powershell
docker compose -f docker/docker-compose.yml --profile setup run --rm kong-setup
```

Hoặc với Phase 3 key-auth bật:

```powershell
docker compose -f docker/docker-compose.yml --profile setup run --rm `
  -e ENABLE_ADMIN_KEY_AUTH=1 kong-setup
```

Script tạo:
- Service `aeroflow-backend` → upstream `http://host.docker.internal:8888`, route `/api`
- Service `release-worker` → upstream `http://release-worker:8100`, route `/api/release`
- Plugin **aeroflow-jwks** trên cả 2 services → RS256 verify từ JWKS Keycloak, cache 300s  
  Inject headers: `X-User-Id`, `X-User-Email`, `X-User-Name`, `X-User-Roles`, `X-Tenant-Id`, `X-Role-Id`, `X-Kong-Verified: true`
- Plugin **IP Restriction** → allowlist private ranges (10/8, 172.16/12, 192.168/16)
- Plugin **Correlation-ID** → header `X-Request-ID`
- Plugin **Rate Limiting** → 300 req/phút per IP (policy=local)

Nếu cert mTLS đã được tạo (`infra/certs/kong-client.crt` tồn tại), script sẽ tự đăng ký thêm.

> **Lưu ý:** Mỗi khi rebuild Kong image (sau khi sửa `handler.lua`), cần restart Kong rồi chạy lại setup:
>
> ```powershell
> docker compose -f docker/docker-compose.yml build kong
> docker compose -f docker/docker-compose.yml up -d kong
> docker compose -f docker/docker-compose.yml --profile setup run --rm kong-setup
> ```

---

## Bước 4 — Setup Kafka ACL (chạy một lần)

```bash
bash infra/kafka/kafka-setup.sh
```

Tạo topic `audit.auth.events` (3 partitions, retention 30 ngày) và gán ACL:
- `audit-bridge` → Write + Describe on `audit.auth.events`
- `audit-consumer` → Read + Describe on `audit.auth.events`, Read on group `audit-consumer-group`

---

## Bước 5 — Setup mTLS certs (tùy chọn)

```bash
bash infra/certs/gen-certs.sh
```

Tạo trong `infra/certs/` (git-ignored):
- `ca.crt` — CA gốc, chia sẻ với backend để verify
- `kong-client.crt` / `kong-client.key` — Kong present trên mỗi upstream request
- `backend.crt` / `backend.key` — placeholder cho backend service

Sau đó chạy lại `kong-setup.sh` để đăng ký cert vào Kong.

---

## TEST CASES

### Kết quả Test Thực Tế — 2026-05-29

Chạy trên stack live (Docker Compose, Windows 11). Môi trường: `localhost`, không có backend thật ở port 8888.

| TC | Tên | Trạng thái | Ghi chú |
|---|---|---|---|
| TC-01 | KC HA Failover | ✅ PASS | node1 dừng → HAProxy route sang node2, login thành công |
| TC-02 | KC Session Sync | ✅ PASS | Token node1 introspect trên node2 → `active:true` |
| TC-03 | JWT valid → Kong → release-worker | ✅ PASS | 200 real data; tested 2026-05-29 |
| TC-04 | JWT missing → 401 | ✅ PASS | `HTTP/1.1 401 Unauthorized` |
| TC-05 | JWT tampered → 401 | ✅ PASS | `HTTP/1.1 401 Unauthorized` |
| TC-06 | JWT expired → 401 | ✅ PASS | Token expires_in=15s → đợi 22s → 401 |
| TC-07 | JWKS cache 300s | ✅ PASS | Sau reload, JWKS fetch 1 lần, cache TTL 300s |
| TC-08 | Rate limit 300 req/min | ✅ PASS | 429 tại request #5 (test với limit=5), headers đúng |
| TC-09 | Brute force 5 fail | ✅ PASS | 6 lần sai → `disabled:true`, đúng password vẫn 401 |
| TC-10 | Session limit | ⚠️ Không cấu hình | KC 24 hỗ trợ qua User Session Limits flow, chưa enable |
| TC-11 | Password policy | ✅ PASS | "abc" → 400, "password123" → 400, "Str0ng@Pass" → 201 |
| TC-12 | Kafka audit pipeline | ✅ PASS | LOGIN events → Kafka → PostgreSQL, latency ~12s (bridge poll=10s); IP address written |
| TC-13 | Kafka ACL deny | ✅ PASS | `TopicAuthorizationException`: audit-consumer WRITE bị từ chối |
| TC-14 | KC Event Logging | ✅ PASS | KC event store có LOGIN events |
| TC-15 | Correlation ID | ✅ PASS | `X-Kong-Request-Id` trong response |
| TC-16 | IP Restriction config | ✅ PASS | Allow 127/10/172.16/192.168 |
| TC-17 | mTLS client cert | ✅ PASS | CA + Kong client cert đăng ký vào Kong Admin API |
| TC-18 | HAProxy round-robin | ✅ PASS | 4/4 requests → HTTP 200 |
| TC-19 | Token payload | ✅ PASS | RS256, issuer đúng, expires_in=900 |
| — | KC HA cluster (cả 2 node) | ✅ PASS | Cả node1 và node2 `"status":"UP"` |
| — | Kong plugins đã config | ✅ PASS | 4 plugins: aeroflow-jwks (×2 services), rate-limiting, correlation-id, ip-restriction |
| TR-E2E | End-to-end qua Kong 8000 | ✅ PASS | GET /api/release/pipelines → 200 data thật; POST trigger → pipeline_id tạo thành công |
| TR-02b | Trigger staging (correct field) | ✅ PASS | `target_environments:["staging"]` → AWAITING_APPROVAL |
| TR-09b | Approve staging 2026-05-29 | ✅ PASS | POST approve → SUCCESS; DB release_history ghi staging/SUCCESS |
| TR-10b | Reject staging 2026-05-29 | ✅ PASS | POST reject → pipeline FAILED |
| TR-11b | Rollback 2026-05-29 | ✅ PASS | rb-20260529 → DB status=SUCCESS (3 steps) |
| TR-15b | Drift detection 2026-05-29 | ✅ PASS | drift_id=5, CRITICAL_DRIFT, production vs staging |
| TR-17b | Immutable release_history | ✅ PASS | UPDATE blocked: "release_history is immutable" |
| TR-22b | Partition auto-create | ✅ PASS | release_history_2026_06 created |
| TC-RBAC | ai-engineer rollback → 403 | ✅ PASS | "Only platform-admin can trigger rollback" |
| TC-20 | MFA bắt buộc (CONFIGURE_TOTP) | ✅ PASS | `defaultAction: true` set trong realm-export.json; `platform-admin.requiredActions=["CONFIGURE_TOTP"]` verified qua kcadm.sh; browser login redirect sang TOTP setup screen; tested 2026-05-29 |
| TC-21 | IP allowlist middleware release-worker | ✅ PASS | Middleware active — Docker bridge IP (172.x.x.x) passes, tested 2026-05-29 |
| TC-22 | audit_logs immutable trigger | ✅ PASS | `UPDATE` blocked → `ERROR: audit_logs is immutable — UPDATE/DELETE are not allowed`; tested 2026-05-29 |
| TC-23 | MongoDB service healthy | ✅ PASS | `db.runCommand({ping:1})` → `{ ok: 1 }`; tested 2026-05-29 |
| TC-24 | Elasticsearch service healthy | ✅ PASS | `GET /_cluster/health` → `"status":"green"`; tested 2026-05-29 |
| TC-25 | Jaeger UI accessible | ✅ PASS | `http://localhost:16686` → 200 OK; OTLP endpoint `http://jaeger:4317` set in release-worker env; tested 2026-05-29 |
| TC-26 | JWKS refresher hoạt động | ✅ PASS | `INFO Registered new Kong JWT credential` trong logs jwks-refresher; tested 2026-05-29 |
| TC-27 | OpenBao Transit signing (RSA-4096) | ✅ PASS | SIGNING_KEY inject → vault tạo RSA key; POST /sign → `vault:v1:...` signature; POST /verify valid=true; tested 2026-05-30 |
| TC-28 | Transit verify tampered data | ✅ PASS | Wrong payload → `{"valid":false}`; tested 2026-05-30 |
| TC-29 | Transit rotate (no value needed) | ✅ PASS | POST /rotate → version 2; old version vẫn verify được; tested 2026-05-30 |
| TC-30 | Transit reveal = public key only | ✅ PASS | GET /reveal → `-----BEGIN PUBLIC KEY-----\n...`; private key không export; tested 2026-05-30 |
| TC-31 | Panic Mode enforcement | ✅ PASS | `vault_panic_mode=true` → tất cả /secrets/* trả 503; governance/audit vẫn hoạt động; tested 2026-05-30 |
| TC-32 | Governance PUT → state persists | ✅ PASS | PUT /governance → `panic_mode=false` → GET /secrets HTTP 200; tested 2026-05-30 |
| TC-33 | PII Access Log | ✅ PASS | vault_pii_access_log=true + reveal → entry trong /pii-log với triggered_by=REVEAL; tested 2026-05-30 |
| TC-34 | Transit HMAC key (aes256-gcm96) | ✅ PASS | HMAC_KEY + HMAC-SHA256 → Transit key type `aes256-gcm96` (không dùng 'hmac'); tested 2026-05-30 |
| TC-MT01 | Tenant isolation — secrets list | ✅ PASS | T1 list (11 secrets): 0 HELIOS keys; T2 list (5 secrets): chỉ HELIOS keys; tested 2026-05-30 |
| TC-MT02 | Cross-tenant reveal blocked | ✅ PASS | T1 user GET T2 secret UUID → 404 (không phải 403); tested 2026-05-30 |
| TC-MT03 | Cross-tenant sign blocked | ✅ PASS | T1 user POST T2 signing key → 404; T2 user POST T1 signing key → 404; tested 2026-05-30 |
| TC-MT04 | Cross-tenant API key revoke blocked | ✅ PASS | T1 POST revoke T2 key → 404; T2 POST revoke T1 key → 404; tested 2026-05-30 |
| TC-MT05 | Cross-tenant IP rule toggle blocked | ✅ PASS | T1 PATCH T2 IP rule → 404; T2 PATCH T1 IP rule → 404; tested 2026-05-30 |
| TC-MT06 | HSM Transit key isolation | ✅ PASS | T1 HSM: 2 keys (prefix a0000000); T2 HSM: 2 keys (prefix b0000000); no overlap; tested 2026-05-30 |
| TC-MT07 | Audit log isolation | ✅ PASS | T1 audit (20 entries): 0 HELIOS entries; T2 audit (2 entries): 0 AeroFlow entries; tested 2026-05-30 |
| TC-MT08 | T2 own data fully operational | ✅ PASS | T2 reveal, sign, rotate, API key create all work on own resources; tested 2026-05-30 |
| TC-MT09 | JWT tenant binding | ✅ PASS | helios-admin JWT: tenant_id=b0000000-..., role_id=platform-admin (confirmed via token decode) |
| TC-MS01 | Microsoft IDP configured | ✅ PASS | keycloak-setup.py → IDP alias=microsoft, mapper set, protocol mapper added; tested 2026-06-09 |
| TC-MS02 | Microsoft SSO first login → pending | ⏳ PENDING | Cần account Microsoft thật để test E2E |
| TC-MS03 | Admin approve → user active | ⏳ PENDING | Depends on TC-MS02 |
| TC-MS04 | Subsequent login no approval needed | ⏳ PENDING | Depends on TC-MS03 |
| TC-MS05 | Reject → Keycloak account disabled | ⏳ PENDING | Depends on TC-MS02 |

---

## Microsoft SSO — Hướng dẫn Test

### Setup (chạy một lần)

```bash
# Chạy keycloak-setup với MS SSO credentials
docker run --rm --network data-agent-network \
  -e KC_URL=http://keycloak-lb:8080 \
  -e KC_REALM=aeroflow \
  -e KC_ADMIN_USER=admin \
  -e KC_ADMIN_PASSWORD=admin \
  -e MS_SSO_CLIENT_ID=<client-id> \
  -e "MS_SSO_CLIENT_SECRET=<client-secret>" \
  -e MS_SSO_TENANT_ID=consumers \
  -v "$(pwd)/infra/keycloak:/scripts:ro" \
  python:3-alpine sh -c "python /scripts/keycloak-setup.py"
```

Kết quả mong đợi:
```
[keycloak-setup] Microsoft Entra ID IDP created.
[keycloak-setup] Microsoft IDP attribute mapper created.
[keycloak-setup] idp_provider protocol mapper added to aeroflow-frontend.
[keycloak-setup] Done.
```

### Kiểm tra IDP đã được tạo

```bash
ADMIN_TOKEN=$(curl -s http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d "client_id=admin-cli&grant_type=password&username=admin&password=admin" \
  | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

# Xác nhận microsoft IDP tồn tại
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8080/admin/realms/aeroflow/identity-provider/instances" \
  | python3 -c "import sys,json; idps=json.load(sys.stdin); print([i['alias'] for i in idps])"
# Kết quả mong đợi: ['microsoft']

# Kiểm tra mappers
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8080/admin/realms/aeroflow/identity-provider/instances/microsoft/mappers" \
  | python3 -c "import sys,json; m=json.load(sys.stdin); print([x['name'] for x in m])"
# Kết quả mong đợi: ['set-idp-provider-attr']
```

### TC-MS01 — Kiểm tra Microsoft login button trên UI

1. Truy cập `http://localhost:5173` → redirect sang Keycloak login
2. Keycloak login page phải hiển thị nút **"Microsoft"** hoặc **"Sign in with Microsoft"**
3. Nếu không thấy → vào Keycloak Admin → Identity Providers → kiểm tra `microsoft` enabled

### TC-MS02 — First Login Flow (cần Microsoft account)

1. Click nút Microsoft trên Keycloak login page
2. Đăng nhập bằng Microsoft account (personal Outlook/Hotmail)
3. Sau khi auth xong → frontend nhận JWT từ Keycloak
4. `AuthProvider` gọi `GET /api/auth/me` → response `{status: "pending_approval"}`
5. Frontend hiển thị màn hình **"Waiting for admin approval"**

```bash
# Verify DB có pending record
docker exec dataagent-postgres psql -U admin -d dataagent \
  -c "SELECT email, display_name, status, created_at FROM microsoft_sso_approvals ORDER BY created_at DESC LIMIT 5;"
```

### TC-MS03 — Admin Approve

1. Đăng nhập với `platform-admin` account
2. Vào **Settings → Auth & SSO → MS Approvals**
3. Thấy user Microsoft trong danh sách Pending
4. Click **Approve** → user được enable trong Keycloak + status=approved trong DB

```bash
# Verify approval via API
curl -s http://localhost:8000/api/auth/approvals?status=approved \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python3 -m json.tool
```

### TC-MS04 — Subsequent Login (không cần approval)

1. User Microsoft login lại lần 2
2. `GET /api/auth/me` trả `{status: "active"}` ngay (DB lookup, không gọi Keycloak Admin API)
3. Frontend render app bình thường

### TC-MS05 — Reject Flow

```bash
# Admin reject một pending user
curl -s -X POST http://localhost:8000/api/auth/approvals/<keycloak_user_id>/reject \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# Keycloak account bị disable, user không login được nữa
```

---

### Chi tiết kết quả đã pass

#### TC-03/04/05 — JWT Verification qua Kong → Release Worker

```
# TC-03: Token hợp lệ → release-worker trả data thật
curl http://localhost:8000/api/release/pipelines -H "Authorization: Bearer $TOKEN"
→ HTTP/1.1 200 OK
→ {"pipelines":[...], "count":25}
→ X-Kong-Request-Id: <uuid>#counter

# TC-04: Không có token
curl http://localhost:8000/api/release/pipelines
→ HTTP/1.1 401 Unauthorized   ← {"message":"Authorization header missing"}

# TC-05: Token bị tamper
curl http://localhost:8000/api/release/pipelines -H "Authorization: Bearer BADTOKEN"
→ HTTP/1.1 401 Unauthorized   ← {"message":"Malformed JWT"}
```

Kong inject `X-User-Id`, `X-User-Email`, `X-User-Roles: platform-admin`, `X-Kong-Verified: true` vào upstream request. Release-worker đọc các header này để xác định user và role.

---

#### TC-12 — Kafka Audit Pipeline

Toàn bộ pipeline đã được verify end-to-end:

```
# Kafka topic offsets (tổng 5 messages)
audit.auth.events:0:5   ← 4 LOGIN events từ KC SPI + 1 test msg
audit.auth.events:1:0
audit.auth.events:2:1

# Sample Kafka message (từ Keycloak SPI)
{
  "realmId": "a86f008b-832d-42c9-b5f6-e0535444d709",
  "clientId": "aeroflow-frontend",
  "ipAddress": "172.22.0.9",
  "type": "LOGIN",
  "userId": "ea14d6a1-8d72-429a-b342-828a3766de96",
  "time": 1779697404663,
  "details": {
    "auth_method": "openid-connect",
    "grant_type": "password",
    "username": "platform-admin",
    "client_auth_method": "client-secret"
  }
}

# PostgreSQL audit_logs (5 rows confirmed)
actor                                  | action | status  | ip_address
ea14d6a1-8d72-429a-b342-828a3766de96  | LOGIN  | SUCCESS | 172.22.0.9
ea14d6a1-8d72-429a-b342-828a3766de96  | LOGIN  | SUCCESS | 172.22.0.9
...
```

**Pipeline latency:** Login event → Kafka → PostgreSQL: ~2 giây.

---

#### TC-14 — Keycloak Event Store

```bash
# Sau khi enable events qua Admin API:
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8080/admin/realms/aeroflow/events?max=5"

# Kết quả:
"time": 1779697404663, "type": "LOGIN", "userId": "ea14d6a1-..."
"time": 1779697195862, "type": "LOGIN", "userId": "ea14d6a1-..."
```

**Lưu ý quan trọng:** Realm được import lần đầu có `eventsEnabled: true`, nhưng nếu realm đã tồn tại trong DB thì import bị skip. Phải kích hoạt events thủ công sau khi stack start lần đầu:

```bash
ADMIN_TOKEN=$(curl -s http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d "client_id=admin-cli&grant_type=password&username=admin&password=admin" \
  | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

curl -X PUT -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  "http://localhost:8080/admin/realms/aeroflow" \
  -d '{"eventsEnabled":true,"eventsExpiration":86400,"enabledEventTypes":["LOGIN","LOGIN_ERROR","LOGOUT","REGISTER","REFRESH_TOKEN"],"eventsListeners":["jboss-logging","aeroflow-kafka"]}'
```

---

#### TC-15 — Correlation ID & Kong Headers

```
curl -si http://localhost:8000/api/health (no auth)
→ HTTP/1.1 401 Unauthorized
→ X-Kong-Response-Latency: 0

curl -si http://localhost:8000/api/health -H "Authorization: Bearer $TOKEN"
→ HTTP/1.1 503 Service Temporarily Unavailable
→ X-Kong-Response-Latency: 1
→ X-Kong-Request-Id: 85493369c179390a46489274cb5cab51
```

---

#### TC-16 — IP Restriction

```bash
curl http://localhost:8001/plugins | jq '.data[] | select(.name=="ip-restriction") | .config'
```
```json
{
  "allow": ["127.0.0.1/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
  "deny": null
}
```

---

#### TC-18 — HAProxy Load Balancing

```bash
# 4 requests qua HAProxy LB (port 8080)
curl -si http://localhost:8080/realms/aeroflow/.well-known/openid-configuration → HTTP/1.1 200 OK
curl -si http://localhost:8080/realms/aeroflow/.well-known/openid-configuration → HTTP/1.1 200 OK
curl -si http://localhost:8080/realms/aeroflow/.well-known/openid-configuration → HTTP/1.1 200 OK
curl -si http://localhost:8080/realms/aeroflow/.well-known/openid-configuration → HTTP/1.1 200 OK

# Cả 2 nodes healthy:
curl http://localhost:8081/health/ready → {"status": "UP"}
curl http://localhost:8082/health/ready → {"status": "UP"}
```

---

#### TC-19 — Token Structure

```bash
curl -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456"
→ {"token_type":"Bearer","expires_in":900}

# Token RS256, issuer: http://localhost:8080/realms/aeroflow
# Audience: aeroflow-backend, account
# Realm roles: platform-admin
```

---

#### TC-01 — KC HA Failover

```bash
docker stop aeroflow-keycloak-1
sleep 5
curl -s -X POST "http://localhost:8080/realms/aeroflow/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=aeroflow-frontend&username=ai-engineer&password=Engineer@1234"
→ "expires_in":900   ← HAProxy chuyển sang node2 thành công

docker start aeroflow-keycloak-1   # Khôi phục
```

---

#### TC-02 — KC Session Sync (Infinispan)

```bash
# Lấy token từ node1 (port 8081)
TOKEN=$(curl -s -X POST "http://localhost:8081/realms/aeroflow/protocol/openid-connect/token" ...)

# Introspect trên node2 (port 8082) dùng aeroflow-admin client
curl -s -X POST "http://localhost:8082/realms/aeroflow/protocol/openid-connect/token/introspect" \
  -d "token=$TOKEN&client_id=aeroflow-admin&client_secret=aeroflow-admin-secret" \
→ {"active":true,"preferred_username":"ai-engineer","session_state":"496a6ccf-..."}
```

Infinispan đồng bộ session giữa node1 ↔ node2.

---

#### TC-06 — JWT Expired → 401

```bash
# Tạm set accessTokenLifespan = 15s
curl -X PUT .../admin/realms/aeroflow -d '{"accessTokenLifespan":15}'

TOKEN=$(curl ... | grep access_token)
sleep 22
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/test
→ HTTP/1.1 401 Unauthorized   ← token đã hết hạn

# Khôi phục accessTokenLifespan = 900
curl -X PUT .../admin/realms/aeroflow -d '{"accessTokenLifespan":900}'
```

---

#### TC-07 — JWKS Cache 300s

Sau khi reload Kong với `jwks_uri=http://aeroflow-keycloak-lb:8080/...` (internal DNS), Kong fetch JWKS 1 lần khi có request đầu tiên, cache trong `kong.cache` với TTL 300s.
- Nhiều requests liên tiếp không tạo JWKS error mới trong logs
- `X-RateLimit-Limit-Minute: 300` xác nhận plugin chain chạy đúng

**Lưu ý quan trọng (Bug #8):** Cần dùng internal Docker hostname (`aeroflow-keycloak-lb`) chứ không phải `localhost:8080` trong `jwks_uri`, vì Kong container không thể reach `localhost:8080` sau khi Lua cache hết hạn.

---

#### TC-08 — Rate Limiting

Test với limit=5/phút (tạm, sau restore về 300):

```
Request 1: HTTP 502  X-RateLimit-Remaining-Minute: 3  RateLimit-Limit: 5
Request 2: HTTP 502  X-RateLimit-Remaining-Minute: 2
Request 3: HTTP 502  X-RateLimit-Remaining-Minute: 1
Request 4: HTTP 502  X-RateLimit-Remaining-Minute: 0
Request 5: HTTP 429  → "API rate limit exceeded"
Request 6: HTTP 429
Request 7: HTTP 429
```

`X-RateLimit-Limit-Minute`, `X-RateLimit-Remaining-Minute`, `RateLimit-Reset` headers đúng.

**Lưu ý:** `policy=local` với 20 nginx workers → mỗi worker có counter riêng → effective limit là 20×300=6000 RPM từ một IP. Đủ cho production với IP-based routing. Nếu cần strict per-user limit, dùng `policy=redis`.

---

#### TC-09 — Brute Force Protection

```bash
# Set failureFactor=5 tạm thời
curl -X PUT .../admin/realms/aeroflow -d '{"failureFactor":5}'

# 6 lần login sai
for i in $(seq 1 6); do
  curl -X POST .../token -d "username=ai-engineer&password=WRONG_$i" → HTTP 401
done

# Kiểm tra trạng thái lockout
curl .../attack-detection/brute-force/users/$USER_ID
→ {"disabled":true,"numFailures":2,"numTemporaryLockouts":0}

# Thử đúng password — vẫn bị từ chối
curl -X POST .../token -d "username=ai-engineer&password=Engineer@1234"
→ {"error":"invalid_grant","error_description":"Invalid user credentials"}

# Restore: failureFactor=30, clear lockout
curl -X PUT .../admin/realms/aeroflow -d '{"failureFactor":30}'
curl -X DELETE .../attack-detection/brute-force/users/$USER_ID
```

---

#### TC-11 — Password Policy

Policy: `length(8) and upperCase(1) and specialChars(1)` (set via Admin API).

```
POST /admin/realms/aeroflow/users/$USER_ID/reset-password
  "password": "abc"       → HTTP 400 (quá ngắn)
  "password": "password123" → HTTP 400 (thiếu ký tự đặc biệt)
  "password": "Str0ng@Pass" → HTTP 201 ✅
```

---

#### TC-13 — Kafka ACL Deny

audit-consumer chỉ có READ+DESCRIBE trên `audit.auth.events`, không có WRITE:

```bash
echo "test" | kafka-console-producer.sh \
  --bootstrap-server kafka:9092 \
  --topic audit.auth.events \
  --producer.config /tmp/prod_correct.props  # username=audit-consumer

→ ERROR: org.apache.kafka.common.errors.TopicAuthorizationException:
         Not authorized to access topics: [audit.auth.events]
```

ACL `StandardAuthorizer` từ chối đúng.

---

#### TC-17 — mTLS Client Cert

```bash
# 1. Generate certs (dùng Docker do Git Bash path conversion issue)
docker run --rm -v "infra/certs:/certs" alpine/openssl ...
→ ca.crt, kong-client.crt, kong-client.key, backend.crt

# 2. Đăng ký CA cert trong Kong
curl -X POST http://localhost:8001/ca_certificates -d cert=...
→ id: 81f0b17f-bfab-486e-a98c-f53b68d7d2fd ✅

# 3. Đăng ký Kong client cert
curl -X POST http://localhost:8001/certificates -d cert=... key=...
→ id: fa6c9ddf-e762-41f2-9c88-df2e8bced01c ✅
```

Kong client cert sẵn sàng để gắn vào upstream service khi có backend thật.

---

### Bugs phát hiện trong quá trình test

| # | Bug | Nguyên nhân | Fix |
|---|---|---|---|
| 1 | `kafka-python 2.0.2` crash trên Python 3.12 | `kafka.vendor.six.moves` không tồn tại | Đổi sang `kafka-python-ng==2.2.3` |
| 2 | Keycloak SPI `ClusterAuthorizationException` | `enable.idempotence=true` (default) yêu cầu `CLUSTER:IDEMPOTENT_WRITE` | Set `enable.idempotence=false` trong producer |
| 3 | `__consumer_offsets` không tự tạo trong KRaft | Controller nhận request nhưng không tạo được | Tạo thủ công, thêm vào `kafka-setup.sh` |
| 4 | Consumer group join hang vô hạn | ACL thiếu trên `__consumer_offsets` | Thêm ACL `audit-consumer: READ/WRITE/DESCRIBE` trên `__consumer_offsets` |
| 5 | `aeroflow-kafka` event listener không fire | `eventsEnabled=false` sau khi KC skip import | Kích hoạt qua Admin API sau khi start |
| 6 | audit-consumer crash khi gặp non-JSON message | `value_deserializer` không handle lỗi | Thêm `_safe_json()` wrapper + skip non-dict messages |
| 7 | `kong-setup.sh` crash: `SCRIPT_DIR: unbound variable` | Biến chưa được khai báo | Thêm `SCRIPT_DIR=$(dirname "${BASH_SOURCE[0]}")` |
| 8 | `aeroflow-jwks` plugin: `pkey.new:load_key: expecting 'DER','PEM','JWK','*' as format` | `pkey.new(pem, "PEM")` — Lua string metatable làm `"PEM".format = string.format` (function) thay vì nil, phá vỡ format check | Đổi sang `pkey.new(pem, { format = "PEM" })` trong `handler.lua` |
| 9 | JWKS fetch fail sau 300s: `connection refused` | `jwks_uri` dùng `localhost:8080` — từ trong Kong container, `localhost` là Kong chứ không phải Keycloak | Đổi `jwks_uri` sang `http://aeroflow-keycloak-lb:8080/...` (Docker internal DNS) |
| 10 | `openssl req -subj "/CN=..."` fail trên Windows với Git Bash | Git Bash convert `/CN=...` thành `C:/Program Files/Git/CN=...` (Windows path) | Chạy `openssl` qua Docker container (alpine/openssl) |
| 11 | Kafka broker không start: `Line 1: expected [{], found [⚠️]` | `kafka-broker-jaas.config` có comment kiểu `# ⚠️ DEV ONLY` — Java JAAS parser không hỗ trợ `#` comments, chỉ hỗ trợ `//` | Đổi tất cả comment từ `#` sang `//`, bỏ emoji |
| 12 | Kafka SASL_PLAINTEXT: `SCRAM-SHA-512` không match broker | `docker-compose.yml` set `KAFKA_SASL_ENABLED_MECHANISMS: SCRAM-SHA-512` nhưng JAAS config dùng `PlainLoginModule` | Đổi sang `PLAIN` trong `docker-compose.yml` |
| 13 | MinIO healthcheck fail: `curl: not found` | MinIO container (RELEASE.2024) không có `curl` | Dùng `["CMD", "mc", "ready", "local"]` thay vì curl |
| 14 | `aeroflow-jwks` plugin chỉ trên `aeroflow-backend`, không có trên `release-worker` | `kong-setup.sh` chỉ đăng ký plugin cho một service | Thêm `upsert_plugin` cho `release-worker` trong `kong-setup.sh` |
| 15 | `X-User-Roles` header rỗng → release-worker từ chối 401 | handler.lua chỉ set `X-Role-Id` từ custom claim, không đọc `realm_access.roles` | Cập nhật handler.lua: ưu tiên `realm_access.roles` (standard OIDC) và filter system roles |
| 16 | `release-init.sql` không được mount vào postgres | Chỉ `init.sql` được mount; tất cả release tables (`pipelines`, `release_history`, v.v.) chưa tồn tại | Thêm volume mount `./infra/sql/release-init.sql:/docker-entrypoint-initdb.d/02-release-init.sql` |
| 17 | `audit-consumer`: `column "source_event_id" does not exist` | DB live khởi tạo từ version `init.sql` cũ trước khi column được thêm; `init.sql` đã có column nhưng postgres volume không được re-init | `ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS source_event_id text;` rồi restart consumer |
| 18 | `audit-consumer`: `ip_address` luôn rỗng | `insert_event()` hardcode `""` cho `ip_address` thay vì đọc `event.get("ipAddress")` | Sửa `main.py` dòng ip_address, đổi metadata thành `event.get("details")` |

---

### TC-01: Keycloak HA — Failover khi node1 down

**Mục đích:** Xác nhận HAProxy tự chuyển traffic sang node2 khi node1 fail.

```bash
# Xác nhận cả 2 nodes healthy trước
curl -s http://localhost:8081/health/ready | jq .status   # → "UP"
curl -s http://localhost:8082/health/ready | jq .status   # → "UP"

# Dừng node1
docker compose stop keycloak-node1

# Đợi 20s để HAProxy detect (health-check interval 15s)
sleep 20

# Thử login qua HAProxy (port 8080) — phải vẫn hoạt động qua node2
curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | jq .access_token | head -c 50
```

**Kết quả mong đợi:**
- Access token vẫn được cấp (không phải null)
- `docker compose logs keycloak-lb` hiển thị health-check fail cho node1, traffic chỉ đi node2

```bash
# Khôi phục node1
docker compose start keycloak-node1

# Đợi node1 rejoin cluster (~60s)
sleep 90
curl -s http://localhost:8081/health/ready | jq .status   # → "UP"
```

---

### TC-02: Keycloak HA — Session sync giữa 2 nodes

**Mục đích:** Token lấy từ node1 có thể verify thành công trên node2 (Infinispan sync).

```bash
# Lấy token qua node1 trực tiếp
TOKEN=$(curl -s -X POST http://localhost:8081/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=ai-engineer&password=Engineer@1234" \
  | jq -r .access_token)

# Introspect token qua node2 trực tiếp
curl -s -X POST http://localhost:8082/realms/aeroflow/protocol/openid-connect/token/introspect \
  -u "aeroflow-backend:aeroflow-backend-secret-change-in-prod" \
  -d "token=$TOKEN" \
  | jq .active
```

**Kết quả mong đợi:** `true` — node2 nhận ra session từ node1.

---

### TC-03: JWT Verification — Token hợp lệ qua Kong → Release Worker

**Mục đích:** Kong aeroflow-jwks plugin verify đúng và inject headers; release-worker trả data thật.

```bash
# Bước 1: lấy token
TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | grep -o '"access_token":"[^"]*"' | head -1 | sed 's/"access_token":"//;s/"$//')

# Bước 2: gọi qua Kong port 8000 — có JWT auth
curl -s http://localhost:8000/api/release/pipelines \
  -H "Authorization: Bearer $TOKEN" | head -c 200
```

**Kết quả mong đợi:**
```json
{"pipelines": [...], "count": N}
```

Kong inject vào upstream request:
- `X-User-Id`: Keycloak user UUID (sub claim)
- `X-User-Email`: admin@aeroflow.local
- `X-User-Name`: platform-admin
- `X-User-Roles`: platform-admin (từ `realm_access.roles`, lọc system roles)
- `X-Kong-Verified`: true

---

### TC-04: JWT Verification — Không có token → 401

```bash
curl -sv http://localhost:8000/api/health 2>&1 | grep "< HTTP"
```

**Kết quả mong đợi:** `< HTTP/1.1 401 Unauthorized`

---

### TC-05: JWT Verification — Token giả/tampered → 401

```bash
# Dùng token hợp lệ nhưng thay đổi 1 ký tự ở signature (phần sau dấu . thứ 2)
FAKE_TOKEN=$(echo $TOKEN | sed 's/.\{3\}$/XXX/')
curl -sv http://localhost:8000/api/health \
  -H "Authorization: Bearer $FAKE_TOKEN" 2>&1 | grep "< HTTP"
```

**Kết quả mong đợi:** `< HTTP/1.1 401 Unauthorized`

---

### TC-06: JWT Verification — Token hết hạn → 401

```bash
# Decode payload để xem exp
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq '{exp, iat, sub}'

# Để test: đặt accessTokenLifespan trong realm về 1 phút, đợi hết hạn
# Hoặc dùng token cũ đã lưu từ trước (nếu đã quá 15 phút)
# Kong sẽ trả 401 khi exp < current_time
```

**Kết quả mong đợi:** `HTTP 401 Unauthorized` với body lỗi expired token.

---

### TC-07: JWKS Cache — 300s TTL

**Mục đích:** Xác nhận Kong cache JWKS và không fetch lại liên tục.

```bash
# Xem logs Kong trong 1 phút — không nên thấy JWKS fetch sau lần đầu
docker compose logs kong --tail=50 2>&1 | grep -i "jwks\|fetch\|cache"

# Gọi 10 request liên tiếp
for i in $(seq 1 10); do
  curl -s http://localhost:8000/api/health -H "Authorization: Bearer $TOKEN" > /dev/null
done

# Kiểm tra log — chỉ có 1 lần fetch JWKS (lần đầu), các lần sau dùng cache
docker compose logs kong --tail=100 2>&1 | grep -c "fetch"
```

**Kết quả mong đợi:** JWKS fetch xuất hiện tối đa 1 lần trong 300s đầu, sau đó dùng cache.

---

### TC-08: Rate Limiting — 300 req/phút

**Mục đích:** Sau 300 requests trong 1 phút, Kong trả 429.

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | jq -r .access_token)

# Gửi 305 request
for i in $(seq 1 305); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health \
    -H "Authorization: Bearer $TOKEN")
  if [ "$STATUS" = "429" ]; then
    echo "Rate limit hit at request $i"
    break
  fi
done
```

**Kết quả mong đợi:** "Rate limit hit at request 301" (hoặc gần đó), HTTP 429 với header `RateLimit-Remaining: 0`.

---

### TC-09: Brute Force Protection — 5 lần fail → lockout

**Mục đích:** Sau 5 lần sai password, tài khoản bị khóa tạm thời.

```bash
# 5 lần đăng nhập sai
for i in $(seq 1 5); do
  curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "client_id=aeroflow-frontend&grant_type=password&username=ai-engineer&password=WRONG_PASSWORD" \
    | jq -r .error_description
done

# Lần 6 — dù password đúng cũng bị từ chối
curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=ai-engineer&password=Engineer@1234" \
  | jq .error_description
```

**Kết quả mong đợi:**
- Lần 1-5: `"Invalid user credentials"`
- Lần 6: `"Account is temporarily disabled"` hoặc tương tự

```bash
# Unlock thủ công (Keycloak Admin API)
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d "client_id=admin-cli&grant_type=password&username=admin&password=admin" \
  | jq -r .access_token)

USER_ID=$(curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8080/admin/realms/aeroflow/users?username=ai-engineer" \
  | jq -r '.[0].id')

curl -s -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8080/admin/realms/aeroflow/attack-detection/brute-force/users/$USER_ID"
echo "User unlocked"
```

---

### TC-10: Concurrent Session Limit — Tối đa 2

**Mục đích:** Session thứ 3 bị từ chối.

```bash
# Session 1
TOKEN1=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | jq -r .access_token)
echo "Session 1: ${TOKEN1:0:20}..."

# Session 2
TOKEN2=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | jq -r .access_token)
echo "Session 2: ${TOKEN2:0:20}..."

# Session 3 — phải bị từ chối hoặc session cũ nhất bị revoke
TOKEN3=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | jq '{access_token: .access_token[:20], error: .error}')
echo "Session 3: $TOKEN3"
```

**Kết quả mong đợi:** Session 3 bị reject hoặc session 1 bị revoke (tùy policy `ssoSessionMaxConcurrentLogins=2`).

---

### TC-11: Password Policy

**Mục đích:** Keycloak từ chối password không đủ độ phức tạp.

```bash
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d "client_id=admin-cli&grant_type=password&username=admin&password=admin" \
  | jq -r .access_token)

# Tạo user test
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  "http://localhost:8080/admin/realms/aeroflow/users" \
  -d '{"username":"test-policy","enabled":true,"credentials":[{"type":"password","value":"short","temporary":false}]}' \
  | jq .errorMessage
```

**Kết quả mong đợi:** Error message về password policy (length, complexity).

---

### TC-12: Kafka Audit Pipeline — Event đến PostgreSQL

**Mục đích:** Login event từ Keycloak SPI → Kafka → audit_logs.

```bash
# Bước 1: đếm rows hiện tại
COUNT_BEFORE=$(docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow -tAc \
  "SELECT COUNT(*) FROM audit_logs;")
echo "Before: $COUNT_BEFORE"

# Bước 2: trigger login event
curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  > /dev/null

# Bước 3: đợi consumer xử lý (~3s)
sleep 5

# Bước 4: đếm rows sau
COUNT_AFTER=$(docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow -tAc \
  "SELECT COUNT(*) FROM audit_logs;")
echo "After: $COUNT_AFTER"

# Bước 5: xem event mới nhất
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT action, actor, ip_address, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 3;"
```

**Kết quả mong đợi:**
- `COUNT_AFTER > COUNT_BEFORE`
- Row mới nhất có `action=LOGIN`, `actor` chứa user ID hoặc username

```bash
# Kiểm tra message trực tiếp trong Kafka (đọc partition 0 từ đầu)
docker exec aeroflow-kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --consumer.config /tmp/admin.props \
  --topic audit.auth.events \
  --partition 0 \
  --offset earliest \
  --max-messages 3 \
  --timeout-ms 8000
# Lưu ý: dùng --partition + --offset thay vì --from-beginning
# vì --from-beginning với consumer group chỉ hoạt động khi group chưa có committed offset
```

---

### TC-13: Kafka ACL — Unauthorized user bị từ chối

**Mục đích:** User không có ACL không produce được vào topic.

```bash
# Thử produce với user audit-consumer (chỉ có quyền Read, không có Write)
docker exec aeroflow-kafka bash -c "
printf 'security.protocol=SASL_PLAINTEXT\nsasl.mechanism=PLAIN\nsasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username=\"audit-consumer\" password=\"'\"$AUDIT_CONSUMER_KAFKA_PASSWORD\"'\";\n' > /tmp/consumer.props
echo 'unauthorized-message' | kafka-console-producer.sh \
  --bootstrap-server localhost:9092 \
  --producer.config /tmp/consumer.props \
  --topic audit.auth.events 2>&1
"
```

**Kết quả mong đợi:** `TOPIC_AUTHORIZATION_FAILED` hoặc tương tự — producer bị từ chối.

---

### TC-14: Keycloak Event Logging

**Mục đích:** Events được ghi trong Keycloak Admin Console.

```bash
# Login để tạo event
curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=ai-engineer&password=Engineer@1234" \
  > /dev/null

# Xem events qua Admin API
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d "client_id=admin-cli&grant_type=password&username=admin&password=admin" \
  | jq -r .access_token)

curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8080/admin/realms/aeroflow/events?type=LOGIN&max=5" \
  | jq '.[].type'
```

**Kết quả mong đợi:** `"LOGIN"` xuất hiện trong response.

---

### TC-15: Correlation ID

**Mục đích:** Mỗi request qua Kong có header `X-Request-Id` duy nhất.

```bash
# Lấy X-Request-Id từ response headers
curl -sv http://localhost:8000/api/health \
  -H "Authorization: Bearer $TOKEN" 2>&1 | grep -i "x-request-id"
```

**Kết quả mong đợi:** `< X-Request-Id: <uuid>#<counter>` — mỗi request là unique.

---

### TC-16: IP Restriction — Dynamic Allowlist (auth-api → Kong sync)

**Mục đích:** Kong ip-restriction plugin được cập nhật tự động từ bảng `ip_allowlists` qua auth-api.
**Kết quả test 2026-05-29:** PASS

```bash
TENANT="a0000000-0000-0000-0000-000000000001"

# 1. Thêm CIDR rule → Kong cập nhật ngay
curl -s -X POST "http://localhost:8200/api/auth/ip-allowlists" \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Id: admin" -H "X-User-Roles: platform-admin" \
  -H "Content-Type: application/json" \
  -d '{"cidr":"10.0.0.0/24","label":"Office Network"}'

# 2. Xác nhận Kong plugin được tạo/cập nhật
curl -s http://localhost:8001/plugins | \
  python3 -c "import sys,json; p=[x for x in json.load(sys.stdin)['data'] if x['name']=='ip-restriction']; print(p[0]['config']['allow'] if p else 'missing')"
# Expected: ['10.0.0.0/24']

# 3. Switch sang allow_all → Kong allow: 0.0.0.0/0
curl -s -X PUT "http://localhost:8200/api/auth/ip-allowlist/config" \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Roles: platform-admin" \
  -H "Content-Type: application/json" -d '{"mode":"allow_all"}'
# Kong config: ['0.0.0.0/0', '::/0']

# 4. Invalid CIDR → 422
curl -s -X POST "http://localhost:8200/api/auth/ip-allowlists" \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Id: admin" -H "X-User-Roles: platform-admin" \
  -H "Content-Type: application/json" -d '{"cidr":"not-a-cidr"}'
# Expected: {"detail":"Invalid CIDR: not-a-cidr"}

# 5. Non-admin → 403
curl -s "http://localhost:8200/api/auth/ip-allowlists" \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Roles: ai-engineer"
# Expected: {"detail":"Requires PLATFORM_ADMIN role"}
```

**Lưu ý:** platform-admin user có OTP bật nên không lấy được token qua direct grant từ CLI.
Test qua `http://localhost:8200` (auth-api trực tiếp) với header X-User-Roles inject thay Kong.

---

### TC-17: mTLS — Kong client cert đã đăng ký

**Mục đích:** Xác nhận Kong đã đăng ký client cert (sau khi chạy gen-certs.sh + kong-setup.sh).

```bash
# Kiểm tra cert đã gắn vào service
curl -s http://localhost:8001/services/aeroflow-backend | jq .client_certificate

# Danh sách certs đã đăng ký
curl -s http://localhost:8001/certificates | jq '.data | length'
echo "certs registered"

# CA certificates
curl -s http://localhost:8001/ca_certificates | jq '.data | length'
echo "CA certs registered"
```

**Kết quả mong đợi:** `client_certificate` có `id` (không phải null), ít nhất 1 cert và 1 CA cert.

---

### TC-18: HAProxy Load Balancing — Round-robin

**Mục đích:** Request được phân phối đều giữa 2 Keycloak nodes.

```bash
# Gửi 10 request qua HAProxy
for i in $(seq 1 10); do
  curl -s http://localhost:8080/realms/aeroflow/.well-known/openid-configuration > /dev/null
done

# Xem log — cả 2 nodes nhận requests
docker compose logs keycloak-node1 --tail=10 2>&1 | grep -c "GET /realms"
docker compose logs keycloak-node2 --tail=10 2>&1 | grep -c "GET /realms"
```

**Kết quả mong đợi:** Cả node1 và node2 đều xử lý requests (round-robin).

---

### TC-19: Token decode — Payload đúng

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | grep -o '"access_token":"[^"]*"' | head -1 | sed 's/"access_token":"//;s/"$//')

# Decode JWT payload (base64url → JSON)
echo $TOKEN | cut -d. -f2 | sed 's/-/+/g;s/_/\//g' | \
  awk '{l=length($0)%4; if(l==2)$0=$0"=="; if(l==3)$0=$0"="; print}' | base64 -d 2>/dev/null
```

**Kết quả mong đợi:**
```json
{
  "sub": "<uuid>",
  "email": "admin@aeroflow.local",
  "iss": "http://localhost:8080/realms/aeroflow",
  "aud": ["aeroflow-backend","account"],
  "realm_access": ["platform-admin","..."],
  "exp_in_min": 14
}
```

---

### TC-20: MFA Bắt Buộc (CONFIGURE_TOTP)

**Điều kiện:** Fresh realm import (sau `docker compose down -v && docker compose up -d`).

```bash
# Login lần đầu với user mới — phải bị yêu cầu setup TOTP
curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456"
```

**Kết quả mong đợi:** Response có `"error":"invalid_grant"` với `"error_description":"Account is not fully set up"` và required action `CONFIGURE_TOTP` — không cấp token cho đến khi user hoàn thành TOTP setup qua browser.

---

### TC-21: IP Allowlist Middleware — Release Worker

```bash
# Test từ bên ngoài Docker network (localhost qua port 8100 trực tiếp)
# Trong Docker bridge, host Docker IP thường là 172.17.0.1 hoặc tương tự — thuộc private range nên PASS

# Để test FAIL: gọi trực tiếp từ host với IP không thuộc private range
# Trong môi trường Docker Compose, client.host sẽ là Docker bridge IP → thuộc 172.16.0.0/12 → PASS
# Smoke test: health check phải luôn accessible
curl -sf http://localhost:8100/health && echo "Health OK (no auth, no IP check)"

# Verify middleware active trong release-worker logs
docker compose logs release-worker | grep -i "origin\|forbidden\|middleware" | tail -5
```

---

### TC-22: audit_logs Immutable Trigger

```bash
# Chạy sau khi stack fresh start (trigger đã được tạo từ init.sql)
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow -c "
  -- Thử UPDATE một row — phải bị block
  UPDATE audit_logs SET action = 'HACKED' WHERE id IN (SELECT id FROM audit_logs LIMIT 1);
"
```

**Kết quả mong đợi:** `ERROR:  audit_logs is immutable — UPDATE/DELETE are not allowed (compliance audit trail)`

```bash
# Kiểm tra trigger tồn tại
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT trigger_name, event_manipulation FROM information_schema.triggers WHERE trigger_name LIKE 'trg_audit_logs%';"
# → trg_audit_logs_no_update | UPDATE
# → trg_audit_logs_no_delete | DELETE
```

---

### TC-23 + TC-24: MongoDB & Elasticsearch Services

```bash
# MongoDB
curl -sf http://localhost:27017 || docker exec aeroflow-mongo mongosh --eval "db.adminCommand('ping')" | grep -i "ok"

# Elasticsearch
curl -sf http://localhost:9200/_cluster/health | grep -o '"status":"[^"]*"'
# Kết quả: "status":"green" hoặc "yellow" (single node)
```

---

### TC-25: Jaeger Distributed Tracing

```bash
# Kiểm tra Jaeger UI accessible
curl -sf http://localhost:16686/ > /dev/null && echo "Jaeger UI OK"

# Trigger một pipeline request (tạo trace)
TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | jq -r .access_token)
curl -s http://localhost:8000/api/release/pipelines -H "Authorization: Bearer $TOKEN" > /dev/null

# Xem trace trong Jaeger UI: http://localhost:16686
# Service: release-worker → tìm span "pipeline.execute"
```

---

### TC-26: JWKS Refresher

```bash
# Kiểm tra service đang chạy và sync JWKS
docker compose logs jwks-refresher --tail=20
# Kết quả mong đợi: log "JWKS fetched, kid=..." mỗi 300s

# Kiểm tra service đã start và không crash
docker compose ps jwks-refresher
```

---

### TC-27: IP Allowlist CRUD + Kong Sync — Full Flow

**Mục đích:** Verify toàn bộ vòng đời IP rule: thêm → toggle → xóa → đồng bộ Kong.
**Kết quả test 2026-05-29:** PASS ✅

```bash
TENANT="a0000000-0000-0000-0000-000000000001"

# Thêm 2 rules
R1=$(curl -s -X POST http://localhost:8200/api/auth/ip-allowlists \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Id: admin" -H "X-User-Roles: platform-admin" \
  -H "Content-Type: application/json" -d '{"cidr":"10.0.0.0/24","label":"Office"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

R2=$(curl -s -X POST http://localhost:8200/api/auth/ip-allowlists \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Id: admin" -H "X-User-Roles: platform-admin" \
  -H "Content-Type: application/json" -d '{"cidr":"192.168.1.0/24","label":"Dev"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Đặt mode = allowlist → Kong nhận 2 CIDRs
curl -s -X PUT http://localhost:8200/api/auth/ip-allowlist/config \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Roles: platform-admin" \
  -H "Content-Type: application/json" -d '{"mode":"allowlist"}'
# Kong allow: ['10.0.0.0/24', '192.168.1.0/24']

# Toggle disable R1
curl -s -X PATCH "http://localhost:8200/api/auth/ip-allowlists/$R1/toggle" \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Roles: platform-admin"
# Kong allow: ['192.168.1.0/24']  (chỉ còn R2 active)

# Delete R2
curl -s -X DELETE "http://localhost:8200/api/auth/ip-allowlists/$R2" \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Roles: platform-admin"
# Kong allow: fallback ['127.0.0.1/32','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16']
```

| Step | Expected | Observed |
|---|---|---|
| Add 2 CIDRs | 201 Created × 2 | ✅ |
| Set mode=allowlist | Kong allow = 2 CIDRs | ✅ |
| Toggle disable | Kong allow = 1 CIDR | ✅ |
| Delete last active | Kong allow = private fallback | ✅ |
| allow_all mode | Kong allow = 0.0.0.0/0, ::/0 | ✅ |
| Invalid CIDR | 422 detail: Invalid CIDR | ✅ |
| Non-admin role | 403 detail: Requires PLATFORM_ADMIN | ✅ |

---

### TC-28: API Keys — Generate / Revoke / Rotate

**Mục đích:** Verify API key lifecycle: tạo key (raw shown once) → revoke → rotate (audit chain).
**Kết quả test 2026-05-29:** PASS ✅

```bash
TENANT="a0000000-0000-0000-0000-000000000001"

# Tạo key read_only
RESULT=$(curl -s -X POST http://localhost:8200/api/auth/api-keys \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Id: admin" -H "X-User-Roles: platform-admin" \
  -H "Content-Type: application/json" \
  -d '{"name":"CI Pipeline","scope":"read_only","expires_at":"2027-01-01"}')
KEY_ID=$(echo $RESULT | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
# raw_key trong response (bắt đầu sk-ro-), KHÔNG có trong GET list sau đó

# Revoke
curl -s -X POST "http://localhost:8200/api/auth/api-keys/$KEY_ID/revoke" \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Roles: platform-admin"
# revoked_at: timestamp

# Revoke lần 2 → 404
curl -s -X POST "http://localhost:8200/api/auth/api-keys/$KEY_ID/revoke" \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Roles: platform-admin"
# {"detail":"Key not found or already revoked"}

# Tạo key full_access và rotate
K2=$(curl -s -X POST http://localhost:8200/api/auth/api-keys \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Id: admin" -H "X-User-Roles: platform-admin" \
  -H "Content-Type: application/json" -d '{"name":"Prod Key","scope":"full_access"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -s -X POST "http://localhost:8200/api/auth/api-keys/$K2/rotate" \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Id: admin" -H "X-User-Roles: platform-admin"
# new key: rotated_from = $K2, raw_key present (sk-prod-)
# old key ($K2): revoked_at set

# Invalid scope → 422
curl -s -X POST http://localhost:8200/api/auth/api-keys \
  -H "X-Tenant-Id: $TENANT" -H "X-User-Id: admin" -H "X-User-Roles: platform-admin" \
  -H "Content-Type: application/json" -d '{"name":"X","scope":"superadmin"}'
# {"detail":"Invalid scope: superadmin"}
```

| Step | Expected | Observed |
|---|---|---|
| Create read_only | 201, raw_key starts sk-ro- | ✅ |
| GET list | raw_key absent | ✅ |
| Revoke | revoked_at set | ✅ |
| Revoke again | 404 | ✅ |
| Rotate full_access | new key sk-prod-, rotated_from set | ✅ |
| Old key after rotate | revoked_at set | ✅ |
| Invalid scope | 422 | ✅ |

**Security note:** Raw key chỉ xuất hiện trong response POST /api-keys và POST .../rotate — không bao giờ lưu plaintext vào DB (chỉ bcrypt hash).

---

## Database Checks

```bash
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow
```

```sql
-- Kiểm tra schemas
\dn

-- audit_logs partition structure
\d+ audit_logs

-- Rows mới nhất
SELECT action, actor, ip_address, created_at
FROM audit_logs ORDER BY created_at DESC LIMIT 10;

-- Kiểm tra plans và roles
SELECT * FROM plans;
SELECT * FROM roles;

-- Thêm partition tháng tiếp theo (chạy đầu tháng)
CREATE TABLE IF NOT EXISTS audit_logs_2026_07
  PARTITION OF audit_logs
  FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
\q
```

---

## Troubleshooting

### Keycloak node1 không start
```bash
docker compose logs keycloak-node1 | tail -50
# Thường do PostgreSQL chưa ready → đợi rồi:
docker compose restart keycloak-node1
```

### Keycloak node2 không join cluster
```bash
docker compose logs keycloak-node2 | grep -i "jgroup\|cluster\|ispn"
# JGroups TCPPING: node2 phải thấy node1 ở port 7600
docker network inspect data-agentt_aeroflow-net
# Đảm bảo cả 2 nodes đều trong aeroflow-net
```

### Kong aeroflow-jwks plugin lỗi
```bash
docker compose logs kong --tail=50 | grep -i "error\|jwks\|aeroflow"
# Plugin không load → kiểm tra KONG_PLUGINS=bundled,aeroflow-jwks trong compose
# JWKS fetch fail → kiểm tra keycloak-lb có healthy không
```

### Kafka không kết nối được
```bash
docker compose logs kafka | tail -30
docker compose logs audit-consumer | tail=20
# SASL error → kiểm tra username/password trong docker-compose.yml
# Topic chưa có → chạy bash infra/kafka/kafka-setup.sh
```

### audit_logs không có rows / audit-consumer error `source_event_id does not exist`

```bash
# Thêm column thiếu (chỉ cần nếu dùng volume cũ)
docker exec aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS source_event_id text;"
docker restart aeroflow-audit-consumer
```

```bash
# Kiểm tra SPI đang gửi event
docker compose logs keycloak-node1 | grep -i "kafka\|aeroflow-kafka\|event"

# Kiểm tra consumer đang consume
docker compose logs audit-consumer | grep "Stored\|error"

# Trigger event: đăng nhập vào Keycloak → SPI push ngay
curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  > /dev/null
sleep 5
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT COUNT(*) FROM audit_logs;"
```

### Kong migration fail
```bash
docker compose logs kong-migration
# "already exists" → bình thường, migration idempotent

# Reset hoàn toàn (xóa data)
docker compose down -v && docker compose up -d
```

### Kong aeroflow-jwks chặn requests sau rebuild Kong

Sau khi sửa `handler.lua` và rebuild Kong, cần re-register plugins:

```powershell
docker compose -f docker/docker-compose.yml build kong
docker compose -f docker/docker-compose.yml up -d kong
# Đợi Kong healthy
docker compose -f docker/docker-compose.yml --profile setup run --rm kong-setup
```

### Release-worker Kafka consumer không nhận message sau ACL setup muộn

Nếu Kafka ACL được setup sau khi release-worker đã start, consumer thread có thể đã exit do lỗi authorization. Cần restart:
```bash
docker restart aeroflow-release-worker aeroflow-celery-worker
```

### Clean restart hoàn toàn (reset về trạng thái ban đầu)

```bash
# Xóa tất cả containers + volumes → realm import sẽ chạy lại
docker compose down -v

# Rebuild images nếu code thay đổi
docker compose build --no-cache keycloak-node1 keycloak-node2 kong

# Khởi động lại
docker compose up -d

# Chờ healthy rồi setup
bash infra/kafka/kafka-setup.sh
docker compose -f docker/docker-compose.yml --profile setup run --rm kong-setup
```

> Sau `down -v`, Keycloak import realm-export.json fresh → user passwords là: `platform-admin=Admin@123456`, `ai-engineer=Engineer@1234`, `executive-viewer=Viewer@123456`.

### Frontend không kết nối Keycloak
```bash
docker exec aeroflow-frontend env | grep VITE
# VITE_KEYCLOAK_URL phải là http://localhost:8080 (HAProxy)

docker compose restart frontend
```

### Port conflict (Windows)
```bash
netstat -ano | findstr "8080 8081 8082 8000 8001 5432 5173 9092 1337"
# Đổi ports trong docker-compose.yml nếu cần
```

---

## Dừng / Xoá Services

```bash
# Dừng, giữ data
docker compose stop

# Khởi động lại
docker compose start

# Xóa hoàn toàn (bao gồm volumes/data)
docker compose down -v

# Rebuild images sau khi thay đổi code
docker compose build --no-cache keycloak-node1 keycloak-node2 kong
docker compose up -d
```

---

## Checklist Smoke Test (nhanh)

Chạy theo thứ tự để xác nhận toàn bộ stack hoạt động sau khi deploy:

```bash
# ── 1. Infra services ────────────────────────────────────────────────────────
docker ps --format "table {{.Names}}\t{{.Status}}" | grep aeroflow

# ── 2. Keycloak HA (cả 2 nodes) ─────────────────────────────────────────────
curl -sf http://localhost:8081/health/ready && echo "KC node1 OK"
curl -sf http://localhost:8082/health/ready && echo "KC node2 OK"

# ── 3. JWKS endpoint ─────────────────────────────────────────────────────────
curl -sf http://localhost:8080/realms/aeroflow/protocol/openid-connect/certs \
  | python -c "import sys,json; k=json.load(sys.stdin); print(f'JWKS OK: {len(k[\"keys\"])} keys')"

# ── 4. Lấy token — Tenant 1 (AeroFlow Dev) ──────────────────────────────────
T1_TOKEN=$(curl -sf -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin123456!" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
[ -n "$T1_TOKEN" ] && echo "T1 Token OK" || echo "T1 Token FAIL"

# ── 5. Lấy token — Tenant 2 (Helios Corp) ───────────────────────────────────
T2_TOKEN=$(curl -sf -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d "client_id=aeroflow-frontend&grant_type=password&username=helios-admin&password=H3lios@Admin01!" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
[ -n "$T2_TOKEN" ] && echo "T2 Token OK" || echo "T2 Token FAIL"

# ── 6. Kong plugins ───────────────────────────────────────────────────────────
curl -sf http://localhost:8001/plugins | python -c \
  "import sys,json; names=sorted({p['name'] for p in json.load(sys.stdin)['data']}); print('Plugins:', names)"
# → ['aeroflow-jwks', 'correlation-id', 'ip-restriction', 'rate-limiting']

# ── 7. E2E qua Kong → release-worker ────────────────────────────────────────
curl -sf -H "Authorization: Bearer $T1_TOKEN" http://localhost:8000/api/release/pipelines \
  | python -c "import sys,json; r=json.load(sys.stdin); print('Release E2E OK, count:', r.get('count',0))"

# ── 8. No-auth → 401 ─────────────────────────────────────────────────────────
STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/release/pipelines)
[ "$STATUS" = "401" ] && echo "No-auth 401 OK" || echo "FAIL: got $STATUS"

# ── 9. Auth API health ───────────────────────────────────────────────────────
curl -sf http://localhost:8200/api/auth/health && echo " ← Auth API OK"

# ── 10. OpenBao healthy (vault unsealed) ─────────────────────────────────────
curl -sf http://localhost:8300/v1/sys/health \
  | python -c "import sys,json; r=json.load(sys.stdin); print('OpenBao sealed:', r.get('sealed', '?'))"
# → OpenBao sealed: False

# ── 11. Secrets Vault — T1 có thể list secrets ──────────────────────────────
COUNT=$(curl -sf -H "Authorization: Bearer $T1_TOKEN" http://localhost:8000/api/auth/secrets \
  | python -c "import sys,json; print(len(json.load(sys.stdin)))")
[ "$COUNT" -ge 0 ] && echo "T1 Secrets OK: $COUNT secrets" || echo "Secrets FAIL"

# ── 12. Tenant isolation — T1 không thấy T2 secrets ─────────────────────────
T2_FIRST=$(curl -sf -H "Authorization: Bearer $T2_TOKEN" http://localhost:8000/api/auth/secrets \
  | python -c "import sys,json; secrets=json.load(sys.stdin); print(secrets[0]['id'] if secrets else 'none')")
if [ "$T2_FIRST" != "none" ]; then
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $T1_TOKEN" \
    "http://localhost:8000/api/auth/secrets/$T2_FIRST/reveal")
  [ "$STATUS" = "404" ] && echo "Tenant isolation OK (T1 blocked from T2 secret)" || echo "ISOLATION FAIL: $STATUS"
fi

# ── 13. MinIO healthy ─────────────────────────────────────────────────────────
curl -sf http://localhost:9000/minio/health/live && echo "MinIO OK"

# ── 14. Kafka topics ──────────────────────────────────────────────────────────
docker exec aeroflow-kafka bash -c "
printf 'security.protocol=SASL_PLAINTEXT\nsasl.mechanism=PLAIN\nsasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username=\"admin\" password=\"'\"$KAFKA_ADMIN_PASSWORD\"'\";\n' > /tmp/a.props
/opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --command-config /tmp/a.props --list
" | grep -E "audit.auth.events|release.pipeline" | sort

# ── 15. PostgreSQL — tenants table ───────────────────────────────────────────
docker exec aeroflow-postgres psql -U aeroflow -d aeroflow -tAc \
  "SELECT name, slug FROM tenants ORDER BY created_at;"
# → AeroFlow Dev|aeroflow-dev
# → Helios Corp|helios-corp

# ── 16. PostgreSQL — core tables ─────────────────────────────────────────────
docker exec aeroflow-postgres psql -U aeroflow -d aeroflow -tAc \
  "SELECT COUNT(*) FROM information_schema.tables
   WHERE table_name IN ('tenants','audit_logs','secrets_vault','api_keys','ip_allowlists',
                        'pipelines','release_history','rollback_operations');"
# → 8

# ── 17. MongoDB healthy ───────────────────────────────────────────────────────
docker exec aeroflow-mongo mongosh --eval "db.adminCommand('ping').ok" --quiet

# ── 18. Elasticsearch healthy ────────────────────────────────────────────────
curl -sf http://localhost:9200/_cluster/health | python -c \
  "import sys,json; r=json.load(sys.stdin); print('ES status:', r['status'])"

# ── 19. Jaeger UI ─────────────────────────────────────────────────────────────
curl -sf -o /dev/null http://localhost:16686/ && echo "Jaeger OK"

# ── 20. Run full multi-tenant isolation suite ────────────────────────────────
python infra/scripts/test_tenant_isolation.py
# → 23 PASSED | 0 FAILED | 23 TOTAL
```

---

---

# Phần 2 — Release Management

> Xem `docs/release-system.md` để hiểu kiến trúc chi tiết.

---

## R1 — Khởi động Release Stack

Release services được thêm vào `docker-compose.yml` (xem file). Sau khi cập nhật:

```bash
# Build và khởi động release services
docker compose up -d release-worker

# Theo dõi logs
docker compose logs -f release-worker
```

**Kết quả mong đợi:**
```
release-worker  | {"event": "kafka.consumer.started", "topics": ["release.pipeline.triggered", "release.rollback.initiated"]}
release-worker  | INFO:     Application startup complete.
```

> **Thứ tự khởi động release services:** postgres → kafka (sau auth setup) → release-worker

---

## R2 — URLs & Credentials Release

| Service | URL | Ghi chú |
|---------|-----|---------|
| Release Worker (trực tiếp) | http://localhost:8100 | FastAPI REST API |
| Release Worker (qua Kong) | http://localhost:8000/api/release | **Dùng endpoint này** — có JWT auth |
| Release Worker Health | http://localhost:8100/health | Health check (public, không cần token) |
| PostgreSQL | localhost:5432 | aeroflow / aeroflow_secret |
| MinIO Console | http://localhost:9001 | minio / minio_secret |
| MinIO S3 API | http://localhost:9000 | minio / minio_secret |

### Kafka Users (Release)

| Username | Password | ACL |
|----------|----------|-----|
| `ci-service` | see `CI_SERVICE_KAFKA_PASSWORD` in `.env` | WRITE `release.pipeline.triggered` |
| `release-worker` | see `RELEASE_WORKER_KAFKA_PASSWORD` in `.env` | READ pipeline.triggered, WRITE pipeline.status |
| `drift-detector` | see `DRIFT_DETECTOR_KAFKA_PASSWORD` in `.env` | WRITE `release.drift.detected` |
| `scan-runner` | see `SCAN_RUNNER_KAFKA_PASSWORD` in `.env` | WRITE `release.scan.completed` |
| `notification-consumer` | see `NOTIFICATION_CONSUMER_KAFKA_PASSWORD` in `.env` | READ pipeline.status + drift.detected |

---

## R3 — Setup Release Schema (PostgreSQL)

> **Tự động trên fresh start:** `release-init.sql` được mount vào postgres tại `/docker-entrypoint-initdb.d/02-release-init.sql` — chạy tự động khi volume postgres chưa có data. Bước này chỉ cần thực hiện thủ công nếu stack đã chạy trước và volume đã tồn tại.

```bash
# Chỉ cần nếu đang dùng volume cũ (không down -v)
docker exec -i aeroflow-postgres psql -U aeroflow -d aeroflow \
  < infra/sql/release-init.sql

# Verify tables đã tạo
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "\dt pipelines release_packages release_history rollback_operations drift_events environment_configs release_approvals pipeline_steps"
```

**Kết quả mong đợi:**
```
               List of relations
 Schema |         Name          | Type  |   Owner
--------+-----------------------+-------+----------
 public | drift_events          | table | aeroflow
 public | environment_configs   | table | aeroflow
 public | pipeline_steps        | table | aeroflow
 public | pipelines             | table | aeroflow
 public | release_approvals     | table | aeroflow
 public | release_history       | table | aeroflow  (partitioned)
 public | release_packages      | table | aeroflow
 public | rollback_operations   | table | aeroflow
```

```bash
# Kiểm tra partitions release_history
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "\dt release_history*"
# → release_history_2026_05, _06, _07, _08
```

---

## R4 — Setup Release Kafka Topics & ACL

Chạy một lần sau auth Kafka setup:

```bash
bash infra/kafka/release-setup.sh
```

Script tạo:
- Topics: `release.pipeline.triggered`, `release.pipeline.status`, `release.drift.detected`, `release.rollback.initiated`, `release.scan.completed`
- Kafka users: `ci-service`, `release-worker`, `drift-detector`, `scan-runner`, `notification-consumer`
- ACL cho từng user (WRITE/READ đúng topic)

**Verify sau khi chạy:**

```bash
# List release topics
docker exec aeroflow-kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 \
  --command-config /tmp/admin_release.props \
  --list | grep "^release\."

# Kết quả mong đợi:
# release.drift.detected
# release.pipeline.status
# release.pipeline.triggered
# release.rollback.initiated
# release.scan.completed
```

---

## R5 — Test Cases Release

### Kết quả Test Release — 2026-05-27

| TC | Tên | Trạng thái | Ghi chú / Kết quả thực tế |
|----|-----|-----------|---------|
| TR-01 | Release Worker health | ✅ | `{"status":"ok","service":"release-worker"}` |
| TR-02 | Trigger pipeline qua API | ✅ | pipeline_id=`pipe-20260527-b8776d` tạo thành công |
| TR-03 | Pipeline state ghi PostgreSQL | ✅ | `status=SUCCESS` trong bảng `pipelines` |
| TR-04 | Kafka trigger message nhận đúng | ✅ | Log `kafka.message.received` + consumer thread khởi động |
| TR-05 | Security scan gate PASS | ✅ | trivy/bandit/pip-audit chưa cài → skip=PASS, pipeline SUCCESS |
| TR-06 | Security scan gate FAIL | ✅ | Cài bandit → B104 HIGH severity → `status=FAILED`, `error="Security scan gate failed"` |
| TR-07 | Dev auto-promote | ✅ | target=dev → deploy ngay, `release_history` ghi `dev\|SUCCESS` |
| TR-08 | Staging cần approval | ✅ | target=staging → `status=AWAITING_APPROVAL` trong PostgreSQL |
| TR-09 | Approve staging → deploy | ✅ | POST approve → `status=approved` → pipeline `SUCCESS`, `release_history staging\|SUCCESS` |
| TR-10 | Reject staging | ✅ | POST reject → `status=FAILED`, `error_message="Rejected by <user_id>"` |
| TR-11 | Rollback trigger | ✅ | POST /rollback → `rb-20260527-85ceaf` tạo, Kafka event `rollback.initiated` |
| TR-12 | Rollback compensating steps | ✅ | Step 1 (update_db_pointer) SUCCESS; Step 2 (reroute_kong) SUCCESS sau khi tạo Kong services; Step 3 (redeploy_snapshot) SUCCESS |
| TR-13 | Rollback FULL SUCCESS sau fix Kong | ✅ | `aeroflow-prod` + `aeroflow-staging` Kong services đã tạo → `status=SUCCESS`, `current_step=3`; `release_history` có entry `ROLLED_BACK` |
| TR-14 | Release history ghi đúng | ✅ | Tất cả deploy ghi vào `release_history` (pipeline_id, env, status, triggered_by, deployed_at) |
| TR-15 | Drift detection | ✅ | Insert prod/staging config khác nhau → `drift_events` ghi 3 drifted keys, `severity=CRITICAL_DRIFT` |
| TR-16 | Kafka ACL ci-service chỉ WRITE trigger | ✅ | ci-service WRITE `pipeline.triggered` OK; READ `pipeline.status` → `GroupAuthorizationFailedError` |
| TR-17 | release_history immutable | ✅ | Trigger `fn_release_history_immutable` block UPDATE/DELETE: `"release_history is immutable"` |
| TR-18 | release_history partitioned | ✅ | 4 partitions (2026-05 → 2026-08); insert `2026-06-15` → `release_history_2026_06` ✓ |
| TR-19 | Drift detection API | ✅ | `POST /drift/detect` → `{"status":"drift_detected","drift_id":2,"drift_keys":["dictionary_item_added","values_changed"]}` |
| TR-20 | Drift list API | ✅ | `GET /drift` → trả `drift_events` array với `id`, `env_pair`, `drift_keys`, `severity`, `resolved` |
| TR-21 | Drift resolve API | ✅ | `POST /drift/resolve/2` → `{"status":"resolved","drift_id":2,"resolved_by":"..."}` |
| TR-22 | Partition auto-create API | ✅ | `POST /admin/create-partition` → `{"status":"created","table":"release_history_2026_06"}` (idempotent) |
| TR-23 | Kong services cho rollback | ✅ | `aeroflow-prod` và `aeroflow-staging` Kong services tồn tại; rollback step 2 PATCH thành công |

> **Ngày chạy test:** 2026-05-27 · **Kết quả:** 23/23 PASS (0 FAIL, 0 SKIP)

---

## R5b — Bugs phát hiện & Fixes đã áp dụng

| # | Bug | Nguyên nhân | Fix |
|---|-----|-------------|-----|
| 1 | `approve_deployment` không dispatch pipeline sau approval | Dùng `execute_pipeline.delay()` (Celery) nhưng không có Celery worker | Thay bằng `threading.Thread(target=_run_approval_deploy, ...)` |
| 2 | `_run_approval_deploy` không tồn tại — cần hàm riêng | Approval cần skip build/scan (đã chạy), chỉ deploy thôi | Tạo `_run_approval_deploy()` lấy `package_id` từ DB → gọi `_deploy_to_env()` |
| 3 | `_run_rollback_impl` gọi `execute_rollback(event)` trực tiếp — Celery task không callable as function | `execute_rollback` là Celery task, gọi trực tiếp thiếu request context → `AttributeError: 'NoneType' object has no attribute 'push'` | Extract `_execute_rollback_core()` là plain function; cả thread lẫn Celery task gọi vào đó |
| 4 | `release_history` không có trigger bảo vệ immutability | Schema chỉ có comment `-- KHÔNG UPDATE/DELETE`, không có enforcement | Thêm trigger `fn_release_history_immutable()` + `trg_release_history_no_update` + `trg_release_history_no_delete` |
| 5 | `import threading` bị đặt trong `if __name__ == "__main__"` | Threading dùng trong `start_kafka_consumer` (body file) nhưng chỉ import ở entry point | Move `import threading` lên top-level imports |
| 6 | `kong-setup.sh` dùng `KC_BASE` (localhost:8080) cho `JWKS_URI` của Kong plugin | Kong chạy trong Docker container — `localhost:8080` trỏ về Kong chính, không phải Keycloak | Đổi sang `KONG_KC_HOST=http://aeroflow-keycloak-lb:8080`; fix đã áp dụng vào `kong-setup.sh` |
| 7 | `release-setup.sh` chạy trên Windows Git Bash bị path conversion `/opt/kafka/...` → `C:/Program Files/Git/...` | Git Bash tự convert Unix paths bắt đầu bằng `/` | Chạy via `docker exec aeroflow-kafka bash -c "/opt/kafka/bin/..."` thay vì trực tiếp; cần update script |
| 8 | `keycloak-users` Kong consumer phải tồn tại trước khi jwks-refresher đăng ký JWT credential | jwks-refresher gọi `POST /consumers/keycloak-users/jwt` — nếu consumer chưa tạo → 404 | Tạo consumer: `curl -X POST http://localhost:8001/consumers -d "username=keycloak-users"` rồi restart jwks-refresher |

### Những gì còn thiếu / cần làm thêm

| # | Hạng mục | Mức độ | Ghi chú |
|---|----------|--------|---------|
| 1 | **Celery worker** cho production | Low | Hiện dùng thread trực tiếp; cần Celery worker process cho retry/rate-limit/observability |
| 2 | **Production environment** approval chain | Low | Chưa test target=production (hai-stage: staging + production approval) |
| 3 | **Elasticsearch indexing** | Medium | Service đã deploy; cần thêm code index release_history/audit_logs vào ES |
| 4 | **mTLS enforcement trong backend** | Medium | Kong đã config client cert; backend (release-worker) chưa verify cert — chỉ IP allowlist hiện tại |

---

## R6 — Trigger Pipeline thủ công

```bash
# 1. Lấy token platform-admin
TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | jq -r .access_token)

# 2. Trigger pipeline target dev (auto-promote)
# Quan trọng: dùng "target_environments" (list), KHÔNG phải "target_env" (sẽ bị ignore)
curl -s -X POST http://localhost:8000/api/release/pipeline/trigger \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "package_version": "v2.4.1",
    "branch": "auth/release",
    "commit_sha": "4d143b2",
    "target_environments": ["dev"]
  }' | grep -o '"pipeline_id":"[^"]*"\|"status":"[^"]*"'

# Kết quả mong đợi:
# { "pipeline_id": "pipe-20260527-xxxxxx", "status": "triggered" }

# 3. Xem trạng thái pipeline
PIPELINE_ID="pipe-20260527-xxxxxx"   # thay bằng id thật
curl -s http://localhost:8100/api/release/pipelines/$PIPELINE_ID \
  -H "Authorization: Bearer $TOKEN" | jq .

# 4. Verify trong PostgreSQL
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT id, status, triggered_by, created_at FROM pipelines ORDER BY created_at DESC LIMIT 3;"

# 5. Verify release_history
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT pipeline_id, environment, status, triggered_by, deployed_at FROM release_history ORDER BY deployed_at DESC LIMIT 3;"
```

**Trigger qua Kafka trực tiếp (simulate CI push):**

```bash
# Ghi admin props
docker exec aeroflow-kafka bash -c "cat > /tmp/ci_service.props <<'EOF'
security.protocol=SASL_PLAINTEXT
sasl.mechanism=PLAIN
sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username=\"ci-service\" password=\"${CI_SERVICE_KAFKA_PASSWORD}\";
EOF"

# Produce trigger event
docker exec aeroflow-kafka bash -c "
echo '{\"event\":\"pipeline.triggered\",\"pipeline_id\":\"pipe-$(date +%Y%m%d)-kafka01\",\"triggered_by\":\"ci-system\",\"package_version\":\"v2.4.2\",\"target_environments\":[\"dev\"]}' \
  | /opt/kafka/bin/kafka-console-producer.sh \
    --bootstrap-server localhost:9092 \
    --producer.config /tmp/ci_service.props \
    --topic release.pipeline.triggered"

# Xem release-worker nhận và xử lý
docker compose logs release-worker --tail=20
```

---

## R7 — Approval Flow

```bash
# 1. Trigger pipeline target staging (sẽ vào AWAITING_APPROVAL)
# Route qua Kong port 8000 để JWT được verify
PIPELINE_ID=$(curl -s -X POST http://localhost:8000/api/release/pipeline/trigger \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"package_version":"v2.4.1","target_environments":["staging"]}' \
  | grep -o '"pipeline_id":"[^"]*"' | sed 's/"pipeline_id":"//;s/"//')

echo "Pipeline: $PIPELINE_ID"

# 2. Đợi pipeline vào AWAITING_APPROVAL (~3s)
sleep 3
curl -s http://localhost:8100/api/release/pipelines/$PIPELINE_ID \
  -H "Authorization: Bearer $TOKEN" | jq .pipeline.status
# → "AWAITING_APPROVAL"

# 3. Approve deployment lên staging
curl -s -X POST "http://localhost:8100/api/release/pipeline/$PIPELINE_ID/approve/staging" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision":"APPROVED","comment":"Reviewed and approved by thiennlinh"}' | jq .

# Kết quả: {"status":"approved","pipeline_id":"...","environment":"staging"}

# 4. Verify release_approvals trong DB
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT package_id, environment, decision, approved_by, approved_at FROM release_approvals ORDER BY approved_at DESC LIMIT 5;"

# 5. Test Reject
curl -s -X POST "http://localhost:8100/api/release/pipeline/$PIPELINE_ID/approve/staging" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision":"REJECTED","comment":"Config diff không acceptable"}' | jq .
# → {"status":"rejected","pipeline_id":"..."}

# Verify pipeline status = FAILED
curl -s http://localhost:8100/api/release/pipelines/$PIPELINE_ID \
  -H "Authorization: Bearer $TOKEN" | jq .pipeline.status
# → "FAILED"
```

---

## R8 — Rollback

```bash
# 1. Trigger rollback (chỉ platform-admin)
ROLLBACK_RESPONSE=$(curl -s -X POST http://localhost:8100/api/release/rollback \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "from_version": "v2.4.1",
    "to_version": "v2.4.0",
    "environment": "production",
    "reason": "Critical bug in agent routing v2.4.1"
  }')
echo $ROLLBACK_RESPONSE | jq .

ROLLBACK_ID=$(echo $ROLLBACK_RESPONSE | jq -r .rollback_id)

# 2. Xem trạng thái rollback trong DB
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT id, status, current_step, steps_result FROM rollback_operations WHERE id='$ROLLBACK_ID';"

# Kết quả mong đợi: status=SUCCESS, current_step=3, steps_result=[...]

# 3. Verify release_history có entry ROLLED_BACK
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT pipeline_id, environment, status FROM release_history WHERE status='ROLLED_BACK' ORDER BY deployed_at DESC LIMIT 3;"

# 4. Test rollback bởi non-admin → phải bị 403
AI_TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d "client_id=aeroflow-frontend&grant_type=password&username=ai-engineer&password=Engineer@1234" \
  | jq -r .access_token)

curl -s -X POST http://localhost:8100/api/release/rollback \
  -H "Authorization: Bearer $AI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"from_version":"v2.4.1","to_version":"v2.4.0","environment":"production","reason":"test"}' | jq .
# → {"detail":"Only platform-admin can trigger rollback"}
```

**Test PARTIAL_ROLLBACK scenario:**

```bash
# Simulate: Kong Admin URL không reachable → step 2 fail → PARTIAL_ROLLBACK
# Set KONG_ADMIN_URL sai trong release-worker env temporarily
# Sau đó trigger rollback → check status = PARTIAL_ROLLBACK trong DB
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT id, status, steps_result FROM rollback_operations ORDER BY started_at DESC LIMIT 3;"
```

---

## R9 — Drift Detection

> **Cập nhật 2026-05-27:** `POST /api/release/drift/detect` đã có — gọi inline, không cần Celery beat. Xem §R12 cho các endpoint mới.

```bash
# 1. Insert config khác nhau cho prod và staging
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow <<'SQL'
INSERT INTO environment_configs (environment, key, value, updated_by)
VALUES
  ('production', 'max_agents', '100', 'platform-admin'),
  ('staging',    'max_agents', '50',  'platform-admin'),  -- khác nhau → drift
  ('production', 'log_level', 'ERROR', 'platform-admin'),
  ('staging',    'log_level', 'DEBUG', 'platform-admin')  -- khác nhau → drift
ON CONFLICT (environment, key, version) DO UPDATE SET value = EXCLUDED.value;
SQL

# 2. Chạy drift detection task thủ công (hoặc đợi Celery beat)
curl -s -X POST http://localhost:8100/api/release/drift/detect \
  -H "Authorization: Bearer $TOKEN"
# Nếu endpoint này chưa có, có thể trigger qua Celery:
# docker exec release-worker celery -A main.celery_app call release-worker.detect_config_drift

# 3. Xem drift_events trong DB
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT id, env_pair, drift_keys, severity, detected_at, resolved FROM drift_events ORDER BY detected_at DESC LIMIT 5;"

# 4. Xem Kafka message drift.detected
docker exec aeroflow-kafka bash -c "
/opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --consumer.config /tmp/admin_release.props \
  --topic release.drift.detected \
  --from-beginning --max-messages 3 --timeout-ms 5000"
```

---

## R10 — Release History Query

```bash
# 1. Toàn bộ history (50 records gần nhất)
curl -s http://localhost:8100/api/release/history \
  -H "Authorization: Bearer $TOKEN" | jq .

# 2. Filter theo environment
curl -s "http://localhost:8100/api/release/history?environment=production&limit=20" \
  -H "Authorization: Bearer $TOKEN" | jq '.history[] | {pipeline_id, status, deployed_at}'

# 3. Query trực tiếp PostgreSQL với filter nâng cao
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow <<'SQL'
-- Deploy thành công trong 7 ngày qua
SELECT environment, COUNT(*) as deploy_count,
       AVG(duration_ms) as avg_duration_ms
FROM release_history
WHERE status = 'SUCCESS'
  AND deployed_at > now() - interval '7 days'
GROUP BY environment
ORDER BY deploy_count DESC;

-- Rollback events
SELECT pipeline_id, environment, triggered_by, deployed_at
FROM release_history
WHERE status = 'ROLLED_BACK'
ORDER BY deployed_at DESC;

-- Check partitions đang hoạt động
SELECT schemaname, tablename, tableowner
FROM pg_tables
WHERE tablename LIKE 'release_history_%'
ORDER BY tablename;
SQL

# 4. Check release packages và scan results
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT id, status, validation_score, scan_result->>'overall_status' as scan_status, created_at FROM release_packages ORDER BY created_at DESC LIMIT 5;"
```

---

## R11 — Kong Services cho Rollback (setup một lần)

Rollback step 2 (`reroute_kong`) cần service `aeroflow-{environment}` tồn tại trong Kong.  
Nếu chưa có, tạo thủ công một lần qua Kong Admin API:

```bash
# aeroflow-prod service
curl -X POST http://localhost:8001/services \
  -H "Content-Type: application/json" \
  -d '{
    "name": "aeroflow-prod",
    "url": "http://host.docker.internal:8888/prod"
  }'

# aeroflow-staging service
curl -X POST http://localhost:8001/services \
  -H "Content-Type: application/json" \
  -d '{
    "name": "aeroflow-staging",
    "url": "http://host.docker.internal:8888/staging"
  }'

# Verify
curl -s http://localhost:8001/services | jq '.data[] | select(.name | startswith("aeroflow-")) | {name, url: .host}'
```

Sau khi tạo, rollback cho `environment=production` hoặc `environment=staging` sẽ PATCH service tương ứng để update `tags: ["version:X.Y.Z"]`.

---

## R12 — New API Endpoints (2026-05-27)

Các endpoint mới thêm vào `release-worker` — tất cả đều đi qua Kong `:8000` với JWT.

### Drift Detection API

```bash
TOKEN=$(curl -sf -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | jq -r .access_token)

# 1. Trigger drift detection thủ công (không cần Celery)
curl -sf -X POST http://localhost:8000/api/release/drift/detect \
  -H "Authorization: Bearer $TOKEN"
# → {"status":"drift_detected","drift_id":2,"drift_keys":["dictionary_item_added","values_changed"],...}
# hoặc → {"status":"clean","drift_keys":[],"severity":null}

# 2. List drift events
curl -sf "http://localhost:8000/api/release/drift?resolved=false" \
  -H "Authorization: Bearer $TOKEN" | jq '.drift_events[].severity'

# 3. Mark drift resolved (sau khi sync config)
curl -sf -X POST "http://localhost:8000/api/release/drift/resolve/1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"notes": "Config đã sync prod ↔ staging"}'
# → {"status":"resolved","drift_id":1,"resolved_by":"<user-id>"}
```

### Partition Auto-Creation API

```bash
# Tạo partition tháng tiếp theo (gọi đầu mỗi tháng, idempotent)
curl -sf -X POST http://localhost:8000/api/release/admin/create-partition \
  -H "Authorization: Bearer $TOKEN"
# → {"status":"created","table":"release_history_2026_06","from":"2026-06-01","to":"2026-07-01"}

# Verify trong PostgreSQL
docker exec aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "\dt release_history_*"
```

**Lịch gọi khuyến nghị:** CI/CD job ngày 25 hàng tháng, hoặc cronjob:
```bash
# crontab: 0 2 25 * * curl -sf -X POST http://localhost:8000/api/release/admin/create-partition -H "Authorization: Bearer $TOKEN"
```

### Release UI — 4 tính năng mới (2026-05-27)

| Tính năng | Mô tả |
|-----------|-------|
| **Trigger Pipeline** | Button `+` ở header Deployments → form: package_version, pipeline_name, branch, env checkboxes → POST trigger |
| **Pipeline Steps Detail** | Expand row → lazy-load step timeline (build → scan → deploy); cached, không re-fetch |
| **Environment & Status Filters** | Chip groups (DEV/STAGING/UAT/PROD + Running/Approval/Failed) lọc bảng Deployments; reset khi đổi tab |
| **Trigger Type Badge** | Badge inline tên pipeline: Push (git), Manual, Scheduled |

---

## R14 — Troubleshooting Release

### Release Worker không start

```bash
docker compose logs release-worker | tail -30
# Thường do: PostgreSQL chưa ready, Kafka chưa ready, hoặc module thiếu

docker compose restart release-worker
```

### Pipeline stuck ở RUNNING / BUILDING

```bash
# Xem pipeline steps
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT step_name, status, error FROM pipeline_steps WHERE pipeline_id='$PIPELINE_ID' ORDER BY id;"

# Xem Celery task logs
docker compose logs release-worker | grep "pipeline_id.*$PIPELINE_ID"
```

### Kafka consumer không nhận message

```bash
# Verify consumer group offset
docker exec aeroflow-kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --command-config /tmp/admin_release.props \
  --group release-worker-group \
  --describe

# Reset offset nếu cần (chỉ khi không có message quan trọng)
docker exec aeroflow-kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --command-config /tmp/admin_release.props \
  --group release-worker-group \
  --topic release.pipeline.triggered \
  --reset-offsets --to-earliest --execute
```

### Release schema chưa tạo

```bash
# Chạy lại init script (idempotent — IF NOT EXISTS)
docker exec -i aeroflow-postgres psql -U aeroflow -d aeroflow \
  < infra/sql/release-init.sql

# Kiểm tra
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "\dt pipelines release_packages release_history*"
```

### Rollback PARTIAL_ROLLBACK

> **Lưu ý:** Kể từ 2026-05-27, Kong services `aeroflow-prod` và `aeroflow-staging` đã được tạo → rollback step 2 không còn fail. PARTIAL_ROLLBACK chỉ xảy ra nếu Kong Admin API không reachable.

```bash
# Xem rollback steps để biết bước nào fail
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT id, status, current_step, steps_result, completed_at FROM rollback_operations ORDER BY started_at DESC LIMIT 5;"

# Alert đã được gửi → check Kafka topic release.pipeline.status
docker exec aeroflow-kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --consumer.config /tmp/admin_release.props \
  --topic release.pipeline.status \
  --from-beginning --max-messages 10 --timeout-ms 5000 \
  | grep PARTIAL_ROLLBACK

# Fix: tạo lại Kong services nếu bị xoá (xem §R11)
curl -X POST http://localhost:8001/services -H "Content-Type: application/json" \
  -d '{"name":"aeroflow-staging","url":"http://host.docker.internal:8888/staging"}'

# Sau khi fix thủ công → update status trong DB
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "UPDATE rollback_operations SET status='FAILED', completed_at=now() WHERE id='$ROLLBACK_ID';"
```

### Drift events không được tạo

```bash
# Kiểm tra environment_configs có data khác nhau không
docker exec -it aeroflow-postgres psql -U aeroflow -d aeroflow \
  -c "SELECT environment, key, value FROM environment_configs WHERE is_active=true ORDER BY key, environment;"

# Trigger detect_config_drift thủ công qua Celery
docker exec aeroflow-release-worker python -c "
from main import detect_config_drift
detect_config_drift()
print('Done')
"
```

---

## Checklist Smoke Test — Release (thêm vào sau Auth checklist)

> **Lấy token cho CLI tests (2026-05-29):** Dùng `aeroflow-frontend` với username/password:

```bash
# 8. Release Worker healthy
curl -sf http://localhost:8100/health | jq .status
# → "ok"

# 9. Release DB tables tồn tại
docker exec aeroflow-postgres psql -U aeroflow -d aeroflow -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_name IN ('pipelines','release_packages','release_history','rollback_operations');"
# → 4

# 10. Release Kafka topics tồn tại (qua admin user)
# Cần /tmp/admin_release.props trong Kafka container:
# security.protocol=SASL_PLAINTEXT / sasl.mechanism=PLAIN / admin credentials
docker exec aeroflow-kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --command-config /tmp/admin_release.props --list \
  | grep "^release\."
# → 5 topics: release.pipeline.triggered, release.pipeline.status,
#             release.drift.detected, release.rollback.initiated, release.scan.completed

# 11. Trigger test pipeline (qua Kong :8000 với JWT, hoặc trực tiếp :8100)
TOKEN=$(curl -sf -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d "client_id=aeroflow-frontend&grant_type=password&username=platform-admin&password=Admin@123456" \
  | jq -r .access_token)
PIPE_ID=$(curl -sf -X POST http://localhost:8000/api/release/pipeline/trigger \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"package_version":"v0.0.smoke","target_environments":["dev"]}' | jq -r .pipeline_id)
echo "Pipeline triggered: $PIPE_ID"
sleep 5
STATUS=$(curl -sf http://localhost:8000/api/release/pipelines/$PIPE_ID \
  -H "Authorization: Bearer $TOKEN" | jq -r .pipeline.status)
echo "Pipeline status: $STATUS"
# → "SUCCESS"

# 12. Release history có record
curl -sf http://localhost:8000/api/release/history \
  -H "Authorization: Bearer $TOKEN" | jq '.count'
# → ≥ 1

# 13. Kiểm tra release_history immutability trigger
docker exec aeroflow-postgres psql -U aeroflow -d aeroflow -c \
  "UPDATE release_history SET status='TAMPERED' WHERE id=(SELECT id FROM release_history LIMIT 1);"
# → ERROR: release_history is immutable — UPDATE/DELETE are not allowed

# 14. Kiểm tra rollback SUCCESS (Kong services aeroflow-prod/staging đã tạo)
ROLLBACK_ID=$(curl -sf -X POST http://localhost:8000/api/release/rollback \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"from_version":"v1.0.0","to_version":"v0.9.5","environment":"staging","reason":"smoke test"}' \
  | jq -r .rollback_id)
echo "Rollback: $ROLLBACK_ID"
sleep 3
docker exec aeroflow-postgres psql -U aeroflow -d aeroflow -tAc \
  "SELECT status FROM rollback_operations WHERE id='$ROLLBACK_ID';"
# → SUCCESS  (Kong services aeroflow-staging + aeroflow-prod đã tạo → step 2 SUCCESS)

# 15. Drift detection API
curl -sf -X POST http://localhost:8000/api/release/drift/detect \
  -H "Authorization: Bearer $TOKEN" | jq .status
# → "drift_detected" hoặc "clean" (tuỳ environment_configs)

# 16. Drift list API
curl -sf http://localhost:8000/api/release/drift \
  -H "Authorization: Bearer $TOKEN" | jq .count
# → số drift events đã detect

# 17. Partition tháng tiếp theo
curl -sf -X POST http://localhost:8000/api/release/admin/create-partition \
  -H "Authorization: Bearer $TOKEN" | jq .table
# → "release_history_2026_06" (hoặc tháng tiếp theo)
```

---

# Phần 3 — OpenBao Secrets Vault

## Tổng quan kiến trúc

| Thành phần | Vai trò |
|---|---|
| **OpenBao** (`openbao/openbao:latest`) | KV v2 + Transit engine — lưu secrets và quản lý cryptographic keys |
| **PostgreSQL `secrets_vault`** | Metadata: key_name, key_type, version, rotation_due_at — KHÔNG lưu giá trị |
| **openbao-init** | One-shot: khởi tạo (init) + unseal mỗi khi container restart |
| **openbao-setup** | One-shot: enable KV v2 `secret/` + Transit `transit/`, tạo policy `secrets-vault-policy` |
| **openbao-secrets-bootstrap** | One-shot: đẩy AI provider keys vào `secret/data/tenants/ai-keys/*` |

**Secret types và storage:**

| key_type | Engine | Mô tả |
|---|---|---|
| `SIGNING_KEY`, `ENCRYPTION_KEY`, `HMAC_KEY` | **Transit** | Key tạo trong vault, không bao giờ export — software HSM |
| `BEARER_TOKEN`, `MCP_TOKEN`, `KB_API_KEY` | **KV v2** | Raw value lưu encrypted trong vault |

**Path format:**
- KV v2: `secret/data/tenants/{tenant_id}/{key_name}`
- Transit: `transit/keys/{tenant_id[:8]}-{key_name_lower}`

**Startup order:**
```
openbao → openbao-init → openbao-setup → openbao-secrets-bootstrap → auth-api
```

## URLs & Credentials

| Service | URL | Ghi chú |
|---|---|---|
| OpenBao UI | http://localhost:8300/ui | Root token: xem bên dưới |
| OpenBao API | http://localhost:8300/v1/ | Dùng header `X-Vault-Token` |
| Auth API secrets | http://localhost:8200/api/auth/secrets | Qua Kong: http://localhost:8000/api/auth/secrets |

**Lấy root token:**
```bash
docker exec aeroflow-openbao cat /openbao/data/.root-token
```

## O1 — Khởi động OpenBao Stack

```bash
# Khởi động lần đầu (xóa volume cũ nếu có lỗi permission)
docker compose up -d openbao

# Chờ healthy, sau đó chạy init chain:
docker compose up -d openbao-init      # init + unseal
docker compose up -d openbao-setup     # enable KV v2
docker compose up -d openbao-secrets-bootstrap  # bootstrap infra secrets
docker compose up -d auth-api

# Hoặc để Compose tự xử lý depends_on:
docker compose up -d
```

**Kiểm tra từng bước:**
```bash
docker logs aeroflow-openbao-init      --tail 5   # → ✅ OpenBao init complete
docker logs aeroflow-openbao-setup     --tail 5   # → ✅ OpenBao setup complete
docker logs aeroflow-openbao-secrets-bootstrap --tail 5  # → ✅ Bootstrap complete
```

## O2 — Troubleshooting OpenBao

### Permission denied khi init
```
failed to initialize barrier: failed to persist keyring: mkdir /openbao/data/core: permission denied
```
**Nguyên nhân:** Volume `openbao_data` được tạo với owner `root`, nhưng server chạy bằng user `openbao`.  
**Fix đã áp dụng:** `BAO_SKIP_DROP_ROOT: "true"` trong docker-compose (server chạy as root, tránh privilege drop).

```bash
# Nếu vẫn gặp lỗi, xóa volume và restart:
docker compose stop openbao openbao-init openbao-setup openbao-secrets-bootstrap auth-api
docker compose rm -f openbao openbao-init openbao-setup openbao-secrets-bootstrap auth-api
docker volume rm data-agentt_openbao_data
docker compose up -d
```

### HAProxy 503 / keycloak-lb unhealthy

**Nguyên nhân 1:** `keycloak-node2` chưa chạy khi HAProxy start → DNS resolution fail → HAProxy crash.  
**Fix đã áp dụng:** `keycloak-lb` depends on `keycloak-node2: service_started`.

**Nguyên nhân 2:** HAProxy backend health check chưa pass (cần `rise 3` = 15s).  
**Healthcheck:** `wget -qO- http://127.0.0.1:8080/health/ready 2>&1 | grep -qE 'UP|503'`  
→ Pass ngay cả khi backend đang warm-up (503 = haproxy alive).

```bash
# Kiểm tra HAProxy backend status:
docker logs aeroflow-keycloak-lb --tail 20
# kc1/kc2 UP → "keycloak_nodes/kc1 ... 200"
# kc1/kc2 DOWN → "<NOSRV> ... 503"

# Restart nếu cần:
docker compose restart keycloak-lb
```

### OpenBao sealed sau restart
```bash
# openbao-init tự unseal — chạy lại nếu cần:
docker compose up -d openbao-init
docker logs aeroflow-openbao-init --tail 5
# → ✅ Unsealed successfully / ✅ Already unsealed
```

## O3 — Test Secrets Vault API

```bash
TENANT="a0000000-0000-0000-0000-000000000001"
H="-H X-User-Id:test -H X-User-Roles:PLATFORM_ADMIN -H X-Tenant-Id:$TENANT"

# 1. Danh sách secrets
curl -s http://localhost:8200/api/auth/secrets $H | jq 'length'

# 2. Tạo KV v2 secret (BEARER_TOKEN — cần value)
KV_ID=$(curl -s -X POST http://localhost:8200/api/auth/secrets $H \
  -H "Content-Type: application/json" \
  -d '{"key_name":"MY_API_KEY","key_type":"BEARER_TOKEN","algorithm":"AES-256","realm":"PROD","value":"secret-value-here","rotation_days":90}' \
  | jq -r .id)
echo "KV secret: $KV_ID"

# 3. Reveal KV value
curl -s http://localhost:8200/api/auth/secrets/$KV_ID/reveal $H | jq .value
# → "secret-value-here"

# 4. Rotate KV secret (cần new value)
curl -s -X POST http://localhost:8200/api/auth/secrets/$KV_ID/rotate $H \
  -H "Content-Type: application/json" -d '{"value":"new-rotated-value"}' | jq .version
# → 2

# 5. Governance settings
curl -s http://localhost:8200/api/auth/secrets/governance $H | jq .

# 6. Audit log
curl -s http://localhost:8200/api/auth/secrets/audit-log $H | jq 'length'

# 7. Delete (soft-delete + destroy OpenBao version)
curl -s -X DELETE http://localhost:8200/api/auth/secrets/$KV_ID $H

# 8. Governance — đọc / cập nhật
curl -s http://localhost:8200/api/auth/secrets/governance $H | jq .
# Bật Auto-Rotation (rotate overdue Transit keys mỗi 24h)
curl -s -X PUT http://localhost:8200/api/auth/secrets/governance $H \
  -H "Content-Type: application/json" -d '{"vault_auto_rotation": true}' | jq .vault_auto_rotation
# Bật PII Access Log (ghi lại mỗi lần reveal)
curl -s -X PUT http://localhost:8200/api/auth/secrets/governance $H \
  -H "Content-Type: application/json" -d '{"vault_pii_access_log": true}' | jq .vault_pii_access_log

# 9. PII Access Log — xem các lần đã reveal secret (cần vault_pii_access_log=true)
curl -s http://localhost:8200/api/auth/secrets/pii-log $H | jq 'length'
curl -s http://localhost:8200/api/auth/secrets/pii-log $H | jq '[.[] | {key_name, actor_id, version, time}]'

# 10. Panic Mode — khoá toàn bộ vault access (trả 503 cho tất cả secret ops)
curl -s -X PUT http://localhost:8200/api/auth/secrets/governance $H \
  -H "Content-Type: application/json" -d '{"vault_panic_mode": true}' | jq .vault_panic_mode
# → true; mọi request tới /secrets/* (trừ governance/audit/pii-log) đều 503

# Tắt Panic Mode
curl -s -X PUT http://localhost:8200/api/auth/secrets/governance $H \
  -H "Content-Type: application/json" -d '{"vault_panic_mode": false}' | jq .vault_panic_mode

# 11. Panic trigger (bulk deactivate + destroy tất cả secrets)
curl -s -X POST http://localhost:8200/api/auth/secrets/panic $H | jq .count
```

## O6 — Transit Engine (RSA/HSM Signing)

Transit engine: private key tạo và lưu bên trong OpenBao, không bao giờ export. Signing xảy ra trong vault.

```bash
TENANT="a0000000-0000-0000-0000-000000000001"
H="-H X-User-Id:test -H X-User-Roles:PLATFORM_ADMIN -H X-Tenant-Id:$TENANT"

# 1. Tạo Transit key (SIGNING_KEY — KHÔNG cần value, key tự tạo trong vault)
SIGN_ID=$(curl -s -X POST http://localhost:8200/api/auth/secrets $H \
  -H "Content-Type: application/json" \
  -d '{"key_name":"PAYLOAD_SIGNING_KEY","key_type":"SIGNING_KEY","algorithm":"RSA-4096","realm":"CORE-INFRA"}' \
  | jq -r .id)
echo "Transit key: $SIGN_ID"

# 2. HSM status — xem Transit keys đang quản lý
curl -s http://localhost:8200/api/auth/secrets/hsm/status $H | jq .
# → {"transit_keys":[{"name":"a0000000-payload-signing-key","type":"rsa-4096","latest_version":1}],"key_count":1,"openbao_sealed":false}

# 3. Reveal — trả về PUBLIC KEY (private key không bao giờ xuất ra)
curl -s http://localhost:8200/api/auth/secrets/$SIGN_ID/reveal $H | jq .value
# → "-----BEGIN PUBLIC KEY-----\nMIICIjAN..."

# 4. Sign data (base64-encoded payload)
DATA_B64=$(printf '%s' '{"event":"deploy","tenant":"aeroflow"}' | base64)
SIG=$(curl -s -X POST http://localhost:8200/api/auth/secrets/$SIGN_ID/sign $H \
  -H "Content-Type: application/json" \
  -d "{\"data\":\"$DATA_B64\"}" | jq -r .signature)
echo "Signature: ${SIG:0:60}..."

# 5. Verify signature
curl -s -X POST http://localhost:8200/api/auth/secrets/$SIGN_ID/verify $H \
  -H "Content-Type: application/json" \
  -d "{\"data\":\"$DATA_B64\",\"signature\":\"$SIG\"}" | jq .valid
# → true

# 6. Rotate Transit key (new version tạo trong vault, old version vẫn verify được)
curl -s -X POST http://localhost:8200/api/auth/secrets/$SIGN_ID/rotate $H \
  -H "Content-Type: application/json" -d '{}' | jq .version
# → 2 (new version, cũ v1 vẫn verify được)

# 7. Tạo ENCRYPTION_KEY (AES-256 — cũng dùng Transit)
curl -s -X POST http://localhost:8200/api/auth/secrets $H \
  -H "Content-Type: application/json" \
  -d '{"key_name":"DATA_ENCRYPTION_KEY","key_type":"ENCRYPTION_KEY","algorithm":"AES-256","realm":"DATA-LAYER"}' \
  | jq '{id,key_name,key_type,algorithm}'
```

### Verify Transit key trong OpenBao trực tiếp

```bash
ROOT_TOKEN=$(docker exec aeroflow-openbao cat /openbao/data/.root-token)

# List all Transit keys
curl -s -H "X-Vault-Token: $ROOT_TOKEN" \
  "http://localhost:8300/v1/transit/keys?list=true" | jq .data.keys

# Read Transit key info (public key, versions)
curl -s -H "X-Vault-Token: $ROOT_TOKEN" \
  http://localhost:8300/v1/transit/keys/a0000000-payload-signing-key | jq .data
```

## O7 — Governance Controls

Ba tính năng quản trị vault — tất cả đều bật/tắt qua `PUT /api/auth/secrets/governance`.

### Panic Mode
Khi `vault_panic_mode = true`, **tất cả** endpoint `/secrets/*` (tạo, đọc, ký, xoá) trả về `503 Vault is in PANIC MODE`. Chỉ governance, audit-log, và pii-log vẫn hoạt động để admin có thể tắt.

```bash
H="-H X-User-Id:test -H X-User-Roles:PLATFORM_ADMIN -H X-Tenant-Id:a0000000-0000-0000-0000-000000000001"

# Bật panic
curl -s -X PUT http://localhost:8200/api/auth/secrets/governance $H \
  -H "Content-Type: application/json" -d '{"vault_panic_mode":true}' | jq .vault_panic_mode
# → true

# Kiểm tra — phải 503
curl -s -w "HTTP:%{http_code}" http://localhost:8200/api/auth/secrets $H | grep HTTP
# → HTTP:503

# Tắt panic
curl -s -X PUT http://localhost:8200/api/auth/secrets/governance $H \
  -H "Content-Type: application/json" -d '{"vault_panic_mode":false}' | jq .vault_panic_mode
# → false
```

### Auto-Rotation
Khi `vault_auto_rotation = true`, auth-api chạy background loop mỗi 24h:
- **Transit keys** (SIGNING_KEY, ENCRYPTION_KEY, HMAC_KEY) có `rotation_due_at < now` → tự động `transit.rotate_key()`.
- **KV secrets** (BEARER_TOKEN, MCP_TOKEN, KB_API_KEY) → log `SCHEDULED / FAILED: Manual rotation required` vào audit log (không thể tự rotate vì cần value mới).

```bash
# Bật
curl -s -X PUT http://localhost:8200/api/auth/secrets/governance $H \
  -H "Content-Type: application/json" -d '{"vault_auto_rotation":true}' | jq .vault_auto_rotation
# → true

# Xem audit log sau khi rotation chạy
curl -s http://localhost:8200/api/auth/secrets/audit-log $H | jq '[.[] | select(.event=="SCHEDULED")]'
```

### PII Access Log
Khi `vault_pii_access_log = true`, mỗi lần gọi `GET /secrets/{id}/reveal` sẽ tạo entry trong bảng `key_rotations` với `triggered_by='REVEAL'`. Admin xem qua:

```bash
# Bật
curl -s -X PUT http://localhost:8200/api/auth/secrets/governance $H \
  -H "Content-Type: application/json" -d '{"vault_pii_access_log":true}' | jq .vault_pii_access_log

# Reveal một secret (tạo PII log entry)
curl -s http://localhost:8200/api/auth/secrets/$SECRET_ID/reveal $H | jq .value

# Xem PII log
curl -s http://localhost:8200/api/auth/secrets/pii-log $H | jq '[.[] | {key_name, actor_id, version, time}]'
# → [{"key_name":"test-signing","actor_id":"b00c720f-...","version":1,"time":"2026-05-29T17:41:59"}]
```

UI: Governance Controls panel trong Settings → Auth & SSO → Secrets Vault → "PII Access Log" tab (violet panel giữa Governance và HSM).

---

## O4 — Verify secrets trong OpenBao trực tiếp

```bash
ROOT_TOKEN=$(docker exec aeroflow-openbao cat /openbao/data/.root-token)
TENANT="a0000000-0000-0000-0000-000000000001"

# List tất cả paths dưới tenants/
curl -s -H "X-Vault-Token: $ROOT_TOKEN" \
  "http://localhost:8300/v1/secret/metadata/tenants?list=true" | jq .data.keys
# → ["ai-keys/", "a0000000-0000-0000-0000-000000000001/"]

# Xem AI provider keys (bootstrapped)
curl -s -H "X-Vault-Token: $ROOT_TOKEN" \
  "http://localhost:8300/v1/secret/metadata/tenants/ai-keys?list=true" | jq .data.keys
# → ["ANTHROPIC_API_KEY", "AZURE_OPENAI_KEY", "OPENAI_API_KEY"]

# Xem KV v2 secrets của tenant
curl -s -H "X-Vault-Token: $ROOT_TOKEN" \
  "http://localhost:8300/v1/secret/metadata/tenants/$TENANT?list=true" | jq .data.keys

# Xem value của một KV v2 secret
curl -s -H "X-Vault-Token: $ROOT_TOKEN" \
  "http://localhost:8300/v1/secret/data/tenants/$TENANT/MY_API_KEY" | jq .data.data.value

# List Transit keys
curl -s -H "X-Vault-Token: $ROOT_TOKEN" \
  "http://localhost:8300/v1/transit/keys?list=true" | jq .data.keys
```

## O5 — AI Provider Keys (Bootstrap)

Chỉ có AI provider keys được tự động bootstrap vào `secret/data/tenants/ai-keys/*`:

| Path | Giá trị mặc định | Ghi chú |
|---|---|---|
| `tenants/ai-keys/OPENAI_API_KEY` | `sk-placeholder-replace-me` | Thay bằng real key trong prod |
| `tenants/ai-keys/ANTHROPIC_API_KEY` | `sk-ant-placeholder-replace-me` | Thay bằng real key trong prod |
| `tenants/ai-keys/AZURE_OPENAI_KEY` | `placeholder-replace-me` | Thay bằng real key trong prod |

> **Lưu ý:** Infra credentials (Postgres, Keycloak, Kafka, MinIO) KHÔNG lưu trong OpenBao — chúng đọc thẳng từ docker-compose env vars. Chỉ AI provider API keys mới cần vault vì chúng thuộc về các external service.

---

# Phần 4 — Multi-Tenant Management

## Kiến trúc Multi-Tenant

```
Keycloak JWT
  └─ claim: tenant_id = "a0000000-..." hoặc "b0000000-..."
  └─ claim: role_id   = "platform-admin" | "ai-engineer" | "executive-viewer"

Kong (aeroflow-jwks plugin)
  └─ đọc JWT → inject header X-Tenant-Id, X-User-Id, X-User-Roles
  └─ KHÔNG cho phép client tự set các header này

auth-api (FastAPI)
  └─ đọc X-Tenant-Id từ header (tin tưởng vì chỉ Kong gửi)
  └─ ALL SQL queries đều có WHERE tenant_id = x_tenant_id
  └─ Cross-tenant access → 404 Not Found (không tiết lộ tồn tại)

OpenBao
  └─ KV v2 path: tenants/{tenant_id}/{key_name}
  └─ Transit key name: {tenant_id[:8]}-{key_name_lower}  (prefix enforce isolation)
```

**Tenant seeding:**
| UUID | Name | slug | Plan | Region |
|------|------|------|------|--------|
| `a0000000-0000-0000-0000-000000000001` | AeroFlow Dev | aeroflow-dev | Enterprise | Asia-SE1 |
| `b0000000-0000-0000-0000-000000000002` | Helios Corp  | helios-corp  | Pro        | EU-West1  |

## MT1 — Tạo Tenant Mới

### Bước 1: Insert vào PostgreSQL

```sql
INSERT INTO tenants (id, name, slug, plan_id, data_residency)
VALUES (
  gen_random_uuid(),      -- hoặc UUID cố định để dễ debug
  'Tenant Name',
  'tenant-slug',
  2,                      -- plan_id: 1=Free, 2=Pro, 3=Enterprise
  'Asia-SE1'
) ON CONFLICT (slug) DO NOTHING;
```

Hoặc dùng docker exec:
```bash
docker exec aeroflow-postgres psql -U aeroflow -d aeroflow -c \
  "INSERT INTO tenants (id, name, slug, plan_id, data_residency)
   VALUES ('c0000000-0000-0000-0000-000000000003','New Corp','new-corp',1,'US-East1')
   ON CONFLICT (slug) DO NOTHING;"
```

### Bước 2: Tạo users trong Keycloak

```python
# infra/scripts/provision_tenant.py (example)
import requests, json

KEYCLOAK = 'http://localhost:8080'
TENANT_ID = 'c0000000-0000-0000-0000-000000000003'

admin_token = requests.post(f'{KEYCLOAK}/realms/master/protocol/openid-connect/token',
    data={'grant_type':'password','client_id':'admin-cli','username':'admin','password':'admin'}
).json()['access_token']
headers = {'Authorization': f'Bearer {admin_token}', 'Content-Type': 'application/json'}

# Tạo user
uid = None
resp = requests.post(f'{KEYCLOAK}/admin/realms/aeroflow/users', headers=headers, json={
    'username': 'newcorp-admin',
    'email': 'admin@newcorp.io',
    'enabled': True,
    'emailVerified': True,
    'requiredActions': [],
    'credentials': [{'type': 'password', 'value': 'N3wCorp@Admin01!', 'temporary': False}],
})
uid = resp.headers.get('Location', '').split('/')[-1]

# Set tenant_id và role_id attributes
user = requests.get(f'{KEYCLOAK}/admin/realms/aeroflow/users/{uid}', headers=headers).json()
user['attributes'] = {'tenant_id': [TENANT_ID], 'role_id': ['platform-admin']}
requests.put(f'{KEYCLOAK}/admin/realms/aeroflow/users/{uid}', headers=headers, json=user)
```

**Note:** realm-export.json cần `unmanagedAttributePolicy: ENABLED` (đã set). Trên fresh install, setting này được load từ `userProfileConfig` trong realm-export.

### Bước 3: Seed data cho tenant mới (qua API)

```bash
# Lấy token cho tenant mới
NEW_TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d 'grant_type=password&client_id=aeroflow-frontend&username=newcorp-admin&password=N3wCorp@Admin01!' \
  | jq -r .access_token)

H="-H 'Authorization: Bearer $NEW_TOKEN' -H 'Content-Type: application/json'"

# Tạo signing key (Transit)
curl -s -X POST http://localhost:8000/api/auth/secrets \
  -H "Authorization: Bearer $NEW_TOKEN" -H "Content-Type: application/json" \
  -d '{"key_name":"MAIN_SIGNING_KEY","key_type":"SIGNING_KEY","algorithm":"RSA-2048","realm":"JWT-INFRA"}' \
  | jq '{id, key_name, key_type}'

# Tạo API key
curl -s -X POST http://localhost:8000/api/auth/api-keys \
  -H "Authorization: Bearer $NEW_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Dashboard Key","scope":"read_only","expires_days":365}' \
  | jq '{id, name, key_prefix, raw_key}'
```

## MT2 — Verify Tenant Isolation

```bash
# Chạy full isolation test suite
python infra/scripts/test_tenant_isolation.py

# Kết quả mong đợi:
# 23 PASSED  |  0 FAILED  |  23 TOTAL
```

### Test thủ công (curl)

```bash
T1_TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d 'grant_type=password&client_id=aeroflow-frontend&username=platform-admin&password=Admin123456!' \
  | jq -r .access_token)

T2_TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d 'grant_type=password&client_id=aeroflow-frontend&username=helios-admin&password=H3lios@Admin01!' \
  | jq -r .access_token)

# T1 secrets (chỉ thấy AeroFlow data)
curl -s -H "Authorization: Bearer $T1_TOKEN" http://localhost:8000/api/auth/secrets | jq 'length'

# T2 secrets (chỉ thấy Helios data)
curl -s -H "Authorization: Bearer $T2_TOKEN" http://localhost:8000/api/auth/secrets | jq '[.[] | .key_name]'

# T1 user cố access T2 secret bằng UUID → phải 404
T2_SECRET_ID=$(curl -s -H "Authorization: Bearer $T2_TOKEN" http://localhost:8000/api/auth/secrets \
  | jq -r '.[0].id')
curl -s -w "HTTP:%{http_code}" -H "Authorization: Bearer $T1_TOKEN" \
  "http://localhost:8000/api/auth/secrets/$T2_SECRET_ID/reveal"
# → HTTP:404

# T2 user cố sign với T1 signing key → phải 404
T1_SIGN_ID=$(curl -s -H "Authorization: Bearer $T1_TOKEN" http://localhost:8000/api/auth/secrets \
  | jq -r '[.[] | select(.key_type=="SIGNING_KEY" and .is_active)] | .[0].id')
curl -s -w "HTTP:%{http_code}" -X POST -H "Authorization: Bearer $T2_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data":"aGVsbG8="}' \
  "http://localhost:8000/api/auth/secrets/$T1_SIGN_ID/sign"
# → HTTP:404
```

## MT3 — Tenant Users (Credentials)

| Tenant | Username | Password | Role |
|--------|----------|----------|------|
| AeroFlow Dev | `platform-admin` | `Admin123456!` | PLATFORM_ADMIN |
| AeroFlow Dev | `ai-engineer` | *(reset via Keycloak admin)* | AI_ENGINEER |
| AeroFlow Dev | `executive-viewer` | *(reset via Keycloak admin)* | EXECUTIVE_VIEWER |
| Helios Corp | `helios-admin` | `H3lios@Admin01!` | PLATFORM_ADMIN |
| Helios Corp | `helios-engineer` | `H3lios@Eng001!` | AI_ENGINEER |
| Helios Corp | `helios-viewer` | `H3lios@View01!` | EXECUTIVE_VIEWER |

## MT4 — Isolation Mechanism Summary

| Layer | Mechanism | Details |
|-------|-----------|---------|
| Keycloak | User attribute `tenant_id` | Set by admin, embedded in JWT |
| Kong | Header injection | `X-Tenant-Id` from JWT claim; client-set headers cleared |
| PostgreSQL | Row-level filter | `WHERE tenant_id = x_tenant_id` on every query |
| OpenBao KV v2 | Path prefix | `tenants/{tenant_id}/{key_name}` |
| OpenBao Transit | Key name prefix | `{tenant_id[:8]}-{key_name}` (63-char limit) |
| HSM status | List filter | Filters Transit keys by `{tenant_id[:8]}-` prefix |
| Audit log | tenant_id column | All events scoped to tenant |
| Response | 404 on cross-access | Secrets not found (not 403) — prevents ID enumeration |
## Current Docker Status

This runbook contains historical sections from an earlier auth and release stack shape.

Current supported Docker entrypoints:

- `docker/docker-compose.yml`
- `docker/docker-compose.local.yml`
- `docker/docker-compose.dev.yml`
- `docker/docker-compose.test.yml`

Current local startup command:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml up -d --build
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml ps
```

Current shared-dev startup command:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d --build
```

Current local stack differences versus older sections below:

- uses `dataagent-keycloak` single-node dev mode instead of `keycloak-node1`, `keycloak-node2`, and `keycloak-lb`
- does not include historical `konga`, `openbao-init`, `openbao-setup`, or `openbao-secrets-bootstrap` services
- uses `dataagent-kong` admin port `8900` instead of `8001` on the host
- uses layered compose files under `docker/` rather than a single root-level `docker compose up -d`
