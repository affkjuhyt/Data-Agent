# Docker Entrypoints

This directory is the current Docker entrypoint for the repository.

## Canonical Files

- [docker-compose.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.yml>): aggregate entrypoint using `include`
- [docker-compose.storage.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.storage.yml>): storage layer
- [docker-compose.security.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.security.yml>): auth, gateway, and secrets layer
- [docker-compose.messaging.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.messaging.yml>): messaging layer
- [docker-compose.observability.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.observability.yml>): observability and AI-support layer
- [docker-compose.app.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.app.yml>): application and frontend layer
- [docker-compose.local.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.local.yml>): local developer override
- [docker-compose.dev.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.dev.yml>): shared dev server override
- [docker-compose.test.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.test.yml>): test-only PostgreSQL fixture

## Official Commands

Local development:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml up -d --build
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml down
```

Shared dev server:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d --build
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml down
```

Validation:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml config
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml config
```

Test fixture:

```powershell
docker compose -f docker/docker-compose.test.yml up -d
docker compose -f docker/docker-compose.test.yml down -v
```

## Intent Map

- `docker-compose.yml`: shared base runtime stack
- `docker-compose.yml`: aggregate entrypoint that includes the layer files
- `docker-compose.storage.yml`: storage layer
- `docker-compose.security.yml`: security and gateway layer
- `docker-compose.messaging.yml`: messaging layer
- `docker-compose.observability.yml`: observability and AI-support layer
- `docker-compose.app.yml`: application and frontend layer
- `docker-compose.local.yml`: local developer workflow
- `docker-compose.dev.yml`: shared dev server workflow
- `docker-compose.test.yml`: test-only fixture
- `../archive/docker-legacy/`: archived legacy Docker artifacts, not a supported entrypoint

Related Dockerfiles:

- [Dockerfile.dev](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/Dockerfile.dev>): dev-only frontend container

## Current Decision

The repository is keeping the `docker/docker-compose*.yml` names for now. The main entrypoint now composes the stack through `include`, while local, dev, and test flows continue to use the same top-level commands.
