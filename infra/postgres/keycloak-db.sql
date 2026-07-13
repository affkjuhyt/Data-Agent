-- Dedicated database for Keycloak in the kb_e2e stack.
-- Runs first (00- prefix in docker-entrypoint-initdb.d) so the `keycloak` DB
-- exists before Keycloak/Liquibase creates its tables. Keeps Keycloak's schema
-- out of the KB database (aeroflow_kb), which already holds the KB ORM tables.
-- Only runs on a FRESH postgres volume (docker-entrypoint-initdb.d semantics);
-- on an existing kb_e2e volume, create it once by hand:
--   docker compose -f infra/docker-compose.kb_e2e.yml exec postgres \
--     psql -U aeroflow -d aeroflow_kb -c 'CREATE DATABASE keycloak;'
CREATE DATABASE keycloak;
