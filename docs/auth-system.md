# Auth & Gateway — Mô tả Hệ thống

> Keycloak HA + Kong + Kafka Audit Pipeline + PostgreSQL + Microsoft SSO  
> Thiết kế: Linh Thien (thiennlinh) — Cập nhật: 2026-06-09

---

## Kiến trúc tổng thể

```
Browser / Frontend (Vite :5173)
  │
  │  (1) PKCE S256 Authorization Code Flow → Keycloak HA (:8080)
  │  (2) Nhận JWT RS256 (access_token TTL 15 phút)
  │  (3) updateToken(30) — tự refresh khi còn < 30s
  │
  ▼
Kong Gateway (:8000)  ←  aeroflow-jwks plugin (custom Lua)
  │  (4) Fetch JWKS từ Keycloak, cache 300s trong kong.cache
  │  (5) Verify RS256 signature — reject nếu invalid/expired/missing
  │  (6) Inject headers: X-User-Id, X-User-Email, X-User-Roles, X-Kong-Verified
  │  (7) IP allowlist + rate-limit (300 req/min) + Correlation-ID
  ▼
Backend FastAPI (:8888)
  │  Chỉ đọc X-User-* headers — KHÔNG verify JWT lại
  │  mTLS: kiểm tra Kong client cert qua CA (khi đã có backend service)
  ▼
PostgreSQL (:5432)
  ├── schema public   → platform tables (tenants, members, api_keys, audit_logs)
  ├── schema keycloak → Keycloak internal storage (shared 2 nodes)
  └── schema kong     → Kong config storage

─────────────────────────────────────────────────────
Keycloak HA Cluster:
  keycloak-node1 (:8081) ─┐  Infinispan session sync
  keycloak-node2 (:8082) ─┤  JGroups TCPPING port 7600
                           └─ PostgreSQL (backing store chung)
  HAProxy (:8080) → Round-robin, health-check /health/ready

─────────────────────────────────────────────────────
Kafka Audit Pipeline (push-based, at-least-once):
  Keycloak SPI JAR (aeroflow-kafka EventListenerProvider)
    → Kafka topic: audit.auth.events (SASL/PLAIN dev, 3 partitions)
    → audit-consumer service
    → PostgreSQL audit_logs (partitioned RANGE by month)
```

---

## Danh sách Services

| Container | Image | Port(s) | Vai trò |
|---|---|---|---|
| `aeroflow-postgres` | postgres:16-alpine | 5432 | Database chung cho Keycloak/Kong/app |
| `aeroflow-keycloak-1` | Dockerfile.keycloak | 8081→8080 | Keycloak node 1 (Active), import realm |
| `aeroflow-keycloak-2` | Dockerfile.keycloak | 8082→8080 | Keycloak node 2 (Active), join cluster |
| `aeroflow-keycloak-lb` | haproxy:2.8-alpine | 8080 | HAProxy LB trước 2 Keycloak nodes |
| `aeroflow-kong-migration` | `infra/kong/Dockerfile` | — | Kong DB migration (run-once) |
| `aeroflow-kong` | `infra/kong/Dockerfile` | 8000, 8001 | Kong Gateway + Admin API |
| `aeroflow-konga` | pantsel/konga | 1337 | Kong Admin UI |
| `aeroflow-kafka` | apache/kafka:3.7.0 | 9092 | Kafka KRaft, SASL/PLAIN (dev) |
| `aeroflow-audit-consumer` | services/audit-consumer | — | Kafka → PostgreSQL audit_logs |
| `aeroflow-frontend` | `docker/Dockerfile.dev` | 5173→3000 | Vite dev server |

---

## Microsoft SSO — Cấu hình

| Thành phần | Chi tiết |
|---|---|
| **Provider** | Microsoft Entra ID (Azure AD) qua OIDC |
| **Keycloak IDP alias** | `microsoft` |
| **Protocol** | OIDC + PKCE S256 |
| **Tenant** | `consumers` (Personal Microsoft accounts) |
| **Authorization URL** | `https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize` |
| **Token URL** | `https://login.microsoftonline.com/consumers/oauth2/v2.0/token` |
| **IDP mapper** | `set-idp-provider-attr` — set user attribute `idp_provider=microsoft` |
| **Protocol mapper** | `idp-provider-claim` — emit `idp_provider` claim trong JWT |
| **First Login Flow** | `first broker login` (default) — auto-create user profile |
| **Approval DB table** | `microsoft_sso_approvals` (status: pending/approved/rejected) |
| **Admin UI** | Settings → Auth & SSO → tab "MS Approvals" |
| **Redirect URI** | `http://localhost:8080/realms/aeroflow/broker/microsoft/endpoint` |

**Env vars cần thiết:**
```
MS_SSO_CLIENT_ID      = Azure App Registration Client ID
MS_SSO_CLIENT_SECRET  = Azure App Registration Client Secret
MS_SSO_TENANT_ID      = consumers | <tenant-id>
```

**Setup:** `keycloak-setup.py` tự động cấu hình IDP + mapper khi 3 biến trên được set.

---

## Thành phần tùy chỉnh

### 1. Kong Plugin — `aeroflow-jwks`

**Vị trí:** `infra/kong/plugins/aeroflow-jwks/`

Lua plugin thay thế hoàn toàn JWT built-in plugin + pre-function + jwks-refresher service cũ.

| File | Vai trò |
|---|---|
| `handler.lua` | RS256 verify, JWKS fetch, header inject |
| `schema.lua` | Config fields: jwks_uri, issuer, jwks_refresh_interval |

**Cách hoạt động:**
1. Mỗi request, đọc `Authorization: Bearer <token>`
2. Decode header JWT → lấy `kid`
3. Tìm key trong `kong.cache` (TTL `jwks_refresh_interval` giây, default 300s)
4. Nếu miss → fetch JWKS endpoint, parse RSA key, lưu cache
5. Verify RS256 signature (`resty.openssl.pkey` + `RSASSA_PKCS1_PADDING`)
6. Kiểm tra `exp`, `iss` (nếu config `issuer` được đặt)
7. Nếu `kid` thay đổi → invalidate cache ngay, refetch
8. Inject headers: `X-User-Id` (sub), `X-User-Email`, `X-User-Roles` (realm_access.roles), `X-Kong-Verified: true`

### 2. Keycloak SPI — `aeroflow-kafka`

**Vị trí:** `infra/keycloak/spi/`

Java `EventListenerProvider` build bằng Maven multi-stage Docker, mount vào Keycloak image.

| File | Vai trò |
|---|---|
| `pom.xml` | Dependencies: keycloak-server-spi, kafka-clients (shaded), jackson-databind (shaded) |
| `KafkaEventListenerProvider.java` | `onEvent(Event)` → serialize → Kafka.produce() + flush |
| `KafkaEventListenerProviderFactory.java` | `getId()=aeroflow-kafka`, init KafkaProducer với SASL/SCRAM |
| `META-INF/services/...Factory` | Service loader registration |

**Cách hoạt động:**
- Keycloak gọi `onEvent()` cho mỗi event (LOGIN, LOGOUT, ERROR, ...)
- SPI serialize event thành JSON: `type, realmId, clientId, userId, sessionId, ipAddress, time, details, error`
- Gửi trực tiếp vào Kafka topic `audit.auth.events` (push-based, không poll)
- `KafkaProducer.flush()` sau mỗi event — đảm bảo at-least-once delivery

---

## Auth Flow Chi Tiết

### Login Flow (PKCE S256 — Local Keycloak user)

```
1. Frontend tạo code_verifier (random 32 bytes) + code_challenge = SHA256(code_verifier)
2. Redirect → Keycloak /authorize?code_challenge=...&code_challenge_method=S256
3. User đăng nhập → Keycloak trả authorization_code
4. Frontend POST /token với code + code_verifier → Keycloak verify → trả JWT
5. Frontend lưu JWT trong memory (không localStorage)
6. updateToken(30) chạy mỗi 5s — nếu exp < now+30s → gọi /token?grant_type=refresh_token
7. AuthProvider gọi GET /api/auth/me → status=active → render app
```

### Microsoft SSO Flow (First Login — Admin Approval Required)

```
1. User click "Sign in with Microsoft" trên Keycloak login page
2. Keycloak redirect → Microsoft /authorize (OIDC PKCE S256)
3. User xác thực với Microsoft account
4. Microsoft redirect về Keycloak /broker/microsoft/endpoint với code
5. Keycloak exchange code → Microsoft token → tạo user trong realm aeroflow
   - User attribute idp_provider=microsoft được set bởi IDP mapper
6. Keycloak trả JWT cho frontend (idp_provider claim có trong token)
7. AuthProvider gọi GET /api/auth/me:
   a. Backend check DB: chưa có bản ghi → gọi Keycloak Admin API federated-identity
   b. Xác nhận provider=microsoft → INSERT pending record → disable Keycloak account
   c. Trả về {status: "pending_approval"}
8. AuthProvider render màn hình PendingApproval thay vì app
9. Admin vào Settings → Auth & SSO → MS Approvals → Approve user
10. Backend UPDATE status=approved → enable Keycloak account
11. User login lại → GET /api/auth/me → status=active → vào app bình thường
```

### Microsoft SSO Flow (Subsequent Login)

```
1-6. Giống first login (Keycloak xác thực qua Microsoft)
7. AuthProvider gọi GET /api/auth/me
   a. Backend check DB: bản ghi tồn tại, status=approved
   b. Trả về {status: "active"}
8. AuthProvider render app bình thường (không cần admin review)
```

### JWT Verification Flow (Kong)

```
1. Frontend gửi GET /api/... với header Authorization: Bearer <JWT>
2. Kong aeroflow-jwks plugin intercept
3. Decode JWT header → kid
4. kong.cache.get(kid) → HIT: dùng cached PEM key
                       → MISS: fetch JWKS → parse JWK → build PEM → cache
5. pkey:verify(signature, header.payload, "sha256", RSASSA_PKCS1_PADDING)
6. Verify exp > now, iss == expected issuer
7. PASS → inject X-User-* headers → forward to backend
8. FAIL → trả 401 Unauthorized
```

### Audit Pipeline Flow

```
1. Event xảy ra trong Keycloak (LOGIN, LOGOUT, ERROR, ...)
2. SPI aeroflow-kafka.onEvent() được gọi synchronously
3. Serialize → Kafka.send("audit.auth.events", json_payload)
4. Kafka lưu vào partition (3 partitions, retention 30 ngày)
5. audit-consumer poll Kafka (SASL/SCRAM, group=audit-consumer-group)
6. Parse JSON → INSERT INTO audit_logs (...) VALUES (...)
7. PostgreSQL COMMIT → Kafka offset commit
```

---

## Keycloak HA

| Thuộc tính | Giá trị |
|---|---|
| Mode | Active/Active |
| Số nodes | 2 (keycloak-node1, keycloak-node2) |
| Session sync | Infinispan distributed cache |
| Discovery | JGroups TCPPING port 7600 |
| Load balancer | HAProxy round-robin |
| Health check | `GET /health/ready` mỗi 15s |
| Failover | HAProxy remove node khỏi pool khi /health/ready fail |
| Backing store | PostgreSQL schema keycloak (chung) |

**Node1 vs Node2:**
- Node1: `--import-realm` — import realm-export.json lần đầu boot
- Node2: không import (realm đã có trong DB) — join cluster sau khi node1 healthy

---

## Kafka Security

| Thuộc tính | Giá trị |
|---|---|
| Transport | SASL_PLAINTEXT (dev) → SASL_SSL (prod) |
| Mechanism | PLAIN (dev) — upgrade to SCRAM-SHA-256 for prod |
| Authorizer | `StandardAuthorizer` |
| Default deny | `allow.everyone.if.no.acl.found=false` |
| Super user | `User:admin` (inter-broker) |

| User | Password | ACL |
|---|---|---|
| `admin` | see `KAFKA_ADMIN_PASSWORD` in `.env` | Super user — inter-broker, health check |
| `audit-bridge` *(legacy)* | see `AUDIT_BRIDGE_KAFKA_PASSWORD` in `.env` | Write + Describe on `audit.auth.events` |
| `audit-consumer` | see `AUDIT_CONSUMER_KAFKA_PASSWORD` in `.env` | Read + Describe on `audit.auth.events`, Read on group `audit-consumer-group` |

> `audit-bridge` user còn trong Kafka config cho tương thích, nhưng service không còn chạy. Keycloak SPI dùng credentials riêng từ env var `KAFKA_SASL_USERNAME=audit-bridge`.

---

## Realm `aeroflow` — Cấu hình

| Thuộc tính | Giá trị |
|---|---|
| Access token TTL | 900s (15 phút) |
| SSO idle timeout | 28800s (8 giờ) |
| SSO max lifespan | 36000s (10 giờ) |
| Max concurrent sessions | 2 |
| PKCE | S256 (aeroflow-frontend) |
| Backchannel logout | Required (aeroflow-frontend) |
| SSL Required | none (dev) — đổi thành `external` cho prod |
| Brute force | 5 lần fail → lockout tăng dần, max 15 phút |

### Password Policy
`length(12) and upperCase(1) and lowerCase(1) and digits(1) and specialChars(1) and notUsername and notEmail and passwordHistory(5) and hashIterations(27500)`

### Roles
| Role | Mô tả |
|---|---|
| `platform-admin` | Full platform sovereignty |
| `ai-engineer` | AI runtime và deployment |
| `business-operator` | Business dashboards và reports |
| `executive-viewer` | Read-only executive view |

### Clients
| Client | Type | Dùng cho |
|---|---|---|
| `aeroflow-frontend` | Public, PKCE S256 | Browser SPA |
| `aeroflow-backend` | Confidential, service account | Kong token introspection |
| `aeroflow-admin` | Confidential, service account | python-keycloak admin tasks |

### Federated Identity Providers (disabled — cần credentials thật)
| Provider | Alias | Role Mappers |
|---|---|---|
| Azure AD (Entra ID) | `azure-ad` | `AeroFlow.AIEngineer` → `ai-engineer`, `AeroFlow.Admin` → `platform-admin` |
| Google Workspace | `google` | email attribute |
| Okta | `okta` | groups `aeroflow-engineers` → `ai-engineer` |

---

## mTLS — Kong → Backend

**Trạng thái:** Kong side đã implement. Backend side chờ có backend service.

| File | Vai trò |
|---|---|
| `infra/certs/gen-certs.sh` | Generate CA + kong-client cert/key + backend cert/key |
| `infra/certs/.gitignore` | Exclude `*.key`, `*.crt`, `*.pem` khỏi git |
| `infra/kong/kong-setup.sh` | Detect certs → đăng ký CA + client cert vào Kong |

Khi có backend service:
- Mount `infra/certs/ca.crt` vào container backend
- Backend verify client cert với `ssl.CERT_REQUIRED`
- Kong tự động present `kong-client.crt` trên mỗi upstream request

---

## Trạng thái Implementation

| # | Yêu cầu (thiết kế) | Trạng thái | File |
|---|---|---|---|
| §1.1 | Keycloak OIDC + PKCE S256 | ✅ | `realm-export.json`, `AuthProvider.tsx` |
| §1.2 | Network Segmentation — IP allowlist | ✅ | `kong-setup.sh` (ip-restriction plugin) |
| §1.2 | mTLS Kong → Backend (Kong side) | ✅ | `infra/certs/gen-certs.sh` + `kong-setup.sh` |
| §1.2 | mTLS Backend verify client cert | ⏳ | Chờ có backend service |
| §1.3 | `updateToken(30)` | ✅ | `src/lib/AuthProvider.tsx` |
| §1.4 | JWKS auto-refresh 300s (aeroflow-jwks plugin) | ✅ | `infra/kong/plugins/aeroflow-jwks/` |
| §1.4 | Kid rotation detection + cache invalidation | ✅ | `handler.lua` |
| §1.6 | Kafka audit pipeline — push-based SPI | ✅ | `infra/keycloak/spi/`, `Dockerfile.keycloak` |
| §1.6 | Kafka SASL/PLAIN (dev) | ✅ | `docker-compose.yml` |
| §1.6 | Kafka ACL per-topic | ✅ | `infra/kafka/kafka-setup.sh` |
| §1.6 | audit-consumer Kafka → PostgreSQL | ✅ | `services/audit-consumer/` |
| §1.7 | TOTP + WebAuthn MFA Required Actions | ✅ | `realm-export.json` |
| §1.7 | Session idle 8h, max 2 concurrent | ✅ | `realm-export.json` |
| §1.7 | Brute force protection | ✅ | `realm-export.json` |
| §1.7 | Password policy (12 chars + complexity) | ✅ | `realm-export.json` |
| §1.7 | Keycloak events logging (26 event types) | ✅ | `realm-export.json` |
| §1.7 | Backchannel logout | ✅ | `realm-export.json` |
| §1.8 | Federated IdP: Azure AD, Google, Okta | ✅ (placeholder) | `realm-export.json` |
| §1.8 | IdP Role Mappers | ✅ | `realm-export.json` |
| §1.9 | Keycloak HA Active/Active + HAProxy | ✅ | `docker-compose.yml`, `infra/haproxy/haproxy.cfg` |
| §2.1 | Kafka StandardAuthorizer + default deny | ✅ | `docker-compose.yml` |
| — | SSL/HTTPS Keycloak (production) | ⏳ | Cần domain thật |

---

## Cấu trúc Files

```
Data-Agentt/
├── docker-compose.yml              ← 10 containers (services)
├── docker/
│   └── Dockerfile.dev              ← Frontend Vite dev container
├── infra/
│   └── kong/
│       └── Dockerfile              ← Kong 3.6 + aeroflow-jwks plugin
├── Dockerfile.keycloak             ← Maven build SPI JAR → Keycloak 24.0
├── infra/
│   ├── sql/
│   │   └── init.sql               ← Schema + audit_logs partitioned RANGE by month
│   ├── keycloak/
│   │   ├── realm-export.json      ← Realm: clients, roles, users, MFA, IdP, events
│   │   └── spi/
│   │       ├── pom.xml            ← Maven: keycloak-spi + kafka-clients (shaded)
│   │       └── src/main/java/com/aeroflow/keycloak/
│   │           ├── KafkaEventListenerProvider.java
│   │           ├── KafkaEventListenerProviderFactory.java
│   │           └── resources/META-INF/services/...Factory
│   ├── haproxy/
│   │   └── haproxy.cfg            ← Round-robin + health-check /health/ready
│   ├── kafka/
│   │   └── kafka-setup.sh         ← Topic + ACL per-user
│   ├── certs/
│   │   ├── gen-certs.sh           ← CA + Kong client cert + backend cert
│   │   └── .gitignore             ← Exclude *.key / *.crt / *.pem
│   └── kong/
│       ├── kong-setup.sh          ← aeroflow-jwks + ip-restriction + rate-limit + mTLS cert
│       └── plugins/
│           └── aeroflow-jwks/
│               ├── handler.lua    ← RS256 verify, JWKS cache, header inject
│               └── schema.lua     ← Config: jwks_uri, issuer, jwks_refresh_interval
├── services/
│   └── audit-consumer/            ← Kafka → PostgreSQL audit_logs
└── src/
    ├── lib/AuthProvider.tsx        ← keycloak-js PKCE, updateToken(30)
    └── components/Settings/Auth/  ← UI panels
```

---

## Quyết định Thiết kế

### Tại sao dùng custom Lua plugin thay JWT built-in?

Kong Community 3.6 không có OIDC plugin built-in. `kong-oidc` (Nokia) không tương thích với Kong 3.6. Custom plugin `aeroflow-jwks` dùng thư viện `resty.openssl` và `resty.http` đã có sẵn trong Kong — không cần cài thêm luarocks package, không cần Kong Enterprise.

### Tại sao dùng SPI JAR thay audit-bridge polling?

audit-bridge poll Admin REST API mỗi 10s — có thể miss events khi tải cao, tạo race condition. SPI `onEvent()` được gọi synchronously trong Keycloak event bus — đảm bảo mọi event đều được capture, độ trễ gần 0.

### Tại sao dùng multi-stage Docker build cho Keycloak?

Không yêu cầu Java/Maven trên máy host. Maven chạy trong container riêng (stage 1), artifact được copy sang Keycloak image (stage 2). CI/CD không cần thêm toolchain.

---

## Security Notes cho Production

| Mục | Hiện tại (dev) | Production |
|---|---|---|
| Keycloak password | `admin/admin` | Đổi ngay, dùng secrets manager |
| Client secrets | hardcoded trong realm-export.json | Docker Secrets / OpenBao / Vault |
| DB password | `aeroflow_secret` | Rotate, dùng secrets manager |
| Kafka passwords | hardcoded | Rotate, dùng secrets manager |
| SSL Keycloak | `sslRequired: none` | `sslRequired: external`, `start-dev` → `start` |
| Kong Admin API | Port 8001 exposed | Không expose ra internet |
| audit_logs | app user có toàn quyền | Revoke UPDATE/DELETE cho app user |
| PostgreSQL HA | Single instance | Primary + Replica, pgBouncer |
## Current Docker Status

This document now serves as a design snapshot plus current-state note.

The current supported local stack does not run the older Keycloak HA pair, HAProxy LB, or Konga topology shown in historical sections below.

Current active auth-related services in the supported Docker stack:

- `dataagent-keycloak`
- `dataagent-kong`
- `dataagent-kong-migration`
- `dataagent-kafka`
- `dataagent-audit-bridge`
- `dataagent-audit-consumer`
- `dataagent-jwks-refresher`
- `dataagent-frontend`

Supported Docker entrypoints:

- `docker/docker-compose.yml`
- `docker/docker-compose.local.yml`
- `docker/docker-compose.dev.yml`

Current local startup command:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml up -d --build
```
