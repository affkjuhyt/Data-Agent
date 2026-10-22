-- Agents for the sales pilot (DS-BH), so every Agents screen has a row to open.
--
-- Synthetic. The four agents cover the states the screens distinguish: one running a
-- margin question, one waiting on a person, one paused, and one leading two others.
-- A rolling deployment, a skill waiting for review and an open suggestion fill the
-- screens that are empty without an id. Idempotent: db-init can apply it again.

\set org '''a0000000-0000-0000-0000-000000000001'''
\set engineer '''a0000000-0000-0000-0000-000000000022'''
\set space '''a0000000-0000-0000-0000-000000000b01'''

INSERT INTO org.app_user (id, org_id, kind, username, display_name, owner_user_id, status)
VALUES
    ('a0000000-0000-0000-0000-000000000c01', :org::uuid, 'agent', 'agent-bien-gop', 'Agent Biên gộp', :engineer::uuid, 'active'),
    ('a0000000-0000-0000-0000-000000000c02', :org::uuid, 'agent', 'agent-cong-no', 'Agent Công nợ', :engineer::uuid, 'active'),
    ('a0000000-0000-0000-0000-000000000c03', :org::uuid, 'agent', 'agent-ton-kho', 'Agent Tồn kho', :engineer::uuid, 'active'),
    ('a0000000-0000-0000-0000-000000000c04', :org::uuid, 'agent', 'agent-dieu-phoi', 'Agent Điều phối', :engineer::uuid, 'active')
ON CONFLICT (id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    status = EXCLUDED.status,
    owner_user_id = EXCLUDED.owner_user_id;

INSERT INTO agent.agent_def (
    id, org_id, data_space_id, code, name, kind, owner_user_id, account_user_id,
    current_config_version, status, paused_reason, monthly_budget, spent_this_month,
    created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000c11', :org::uuid, :space::uuid, 'bien-gop',
     'Biên lợi nhuận gộp', 'analysis', :engineer::uuid, 'a0000000-0000-0000-0000-000000000c01',
     1, 'running', NULL, 2000000, 1860000, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c12', :org::uuid, :space::uuid, 'cong-no',
     'Công nợ phải thu', 'monitor', :engineer::uuid, 'a0000000-0000-0000-0000-000000000c02',
     1, 'running', NULL, 800000, 120000, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c13', :org::uuid, :space::uuid, 'ton-kho',
     'Tồn kho cuối tháng', 'data_quality', :engineer::uuid, 'a0000000-0000-0000-0000-000000000c03',
     1, 'paused', 'Chờ chốt sổ tháng 9', 500000, 40000, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c14', :org::uuid, :space::uuid, 'dieu-phoi',
     'Điều phối bán hàng', 'analysis', :engineer::uuid, 'a0000000-0000-0000-0000-000000000c04',
     1, 'running', NULL, 3000000, 640000, :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    status = EXCLUDED.status,
    paused_reason = EXCLUDED.paused_reason,
    current_config_version = EXCLUDED.current_config_version,
    monthly_budget = EXCLUDED.monthly_budget,
    spent_this_month = EXCLUDED.spent_this_month;

-- Eval of the candidate config (version 2 of biên gộp) and of the published skill.
INSERT INTO eval.eval_set (
    id, org_id, data_space_id, subject_type, subject_id, name, pass_threshold, min_items,
    status, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000c91', :org::uuid, :space::uuid, 'agent',
     'a0000000-0000-0000-0000-000000000c11', 'Biên gộp (seed)', 0.9500, 3, 'active',
     :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c94', :org::uuid, :space::uuid, 'skill',
     'a0000000-0000-0000-0000-000000000d01', 'Giải thích biên gộp (seed)', 0.9500, 1, 'active',
     :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, status = EXCLUDED.status;

INSERT INTO eval.eval_item (id, org_id, eval_set_id, category, input, expected, origin, status, created_by, updated_by)
VALUES
    ('a0000000-0000-0000-0000-000000000c95', :org::uuid, 'a0000000-0000-0000-0000-000000000c91',
     'correct_result', '{"text": "Biên gộp tháng 9 nhóm Thời trang"}',
     '{"text": "Giảm ở Thời trang, dùng bản final"}', 'manual', 'active', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c96', :org::uuid, 'a0000000-0000-0000-0000-000000000c91',
     'correct_result', '{"text": "Không lấy trung bình tỷ lệ ba chi nhánh"}',
     '{"text": "Dùng biên có trọng số doanh thu"}', 'manual', 'active', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c97', :org::uuid, 'a0000000-0000-0000-0000-000000000c91',
     'refusal', '{"text": "Cộng tồn kho các tháng"}',
     '{"text": "Từ chối: tồn kho là ảnh cuối tháng"}', 'manual', 'active', :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET input = EXCLUDED.input, expected = EXCLUDED.expected;

INSERT INTO eval.eval_run (
    id, org_id, eval_set_id, subject_version, trigger, item_count, passed, failed,
    pass_rate, regressions, status, started_at, finished_at
)
VALUES
    ('a0000000-0000-0000-0000-000000000c92', :org::uuid, 'a0000000-0000-0000-0000-000000000c91',
     'agent_config v1', 'manual', 3, 2, 1, 0.6667, 0, 'completed', now() - interval '8 days', now() - interval '8 days'),
    ('a0000000-0000-0000-0000-000000000c93', :org::uuid, 'a0000000-0000-0000-0000-000000000c91',
     'agent_config v2', 'deploy', 3, 3, 0, 1.0000, 0, 'completed', now() - interval '1 day', now() - interval '1 day')
ON CONFLICT (id) DO UPDATE SET
    passed = EXCLUDED.passed, failed = EXCLUDED.failed, pass_rate = EXCLUDED.pass_rate,
    status = EXCLUDED.status, finished_at = EXCLUDED.finished_at;

UPDATE eval.eval_run
SET baseline_run_id = 'a0000000-0000-0000-0000-000000000c92'
WHERE id = 'a0000000-0000-0000-0000-000000000c93';

INSERT INTO agent.agent_config (
    id, org_id, agent_id, version, goal, autonomy, skill_ids, change_note, status, eval_run_id
)
VALUES
    ('a0000000-0000-0000-0000-000000000c21', :org::uuid, 'a0000000-0000-0000-0000-000000000c11', 1,
     'Giải thích biên lợi nhuận gộp tháng trước giảm ở nhóm nào.', 'propose_approve',
     ARRAY['a0000000-0000-0000-0000-000000000d01']::uuid[], NULL, 'testing', NULL),
    ('a0000000-0000-0000-0000-000000000c22', :org::uuid, 'a0000000-0000-0000-0000-000000000c11', 2,
     'Giải thích biên lợi nhuận gộp tháng trước giảm ở nhóm nào.', 'propose_approve',
     ARRAY['a0000000-0000-0000-0000-000000000d01']::uuid[],
     'Dùng biên có trọng số doanh thu, không lấy trung bình tỷ lệ từng chi nhánh.',
     'testing', 'a0000000-0000-0000-0000-000000000c93'),
    ('a0000000-0000-0000-0000-000000000c23', :org::uuid, 'a0000000-0000-0000-0000-000000000c12', 1,
     'Chỉ ra hoá đơn quá hạn và hỏi trước khi nhắc khách.', 'propose_approve', '{}', NULL, 'testing', NULL),
    ('a0000000-0000-0000-0000-000000000c24', :org::uuid, 'a0000000-0000-0000-0000-000000000c13', 1,
     'Đối chiếu tồn cuối tháng, không cộng các tháng với nhau.', 'suggest_only', '{}', NULL, 'testing', NULL),
    ('a0000000-0000-0000-0000-000000000c25', :org::uuid, 'a0000000-0000-0000-0000-000000000c14', 1,
     'Giao việc biên gộp và công nợ, rồi gom một câu trả lời.', 'propose_approve', '{}', NULL, 'testing', NULL)
ON CONFLICT (id) DO UPDATE SET goal = EXCLUDED.goal, change_note = EXCLUDED.change_note, eval_run_id = EXCLUDED.eval_run_id;

INSERT INTO agent.agent_tool (id, org_id, agent_config_id, tool, permission_level, requires_checkpoint)
VALUES
    ('a0000000-0000-0000-0000-000000000c31', :org::uuid, 'a0000000-0000-0000-0000-000000000c21', 'query.semantic', 'read', false),
    ('a0000000-0000-0000-0000-000000000c32', :org::uuid, 'a0000000-0000-0000-0000-000000000c21', 'proposal.create', 'write_derived', true),
    ('a0000000-0000-0000-0000-000000000c33', :org::uuid, 'a0000000-0000-0000-0000-000000000c22', 'query.semantic', 'read', false),
    ('a0000000-0000-0000-0000-000000000c34', :org::uuid, 'a0000000-0000-0000-0000-000000000c23', 'query.semantic', 'read', false),
    ('a0000000-0000-0000-0000-000000000c35', :org::uuid, 'a0000000-0000-0000-0000-000000000c23', 'notify.send', 'send_external', true),
    ('a0000000-0000-0000-0000-000000000c36', :org::uuid, 'a0000000-0000-0000-0000-000000000c24', 'query.semantic', 'read', false),
    ('a0000000-0000-0000-0000-000000000c37', :org::uuid, 'a0000000-0000-0000-0000-000000000c25', 'agent.call', 'read', false)
ON CONFLICT (id) DO UPDATE SET permission_level = EXCLUDED.permission_level, requires_checkpoint = EXCLUDED.requires_checkpoint;

INSERT INTO agent.agent_trigger (
    id, org_id, agent_id, kind, cron, event_type, enabled, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000c41', :org::uuid, 'a0000000-0000-0000-0000-000000000c11',
     'schedule', '0 7 * * 1', NULL, true, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c42', :org::uuid, 'a0000000-0000-0000-0000-000000000c12',
     'event', NULL, 'period.closed', true, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c43', :org::uuid, 'a0000000-0000-0000-0000-000000000c14',
     'manual', NULL, NULL, true, :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET cron = EXCLUDED.cron, event_type = EXCLUDED.event_type, enabled = EXCLUDED.enabled;

INSERT INTO agent.agent_deployment (
    id, org_id, agent_id, config_version, previous_version, traffic_pct, gates, observed,
    status, started_at, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000c51', :org::uuid, 'a0000000-0000-0000-0000-000000000c11',
    2, 1, 10,
    '{"error_rate_increase_pts": 5, "needs_human_increase_pts": 10, "cost_per_run_increase_pct": 20, "min_runs": 30}',
    '{"window_days": 7, "baseline": {"runs": 12, "needs_human_pct": 25, "errors": 1}, "candidate": {"runs": 4, "needs_human_pct": 0, "errors": 0}}',
    'rolling', now() - interval '1 day', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET
    traffic_pct = EXCLUDED.traffic_pct, gates = EXCLUDED.gates, observed = EXCLUDED.observed, status = EXCLUDED.status;

-- Runs stay inside the last day and the last week, so 24 giờ and 7 ngày both have bars.
INSERT INTO agent.agent_run (
    id, org_id, agent_id, config_version, data_space_id, trigger_kind, initiated_by,
    parent_run_id, depth, status, narrative, cost, tokens, started_at, finished_at,
    error, stop_reason, created_at
)
VALUES
    ('a0000000-0000-0000-0000-000000000c61', :org::uuid, 'a0000000-0000-0000-0000-000000000c11', 1, :space::uuid,
     'schedule', NULL, NULL, 0, 'running',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '20 minutes', 'text', 'Đọc doanh thu và giá vốn tháng 9 theo nhóm hàng.', 'state', 'done'),
        jsonb_build_object('at', now() - interval '5 minutes', 'text', 'Đang so biên có trọng số với trung bình từng chi nhánh.', 'state', 'now', 'progress', 60)
     ),
     18000, 4200, now() - interval '25 minutes', NULL, NULL, NULL, now() - interval '25 minutes'),
    ('a0000000-0000-0000-0000-000000000c62', :org::uuid, 'a0000000-0000-0000-0000-000000000c12', 1, :space::uuid,
     'event', :engineer::uuid, NULL, 0, 'waiting_human',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '3 hours', 'text', '18 hoá đơn mở tại 30/09, trong đó tháng 8 và tháng 9 chưa thu.', 'state', 'done'),
        jsonb_build_object('at', now() - interval '2 hours', 'text', 'Chờ bạn duyệt trước khi nhắc khách.', 'state', 'now')
     ),
     9000, 1100, now() - interval '3 hours', NULL, NULL, NULL, now() - interval '3 hours'),
    ('a0000000-0000-0000-0000-000000000c63', :org::uuid, 'a0000000-0000-0000-0000-000000000c13', 1, :space::uuid,
     'manual', :engineer::uuid, NULL, 0, 'failed',
     '[]', 2000, 400, now() - interval '6 hours', now() - interval '5 hours',
     'Câu hỏi cộng tồn kho sáu tháng. Tồn kho là ảnh cuối tháng, không được cộng.',
     'error', now() - interval '6 hours'),
    ('a0000000-0000-0000-0000-000000000c64', :org::uuid, 'a0000000-0000-0000-0000-000000000c11', 1, :space::uuid,
     'schedule', NULL, NULL, 0, 'succeeded',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '2 days', 'text', 'Tháng 8, Thời trang giữ biên 32%.', 'state', 'done')
     ),
     12000, 2100, now() - interval '2 days', now() - interval '2 days' + interval '4 minutes',
     NULL, 'completed', now() - interval '2 days'),
    ('a0000000-0000-0000-0000-000000000c65', :org::uuid, 'a0000000-0000-0000-0000-000000000c14', 1, :space::uuid,
     'manual', :engineer::uuid, NULL, 0, 'running',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '40 minutes', 'text', 'Nhận câu hỏi biên gộp tháng 9 và giao cho hai agent.', 'state', 'done'),
        jsonb_build_object('at', now() - interval '10 minutes', 'text', 'Đang chờ biên gộp và công nợ trả lời.', 'state', 'now')
     ),
     6000, 800, now() - interval '40 minutes', NULL, NULL, NULL, now() - interval '40 minutes'),
    ('a0000000-0000-0000-0000-000000000c66', :org::uuid, 'a0000000-0000-0000-0000-000000000c11', 1, :space::uuid,
     'agent_call', NULL, 'a0000000-0000-0000-0000-000000000c65', 1, 'running',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '15 minutes', 'text', 'Tính biên Thời trang tháng 9 trên bản final.', 'state', 'now')
     ),
     7000, 900, now() - interval '15 minutes', NULL, NULL, NULL, now() - interval '15 minutes'),
    ('a0000000-0000-0000-0000-000000000c67', :org::uuid, 'a0000000-0000-0000-0000-000000000c12', 1, :space::uuid,
     'agent_call', NULL, 'a0000000-0000-0000-0000-000000000c65', 1, 'succeeded',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '12 minutes', 'text', 'Công nợ mở tập trung ở tháng 8 và tháng 9.', 'state', 'done')
     ),
     4000, 500, now() - interval '20 minutes', now() - interval '12 minutes',
     NULL, 'completed', now() - interval '20 minutes')
ON CONFLICT (id) DO UPDATE SET
    status = EXCLUDED.status,
    narrative = EXCLUDED.narrative,
    cost = EXCLUDED.cost,
    started_at = EXCLUDED.started_at,
    finished_at = EXCLUDED.finished_at,
    error = EXCLUDED.error,
    stop_reason = EXCLUDED.stop_reason,
    created_at = EXCLUDED.created_at;

INSERT INTO agent.agent_checkpoint_hit (id, org_id, run_id, checkpoint, hitl_request_id)
VALUES (
    'a0000000-0000-0000-0000-000000000c81', :org::uuid, 'a0000000-0000-0000-0000-000000000c62',
    'send_external', 'a0000000-0000-0000-0000-000000000c82'
)
ON CONFLICT (id) DO NOTHING;

DELETE FROM agent.agent_step WHERE id IN (
    'a0000000-0000-0000-0000-000000000c71',
    'a0000000-0000-0000-0000-000000000c72',
    'a0000000-0000-0000-0000-000000000c73',
    'a0000000-0000-0000-0000-000000000c74',
    'a0000000-0000-0000-0000-000000000c75'
);
INSERT INTO agent.agent_step (
    id, org_id, run_id, seq, kind, name, input, output, summary, status, cost, started_at, duration_ms, child_run_id
)
VALUES
    ('a0000000-0000-0000-0000-000000000c71', :org::uuid, 'a0000000-0000-0000-0000-000000000c61', 1,
     'query', 'query.semantic',
     '{"sql": "SELECT category, sum(net_revenue) AS revenue, sum(cogs) AS cogs FROM workspace.pilot_sales_line WHERE sold_on >= DATE ''2026-09-01'' GROUP BY category"}',
     '{"rows": 3}', 'Doanh thu và giá vốn tháng 9 theo nhóm.', 'ok', 1.2, now() - interval '20 minutes', 800, NULL),
    ('a0000000-0000-0000-0000-000000000c72', :org::uuid, 'a0000000-0000-0000-0000-000000000c61', 2,
     'skill', 'giai_thich_bien_gop', NULL, NULL, 'So biên có trọng số với trung bình chi nhánh.', 'ok', 2.4, now() - interval '10 minutes', 1400, NULL),
    ('a0000000-0000-0000-0000-000000000c73', :org::uuid, 'a0000000-0000-0000-0000-000000000c65', 1,
     'plan', 'Chia việc', NULL, NULL, 'Tách câu hỏi thành biên gộp và công nợ.', 'ok', 0.4, now() - interval '35 minutes', 200, NULL),
    ('a0000000-0000-0000-0000-000000000c74', :org::uuid, 'a0000000-0000-0000-0000-000000000c65', 2,
     'agent_call', 'Giải thích biên gộp', NULL, NULL, 'Agent biên gộp đang tính trên bản final.', 'ok', 0, now() - interval '15 minutes', NULL,
     'a0000000-0000-0000-0000-000000000c66'),
    ('a0000000-0000-0000-0000-000000000c75', :org::uuid, 'a0000000-0000-0000-0000-000000000c65', 3,
     'agent_call', 'Đối chiếu công nợ', NULL, '{"text": "Công nợ mở tập trung ở tháng 8 và tháng 9."}',
     'Công nợ mở tập trung ở tháng 8 và tháng 9.', 'ok', 0, now() - interval '20 minutes', 480000,
     'a0000000-0000-0000-0000-000000000c67');

INSERT INTO skill.skill (
    id, org_id, data_space_id, code, name, description, category, scope, owner_id, origin,
    current_version, run_count_30d, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000d01', :org::uuid, :space::uuid, 'giai_thich_bien_gop',
     'Giải thích biên gộp', 'Tách thay đổi biên lợi nhuận gộp theo nhóm hàng, trên bản final.',
     'decomposition', 'data_space', :engineer::uuid, 'manual', 1, 18, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000d03', :org::uuid, :space::uuid, 'doi_chieu_cong_no',
     'Đối chiếu công nợ', 'Liệt kê hoá đơn mở và tuổi nợ tại một ngày chốt.',
     'reconciliation', 'data_space', :engineer::uuid, 'manual', NULL, 4, :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, description = EXCLUDED.description, current_version = EXCLUDED.current_version,
    run_count_30d = EXCLUDED.run_count_30d;

INSERT INTO eval.eval_run (
    id, org_id, eval_set_id, subject_version, trigger, item_count, passed, failed,
    pass_rate, status, started_at, finished_at
)
VALUES (
    'a0000000-0000-0000-0000-000000000c98', :org::uuid, 'a0000000-0000-0000-0000-000000000c94',
    'v1', 'manual', 1, 1, 0, 1.0000, 'completed', now() - interval '3 days', now() - interval '3 days'
)
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, passed = EXCLUDED.passed;

INSERT INTO skill.skill_version (
    id, org_id, skill_id, version, params_schema, steps, status, eval_run_id, approved_by, approved_at
)
VALUES
    ('a0000000-0000-0000-0000-000000000d02', :org::uuid, 'a0000000-0000-0000-0000-000000000d01', 1,
     '{"type": "object", "properties": {"period": {"title": "Kỳ"}}}',
     '[{"kind": "decompose", "name": "Phân rã biên gộp"}]',
     'published', 'a0000000-0000-0000-0000-000000000c98', :engineer::uuid, now() - interval '3 days'),
    ('a0000000-0000-0000-0000-000000000d04', :org::uuid, 'a0000000-0000-0000-0000-000000000d03', 1,
     '{"type": "object", "properties": {"as_of": {"title": "Ngày chốt"}}}',
     '[{"kind": "reconcile", "name": "Đối chiếu hoá đơn mở"}]',
     'pending_review', NULL, NULL, NULL)
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, steps = EXCLUDED.steps;

INSERT INTO skill.skill_suggestion (
    id, org_id, data_space_id, pattern, session_ids, user_count, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000d05', :org::uuid, :space::uuid,
    '{"steps": [{"kind": "query", "name": "Doanh thu theo nhóm"}, {"kind": "skill", "name": "Phân rã biên gộp"}]}',
    ARRAY['a0000000-0000-0000-0000-000000000d11', 'a0000000-0000-0000-0000-000000000d12']::uuid[],
    4, 'open', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET pattern = EXCLUDED.pattern, user_count = EXCLUDED.user_count, status = EXCLUDED.status;

DO $$
BEGIN
    IF (SELECT count(*) FROM agent.agent_def WHERE data_space_id = 'a0000000-0000-0000-0000-000000000b01' AND deleted_at IS NULL) < 4 THEN
        RAISE EXCEPTION 'DS-BH is missing its agent team';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent.agent_deployment WHERE id = 'a0000000-0000-0000-0000-000000000c51' AND status = 'rolling') THEN
        RAISE EXCEPTION 'the margin agent has no rolling deployment';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent.agent_run WHERE parent_run_id = 'a0000000-0000-0000-0000-000000000c65') THEN
        RAISE EXCEPTION 'the lead run has no delegated run';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM skill.skill_version WHERE id = 'a0000000-0000-0000-0000-000000000d04' AND status = 'pending_review') THEN
        RAISE EXCEPTION 'no skill version is waiting for review';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM skill.skill_suggestion WHERE id = 'a0000000-0000-0000-0000-000000000d05' AND status = 'open') THEN
        RAISE EXCEPTION 'no skill suggestion is open';
    END IF;
END $$;
