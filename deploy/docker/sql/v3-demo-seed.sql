-- Local V3 demo identity seed.
-- Keycloak is the identity source; these rows materialise its demo users in the
-- organisation directory so API requests can be scoped by org and Data Space. The Data Spaces
-- they work in come from their own seeds (DS-AW: v3-adventureworks-seed.sql, which grants this
-- file's data-engineer role to helios-engineer).

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

-- The demo organisation's admin, as the realm's demo users file knows it (Keycloak id 6516da6c-…).
-- roles/k8s_org makes the organisation's first admin of it (AC05) and keeps this row: same
-- username, same Keycloak id. On a cluster where the bootstrap made it first, this does nothing.
INSERT INTO org.app_user (id, org_id, external_id, username, display_name, email, status)
VALUES (
    'a0000000-0000-0000-0000-000000000023',
    'a0000000-0000-0000-0000-000000000001',
    '6516da6c-bbaa-47ac-8c61-abfea8b9362d',
    'helios-admin',
    'Admin Helios',
    'helios-admin@helios.corp',
    'active'
)
ON CONFLICT (org_id, username) DO NOTHING;

INSERT INTO org.role (id, org_id, code, name, capabilities, is_system)
VALUES (
    'a0000000-0000-0000-0000-000000000022',
    'a0000000-0000-0000-0000-000000000001',
    'data-engineer',
    'Data Engineer',
    -- steward and approve: the demo engineer also curates DS-AW (its scan's relationships, its
    -- metric drafts), and helios-admin, seated with the same role, decides them.
    ARRAY['ask', 'view_aggregate', 'view_detail', 'run_agent', 'create_agent', 'upload', 'admin_data',
          'steward', 'approve'],
    true
)
ON CONFLICT (id) DO UPDATE SET
    capabilities = EXCLUDED.capabilities,
    name = EXCLUDED.name,
    is_system = EXCLUDED.is_system;

-- Restricted reader for the screen-audit harness (tests/screen_audit): ask + view_aggregate
-- only, so admin_data / exec_vault / steward screens refuse while Ask still works.
INSERT INTO org.app_user (id, org_id, external_id, username, display_name, email, status)
VALUES (
    'a0000000-0000-0000-0000-000000000024',
    'a0000000-0000-0000-0000-000000000001',
    '5004a4c3-8a42-4a65-a30c-2cfa83267f2c',
    'helios-viewer',
    'Viewer Helios',
    'helios-viewer@helios.corp',
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
    'a0000000-0000-0000-0000-000000000025',
    'a0000000-0000-0000-0000-000000000001',
    'viewer',
    'Viewer',
    ARRAY['ask', 'view_aggregate'],
    true
)
ON CONFLICT (id) DO UPDATE SET
    capabilities = EXCLUDED.capabilities,
    name = EXCLUDED.name,
    is_system = EXCLUDED.is_system;

-- DS-DEMO, the Data Space this seed used to open for the engineer, and DS-BH, the synthetic pilot
-- of v3-pilot-seed.sql, are retired: AdventureWorks (DS-AW, v3-adventureworks-seed.sql) is the one
-- the product is tested on. A cluster seeded before keeps rows that point at them in schemas without
-- a foreign key to org.data_space (questions, documents, datasets, agents), so the spaces are
-- archived, which hides them from the switcher and from identity resolution, rather than deleted
-- from under those rows. DS-DEMO's membership goes, unless an access request still names it; the
-- pilot's own tables in the workspace schema go with it.
UPDATE org.app_user
SET default_data_space_id = NULL
WHERE default_data_space_id IN (
    'a0000000-0000-0000-0000-000000000061', 'a0000000-0000-0000-0000-000000000b01'
);

DELETE FROM org.data_space_member m
WHERE m.id = 'a0000000-0000-0000-0000-000000000022'
  AND m.data_space_id = 'a0000000-0000-0000-0000-000000000061'
  AND NOT EXISTS (SELECT 1 FROM org.access_request r WHERE r.member_id = m.id);

UPDATE org.data_space
SET status = 'archived', deleted_at = COALESCE(deleted_at, now())
WHERE id IN ('a0000000-0000-0000-0000-000000000061', 'a0000000-0000-0000-0000-000000000b01')
  AND (status <> 'archived' OR deleted_at IS NULL);

DROP TABLE IF EXISTS workspace.pilot_sales_line, workspace.pilot_order_header,
    workspace.pilot_receivable, workspace.pilot_inventory, workspace.pilot_period_edition;
