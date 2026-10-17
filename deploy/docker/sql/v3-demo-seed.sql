-- Local V3 demo identity seed.
-- Keycloak is the identity source; these rows materialise its demo users in the
-- organisation directory so API requests can be scoped by org and Data Space.

INSERT INTO org.app_user (id, org_id, external_id, username, display_name, email, status)
VALUES (
    'a0000000-0000-0000-0000-000000000022',
    'a0000000-0000-0000-0000-000000000001',
    'bdb221bf-d668-4680-9218-abb7df1ca080',
    'helios-engineer',
    'Engineer Helios',
    'helios-engineer@helios.corp',
    'active'
)
ON CONFLICT (id) DO UPDATE SET
    external_id = EXCLUDED.external_id,
    username = EXCLUDED.username,
    display_name = EXCLUDED.display_name,
    email = EXCLUDED.email,
    status = EXCLUDED.status;

INSERT INTO org.role (id, org_id, code, name, capabilities, is_system)
VALUES (
    'a0000000-0000-0000-0000-000000000022',
    'a0000000-0000-0000-0000-000000000001',
    'data-engineer',
    'Data Engineer',
    ARRAY['ask', 'view_aggregate', 'view_detail', 'run_agent', 'create_agent', 'upload', 'admin_data'],
    true
)
ON CONFLICT (id) DO UPDATE SET
    capabilities = EXCLUDED.capabilities,
    name = EXCLUDED.name,
    is_system = EXCLUDED.is_system;

INSERT INTO org.data_space (
    id, org_id, code, name, description, steward_id, status, opened_at, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000061',
    'a0000000-0000-0000-0000-000000000001',
    'DS-DEMO',
    'Demo Data Space',
    'Local V3 demo data space for the engineer E2E account.',
    'a0000000-0000-0000-0000-000000000022',
    'active',
    now(),
    'a0000000-0000-0000-0000-000000000022',
    'a0000000-0000-0000-0000-000000000022'
)
ON CONFLICT (id) DO UPDATE SET
    code = EXCLUDED.code,
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    steward_id = EXCLUDED.steward_id,
    status = EXCLUDED.status,
    opened_at = COALESCE(org.data_space.opened_at, EXCLUDED.opened_at),
    updated_by = EXCLUDED.updated_by;

INSERT INTO org.data_space_member (
    id, org_id, data_space_id, principal_type, principal_id, role_id, source
)
VALUES (
    'a0000000-0000-0000-0000-000000000022',
    'a0000000-0000-0000-0000-000000000001',
    'a0000000-0000-0000-0000-000000000061',
    'user',
    'a0000000-0000-0000-0000-000000000022',
    'a0000000-0000-0000-0000-000000000022',
    'manual'
)
ON CONFLICT (id) DO UPDATE SET
    data_space_id = EXCLUDED.data_space_id,
    principal_id = EXCLUDED.principal_id,
    role_id = EXCLUDED.role_id;
