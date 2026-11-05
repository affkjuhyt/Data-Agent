-- Data Space "AdventureWorks" (DS-AW): the dataset the product is tested on.
--
-- AdventureWorksDW (Microsoft's sample warehouse of a bicycle maker, MIT) lives in its own database,
-- adventureworks_dw, loaded by `make adventureworks` (tests/e2e/adventureworks). It is queried in
-- place like a customer's warehouse: this file registers the Data Space and the source, nothing of
-- the data. The schema scan, the semantic model (deploy/docker/semantic/ds-aw.yaml through
-- `make semantic-import`) and the metric reviews are the product's own steps, so they are tested
-- rather than seeded.
--
-- The source logs in with the demo PostgreSQL credentials (OpenBao demo-postgres, app_user), which
-- the load grants CONNECT and SELECT on adventureworks_dw only. Until the load has run, scanning the
-- source fails with a connection error.
-- Idempotent: db-init re-applies it on every change of deploy/docker/sql/.

\set org '''a0000000-0000-0000-0000-000000000001'''
\set engineer '''a0000000-0000-0000-0000-000000000022'''
\set space '''a0000000-0000-0000-0000-000000000a01'''

-- DS-AW is the test organisation; self-approval is useful for end-to-end testing there.
UPDATE org.organization
SET allow_self_approval = true
WHERE id = :org::uuid;

INSERT INTO org.data_space (
    id, org_id, code, name, description, steward_id, status, opened_at, created_by, updated_by
)
VALUES (
    :space::uuid, :org::uuid, 'DS-AW', 'AdventureWorks',
    'Kho dữ liệu mẫu AdventureWorksDW của Microsoft (nhà sản xuất xe đạp): bán qua Internet và đại lý, '
    'sản phẩm, khách hàng, khuyến mãi, tài chính, tồn kho. Dữ liệu kiểm thử, không phải khách hàng thật.',
    :engineer::uuid, 'active', now(), :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, description = EXCLUDED.description, status = EXCLUDED.status;

INSERT INTO org.data_space_member (
    id, org_id, data_space_id, principal_type, principal_id, role_id, source
)
VALUES (
    'a0000000-0000-0000-0000-000000000a02', :org::uuid, :space::uuid, 'user', :engineer::uuid,
    'a0000000-0000-0000-0000-000000000022', 'manual'
)
ON CONFLICT (id) DO NOTHING;

-- helios-admin sits in DS-AW too, so the space is reachable from the website (a Data Space shows only
-- to its members, an admin's included) and someone other than the engineer decides what the
-- engineer proposes: the scan's relationships, the metric drafts.
INSERT INTO org.data_space_member (
    id, org_id, data_space_id, principal_type, principal_id, role_id, source
)
SELECT 'a0000000-0000-0000-0000-000000000a04', :org::uuid, :space::uuid, 'user', u.id,
       'a0000000-0000-0000-0000-000000000022', 'manual'
FROM org.app_user u
WHERE u.org_id = :org::uuid AND u.username = 'helios-admin'
ON CONFLICT (data_space_id, principal_type, principal_id, role_id) DO NOTHING;

-- helios-viewer: narrow capabilities for the screen-audit matrix (ask + view_aggregate).
INSERT INTO org.data_space_member (
    id, org_id, data_space_id, principal_type, principal_id, role_id, source
)
SELECT 'a0000000-0000-0000-0000-000000000a05', :org::uuid, :space::uuid, 'user', u.id,
       'a0000000-0000-0000-0000-000000000025', 'manual'
FROM org.app_user u
WHERE u.org_id = :org::uuid AND u.username = 'helios-viewer'
ON CONFLICT (data_space_id, principal_type, principal_id, role_id) DO NOTHING;

INSERT INTO catalog.source (
    id, org_id, data_space_id, name, kind, host, port, database_name, conn_options, secret_ref,
    mode, authority_level, scan_schemas, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000a03', :org::uuid, :space::uuid, 'AdventureWorksDW',
    'postgresql', 'postgres.dataagent-database.svc.cluster.local', 8300, 'adventureworks_dw',
    -- The cluster's own PostgreSQL has no TLS: a query defaults to verify-full
    -- (query/executor.py) and every SQL answer and metric preview of DS-AW failed on the
    -- missing root certificate. The API cannot set it (POST and PATCH /v1/sources carry no
    -- conn_options), so the seed says it.
    '{"sslmode": "disable"}',
    'secret/tenants/a0000000-0000-0000-0000-000000000001/demo-postgres', 'federated',
    'level_1', ARRAY['public'], 'active', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET
    host = EXCLUDED.host, port = EXCLUDED.port, database_name = EXCLUDED.database_name,
    conn_options = EXCLUDED.conn_options,
    secret_ref = EXCLUDED.secret_ref, scan_schemas = EXCLUDED.scan_schemas;
