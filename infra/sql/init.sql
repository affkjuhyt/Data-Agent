-- Minimal Postgres bootstrap — chỉ những gì alembic không handle được.
-- Tất cả application tables/indexes/seed data do alembic migration quản lý.

CREATE SCHEMA IF NOT EXISTS keycloak;
CREATE SCHEMA IF NOT EXISTS kong;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
