-- Data Space "NYC Taxi" (DS-TLC): the real dataset of the evaluation (EV-01,
-- docs/blueprint/DATASET_AND_EVALUATION.md §2).
--
-- The NYC TLC trip records (snapshot TLC-2024Q1, tests/e2e/tlc/sources.json) live in their own
-- database, tlc_trips, loaded by `make tlc`. Like DS-AW they are queried in place: this file
-- registers the Data Space and the source, nothing of the data. The schema scan, the semantic
-- model (deploy/docker/semantic/ds-tlc.yaml through `make semantic-import`) and the metric reviews
-- are the product's own steps.
--
-- The data is published by the City of New York under its terms of use (no open licence named):
-- it is used for internal evaluation and not redistributed.
--
-- The source logs in with the demo PostgreSQL credentials (OpenBao demo-postgres, app_user), which
-- `make tlc` grants CONNECT and SELECT on tlc_trips only. Until the load has run, scanning the
-- source fails with a connection error.
-- Idempotent: db-init re-applies it on every change of deploy/docker/sql/.

\set org '''a0000000-0000-0000-0000-000000000001'''
\set engineer '''a0000000-0000-0000-0000-000000000022'''
\set space '''a0000000-0000-0000-0000-000000000c01'''

INSERT INTO org.data_space (
    id, org_id, code, name, description, steward_id, status, opened_at, created_by, updated_by
)
VALUES (
    :space::uuid, :org::uuid, 'DS-TLC', 'NYC Taxi',
    'Chuyến taxi vàng và taxi xanh của New York do TLC (Taxi & Limousine Commission) công bố: '
    'thời điểm và khu đón khách, quãng đường, tiền cước, phụ phí, tip, hình thức thanh toán. '
    'Dữ liệu thật, giữ nguyên như công bố (kể cả dòng sai), dùng để đánh giá nội bộ.',
    :engineer::uuid, 'active', now(), :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, description = EXCLUDED.description, status = EXCLUDED.status;

INSERT INTO org.data_space_member (
    id, org_id, data_space_id, principal_type, principal_id, role_id, source
)
VALUES (
    'a0000000-0000-0000-0000-000000000c02', :org::uuid, :space::uuid, 'user', :engineer::uuid,
    'a0000000-0000-0000-0000-000000000022', 'manual'
)
ON CONFLICT (id) DO NOTHING;

-- helios-admin reviews what the engineer proposes (the scan's relationships, the metric drafts),
-- as in DS-AW.
INSERT INTO org.data_space_member (
    id, org_id, data_space_id, principal_type, principal_id, role_id, source
)
SELECT 'a0000000-0000-0000-0000-000000000c04', :org::uuid, :space::uuid, 'user', u.id,
       'a0000000-0000-0000-0000-000000000022', 'manual'
FROM org.app_user u
WHERE u.org_id = :org::uuid AND u.username = 'helios-admin'
ON CONFLICT (data_space_id, principal_type, principal_id, role_id) DO NOTHING;

-- helios-viewer asks and sees aggregates, the narrow role the evaluation also asks as.
INSERT INTO org.data_space_member (
    id, org_id, data_space_id, principal_type, principal_id, role_id, source
)
SELECT 'a0000000-0000-0000-0000-000000000c05', :org::uuid, :space::uuid, 'user', u.id,
       'a0000000-0000-0000-0000-000000000025', 'manual'
FROM org.app_user u
WHERE u.org_id = :org::uuid AND u.username = 'helios-viewer'
ON CONFLICT (data_space_id, principal_type, principal_id, role_id) DO NOTHING;

INSERT INTO catalog.source (
    id, org_id, data_space_id, name, kind, host, port, database_name, conn_options, secret_ref,
    mode, authority_level, scan_schemas, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000c03', :org::uuid, :space::uuid, 'NYC TLC Trip Records',
    'postgresql', 'postgres.dataagent-database.svc.cluster.local', 8300, 'tlc_trips',
    -- The cluster's own PostgreSQL has no TLS (see v3-adventureworks-seed.sql).
    '{"sslmode": "disable"}',
    'secret/tenants/a0000000-0000-0000-0000-000000000001/demo-postgres', 'federated',
    'level_1', ARRAY['public'], 'active', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET
    host = EXCLUDED.host, port = EXCLUDED.port, database_name = EXCLUDED.database_name,
    conn_options = EXCLUDED.conn_options,
    secret_ref = EXCLUDED.secret_ref, scan_schemas = EXCLUDED.scan_schemas;
