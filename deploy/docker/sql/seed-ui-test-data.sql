-- ─── UI Test Data — Release Management ───────────────────────────────
-- Seed realistic pipeline records for UI testing.
-- Run: docker exec aeroflow-postgres psql -U aeroflow -d aeroflow -f /tmp/seed-ui-test-data.sql

-- Clean previous test data
DELETE FROM pipeline_steps   WHERE pipeline_id LIKE 'ui-test-%';
DELETE FROM release_history  WHERE pipeline_id LIKE 'ui-test-%';
DELETE FROM release_packages WHERE pipeline_id LIKE 'ui-test-%';
DELETE FROM pipelines        WHERE id          LIKE 'ui-test-%';

-- ── 1. SUCCESS: GlobalCorp Agent v2.5 → PROD ─────────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, created_at, updated_at, completed_at)
VALUES ('ui-test-001', 'GlobalCorp_Agent Promotion', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'GIT_PUSH', 'a3f9c21', 'release/v2.5', 'v2.5.0', 'prod', 'SUCCESS', 12,
        now() - interval '2 hours',
        now() - interval '2 hours' + interval '4 minutes 12 seconds',
        now() - interval '2 hours' + interval '4 minutes 12 seconds');

INSERT INTO release_packages (id, pipeline_id, artifact_paths, validation_score, status, created_by, environment_targets, scan_result)
VALUES ('pkg-v2.5.0', 'ui-test-001', '["packages/pkg-v2.5.0/bundle.tar.gz"]', 98, 'VALIDATED',
        'ea14d6a1-8d72-429a-b342-828a3766de96', '["prod"]',
        '{"overall_status":"PASS","results":{"trivy":{"status":"PASS","critical":0,"high":0},"bandit":{"status":"PASS","high":0}}}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO release_history (pipeline_id, package_id, environment, status, triggered_by, deployed_at, duration_ms)
VALUES ('ui-test-001', 'pkg-v2.5.0', 'prod', 'SUCCESS',
        'ea14d6a1-8d72-429a-b342-828a3766de96',
        now() - interval '2 hours' + interval '4 minutes 12 seconds', 252000);

-- ── 2. AWAITING_APPROVAL: Refund Policy KB → PROD ────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, created_at, updated_at)
VALUES ('ui-test-002', 'refund_policy_index Sync', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'MANUAL', 'b7e2d84', 'feature/refund-v3', 'v3.2.0', 'prod', 'AWAITING_APPROVAL', 45,
        now() - interval '18 minutes',
        now() - interval '18 minutes' + interval '2 minutes 15 seconds');

INSERT INTO release_packages (id, pipeline_id, artifact_paths, validation_score, status, created_by, environment_targets, scan_result)
VALUES ('pkg-v3.2.0', 'ui-test-002', '["packages/pkg-v3.2.0/bundle.tar.gz"]', 91, 'VALIDATED',
        'ea14d6a1-8d72-429a-b342-828a3766de96', '["staging","prod"]',
        '{"overall_status":"PASS","results":{"trivy":{"status":"PASS"},"bandit":{"status":"PASS"}}}')
ON CONFLICT (id) DO NOTHING;

-- ── 3. BUILDING: Onboarding Workflow → UAT ───────────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, created_at, updated_at)
VALUES ('ui-test-003', 'onboarding_workflow Build', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'GIT_PUSH', 'c5a1e73', 'feat/onboarding-v2', 'v2.0.1', 'uat', 'BUILDING', 28,
        now() - interval '3 minutes',
        now() - interval '30 seconds');

-- ── 4. FAILED: Infrastructure Scale-Up → DEV ─────────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, error_message, created_at, updated_at, completed_at)
VALUES ('ui-test-004', 'Infrastructure Scale-Up', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'SCHEDULED', 'deadbeef', 'deploy/docker/scale-3.2', 'infra-3.2', 'dev', 'FAILED', 92,
        'Security scan gate failed — 3 CRITICAL CVEs in base image',
        now() - interval '5 hours',
        now() - interval '5 hours' + interval '12 minutes 40 seconds',
        now() - interval '5 hours' + interval '12 minutes 40 seconds');

-- ── 5. SUCCESS: Auth Policy Hotfix → PROD ────────────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, created_at, updated_at, completed_at)
VALUES ('ui-test-005', 'auth_policy Hotfix', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'MANUAL', 'f1a2b3c', 'hotfix/auth-v4', 'v4.0.0', 'prod', 'SUCCESS', 8,
        now() - interval '7 days',
        now() - interval '7 days' + interval '2 minutes 5 seconds',
        now() - interval '7 days' + interval '2 minutes 5 seconds');

INSERT INTO release_packages (id, pipeline_id, artifact_paths, validation_score, status, created_by, environment_targets, scan_result)
VALUES ('pkg-v4.0.0', 'ui-test-005', '["packages/pkg-v4.0.0/bundle.tar.gz"]', 99, 'VALIDATED',
        'ea14d6a1-8d72-429a-b342-828a3766de96', '["prod"]',
        '{"overall_status":"PASS","results":{"trivy":{"status":"PASS"},"bandit":{"status":"PASS"}}}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO release_history (pipeline_id, package_id, environment, status, triggered_by, deployed_at, duration_ms)
VALUES ('ui-test-005', 'pkg-v4.0.0', 'prod', 'SUCCESS',
        'ea14d6a1-8d72-429a-b342-828a3766de96',
        now() - interval '7 days' + interval '2 minutes 5 seconds', 125000);

-- ── 6. SUCCESS: Pricing Engine → STAGING ─────────────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, created_at, updated_at, completed_at)
VALUES ('ui-test-006', 'pricing_engine Release', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'GIT_PUSH', 'e9d4f67', 'release/pricing-1.2', 'v1.2.0', 'staging', 'SUCCESS', 22,
        now() - interval '6 days',
        now() - interval '6 days' + interval '3 minutes 22 seconds',
        now() - interval '6 days' + interval '3 minutes 22 seconds');

INSERT INTO release_packages (id, pipeline_id, artifact_paths, validation_score, status, created_by, environment_targets, scan_result)
VALUES ('pkg-v1.2.0', 'ui-test-006', '["packages/pkg-v1.2.0/bundle.tar.gz"]', 95, 'VALIDATED',
        'ea14d6a1-8d72-429a-b342-828a3766de96', '["staging"]',
        '{"overall_status":"PASS","results":{"trivy":{"status":"PASS"},"bandit":{"status":"PASS"}}}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO release_history (pipeline_id, package_id, environment, status, triggered_by, deployed_at, duration_ms)
VALUES ('ui-test-006', 'pkg-v1.2.0', 'staging', 'SUCCESS',
        'ea14d6a1-8d72-429a-b342-828a3766de96',
        now() - interval '6 days' + interval '3 minutes 22 seconds', 202000);

-- ── 7. ROLLED_BACK: GlobalCorp Agent v2.4 → PROD ─────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, created_at, updated_at, completed_at)
VALUES ('ui-test-007', 'GlobalCorp_Agent Promotion', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'GIT_PUSH', 'ba9c112', 'release/v2.4', 'v2.4.0', 'prod', 'ROLLED_BACK', 35,
        now() - interval '8 days',
        now() - interval '7 days 18 hours',
        now() - interval '7 days 18 hours');

INSERT INTO release_history (pipeline_id, package_id, environment, status, triggered_by, deployed_at, duration_ms)
VALUES ('ui-test-007', 'pkg-v2.4.0', 'prod', 'ROLLED_BACK',
        'ea14d6a1-8d72-429a-b342-828a3766de96',
        now() - interval '7 days 18 hours', 400000);

-- ── 8. SCANNING: Federal Compliance Patch → PROD ─────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, created_at, updated_at)
VALUES ('ui-test-008', 'Federal Compliance Patch', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'MANUAL', 'd3c7f91', 'compliance/v2.1', 'policy-v2.1', 'prod', 'SCANNING', 15,
        now() - interval '8 minutes',
        now() - interval '1 minute');

-- ── 9. SUCCESS: KB Corporate Sync → STAGING ──────────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, created_at, updated_at, completed_at)
VALUES ('ui-test-009', 'KB_Corporate_v5 Sync', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'SCHEDULED', 'a1b2c3d4', 'kb/corporate-v5', 'kb-v5.0', 'staging', 'SUCCESS', 18,
        now() - interval '3 days',
        now() - interval '3 days' + interval '1 minute 58 seconds',
        now() - interval '3 days' + interval '1 minute 58 seconds');

INSERT INTO release_history (pipeline_id, package_id, environment, status, triggered_by, deployed_at, duration_ms)
VALUES ('ui-test-009', 'pkg-kb-v5.0', 'staging', 'SUCCESS',
        'ea14d6a1-8d72-429a-b342-828a3766de96',
        now() - interval '3 days' + interval '1 minute 58 seconds', 118000);

-- ── 10. FAILED: AI Runtime Upgrade → UAT ─────────────────────────────
INSERT INTO pipelines (id, pipeline_name, triggered_by, trigger_type, commit_sha, branch, package_version, target_env, status, risk_score, error_message, created_at, updated_at, completed_at)
VALUES ('ui-test-010', 'AI_Runtime_Upgrade Deploy', 'ea14d6a1-8d72-429a-b342-828a3766de96',
        'GIT_PUSH', 'e5f6a7b8', 'release/runtime-v3', 'runtime-v3.0', 'uat', 'FAILED', 67,
        'Container health check failed after deploy — timeout 60s',
        now() - interval '4 days',
        now() - interval '4 days' + interval '6 minutes 20 seconds',
        now() - interval '4 days' + interval '6 minutes 20 seconds');

SELECT id, pipeline_name, target_env, status, risk_score
FROM pipelines
WHERE id LIKE 'ui-test-%'
ORDER BY created_at DESC;
