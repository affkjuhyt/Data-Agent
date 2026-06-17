-- Dedicated database for Keycloak in the kb-ui-testing stack.
-- Runs first (00- prefix in docker-entrypoint-initdb.d) so the `keycloak` DB
-- exists before Keycloak/Liquibase creates its tables. Keeps Keycloak's schema
-- out of the KB database (aeroflow_kb), which already holds the KB ORM tables.
-- Only runs on a FRESH postgres volume (docker-entrypoint-initdb.d semantics);
-- on an existing kb-ui-testing volume, create it once by hand:
--   docker compose -f infra/docker-compose.kb-ui-testing.yml exec postgres \
--     psql -U aeroflow -d aeroflow_kb -c 'CREATE DATABASE keycloak;'
CREATE DATABASE keycloak;
