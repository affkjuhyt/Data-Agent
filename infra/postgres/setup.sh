#!/bin/bash
set -e

# Tạo extra DBs
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE DATABASE litellm;
    GRANT ALL PRIVILEGES ON DATABASE litellm TO $POSTGRES_USER;
    CREATE DATABASE langfuse;
    GRANT ALL PRIVILEGES ON DATABASE langfuse TO $POSTGRES_USER;
    CREATE DATABASE knowledge_base;
    GRANT ALL PRIVILEGES ON DATABASE knowledge_base TO $POSTGRES_USER;
EOSQL

# Extension + schemas trong dataagent
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
    CREATE SCHEMA IF NOT EXISTS keycloak;
    CREATE SCHEMA IF NOT EXISTS kong;
EOSQL

# KB tables trong knowledge_base (teammate)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname knowledge_base \
    -f /docker-entrypoint-initdb.d/kb-init.sql
