-- Agents for the AdventureWorks Data Space (DS-AW), so every Agents screen has a row to open.
--
-- Synthetic runs over real AdventureWorksDW figures (Internet 2013: 16,35 triệu USD, biên gộp
-- 41,4 %; đại lý 2013: 33,57 triệu USD against a 29,51 triệu USD quota). The four agents cover the
-- states the screens distinguish: one running a margin question, one waiting on a person, one
-- paused, and one leading two others. A rolling deployment, a skill waiting for review and an open
-- suggestion fill the screens that are empty without an id.
-- Ids are a0000000-…-000000aa0cNN / …aa0dNN, apart from the DS-BH team an older seed left behind.
-- Idempotent: db-init can apply it again, and the runs move back into the last day.

\set org '''a0000000-0000-0000-0000-000000000001'''
\set engineer '''a0000000-0000-0000-0000-000000000022'''
\set space '''a0000000-0000-0000-0000-000000000a01'''

INSERT INTO org.app_user (id, org_id, kind, username, display_name, owner_user_id, status)
VALUES
    ('a0000000-0000-0000-0000-000000aa0c01', :org::uuid, 'agent', 'agent-aw-bien-gop', 'Agent Biên gộp Internet', :engineer::uuid, 'active'),
    ('a0000000-0000-0000-0000-000000aa0c02', :org::uuid, 'agent', 'agent-aw-doanh-thu-kenh', 'Agent Doanh thu theo kênh', :engineer::uuid, 'active'),
    ('a0000000-0000-0000-0000-000000aa0c03', :org::uuid, 'agent', 'agent-aw-ton-kho', 'Agent Tồn kho', :engineer::uuid, 'active'),
    ('a0000000-0000-0000-0000-000000aa0c04', :org::uuid, 'agent', 'agent-aw-dieu-phoi', 'Agent Điều phối', :engineer::uuid, 'active')
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
    ('a0000000-0000-0000-0000-000000aa0c11', :org::uuid, :space::uuid, 'bien-gop-internet',
     'Biên lợi nhuận gộp Internet', 'analysis', :engineer::uuid, 'a0000000-0000-0000-0000-000000aa0c01',
     1, 'running', NULL, 2000000, 1860000, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0c12', :org::uuid, :space::uuid, 'doanh-thu-kenh',
     'Doanh thu theo kênh', 'monitor', :engineer::uuid, 'a0000000-0000-0000-0000-000000aa0c02',
     1, 'running', NULL, 800000, 120000, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0c13', :org::uuid, :space::uuid, 'ton-kho',
     'Tồn kho cuối kỳ', 'data_quality', :engineer::uuid, 'a0000000-0000-0000-0000-000000aa0c03',
     1, 'paused', 'Chờ nạp ảnh tồn kho sau 30/06/2014', 500000, 40000, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0c14', :org::uuid, :space::uuid, 'dieu-phoi',
     'Điều phối kinh doanh', 'analysis', :engineer::uuid, 'a0000000-0000-0000-0000-000000aa0c04',
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
    ('a0000000-0000-0000-0000-000000aa0c91', :org::uuid, :space::uuid, 'agent',
     'a0000000-0000-0000-0000-000000aa0c11', 'Biên gộp Internet (seed)', 0.9500, 3, 'active',
     :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0c94', :org::uuid, :space::uuid, 'skill',
     'a0000000-0000-0000-0000-000000aa0d01', 'Giải thích biên gộp Internet (seed)', 0.9500, 1, 'active',
     :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, status = EXCLUDED.status;

INSERT INTO eval.eval_item (id, org_id, eval_set_id, category, input, expected, origin, status, created_by, updated_by)
VALUES
    ('a0000000-0000-0000-0000-000000aa0c95', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c91',
     'correct_result', '{"text": "Biên gộp Internet năm 2013 theo nhóm sản phẩm"}',
     '{"text": "Bikes 40,5 %, Accessories 62,6 %, Clothing 40,0 %"}', 'manual', 'active', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0c96', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c91',
     'correct_result', '{"text": "Không lấy trung bình biên của ba nhóm sản phẩm"}',
     '{"text": "Biên có trọng số doanh thu: 41,4 %, không phải 47,7 %"}', 'manual', 'active', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0c97', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c91',
     'refusal', '{"text": "Cộng tồn kho các tháng"}',
     '{"text": "Từ chối: tồn kho là ảnh tại một ngày, không cộng theo thời gian"}', 'manual', 'active', :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET input = EXCLUDED.input, expected = EXCLUDED.expected;

INSERT INTO eval.eval_run (
    id, org_id, eval_set_id, subject_version, trigger, item_count, passed, failed,
    pass_rate, regressions, status, started_at, finished_at
)
VALUES
    ('a0000000-0000-0000-0000-000000aa0c92', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c91',
     'agent_config v1', 'manual', 3, 2, 1, 0.6667, 0, 'completed', now() - interval '8 days', now() - interval '8 days'),
    ('a0000000-0000-0000-0000-000000aa0c93', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c91',
     'agent_config v2', 'deploy', 3, 3, 0, 1.0000, 0, 'completed', now() - interval '1 day', now() - interval '1 day')
ON CONFLICT (id) DO UPDATE SET
    passed = EXCLUDED.passed, failed = EXCLUDED.failed, pass_rate = EXCLUDED.pass_rate,
    status = EXCLUDED.status, finished_at = EXCLUDED.finished_at;

UPDATE eval.eval_run
SET baseline_run_id = 'a0000000-0000-0000-0000-000000aa0c92'
WHERE id = 'a0000000-0000-0000-0000-000000aa0c93';

INSERT INTO agent.agent_config (
    id, org_id, agent_id, version, goal, autonomy, skill_ids, change_note, status, eval_run_id
)
VALUES
    ('a0000000-0000-0000-0000-000000aa0c21', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c11', 1,
     'Giải thích biên lợi nhuận gộp Internet tháng trước thay đổi ở nhóm sản phẩm nào.', 'propose_approve',
     ARRAY['a0000000-0000-0000-0000-000000aa0d01']::uuid[], NULL, 'testing', NULL),
    ('a0000000-0000-0000-0000-000000aa0c22', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c11', 2,
     'Giải thích biên lợi nhuận gộp Internet tháng trước thay đổi ở nhóm sản phẩm nào.', 'propose_approve',
     ARRAY['a0000000-0000-0000-0000-000000aa0d01']::uuid[],
     'Dùng biên có trọng số doanh thu, không lấy trung bình biên của từng nhóm sản phẩm.',
     'testing', 'a0000000-0000-0000-0000-000000aa0c93'),
    ('a0000000-0000-0000-0000-000000aa0c23', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c12', 1,
     'So doanh thu Internet và đại lý với chỉ tiêu doanh số, hỏi trước khi gửi báo cáo cho trưởng vùng.',
     'propose_approve', '{}', NULL, 'testing', NULL),
    ('a0000000-0000-0000-0000-000000aa0c24', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c13', 1,
     'Đối chiếu tồn kho tại ngày cuối kỳ, không cộng các ngày với nhau.', 'suggest_only', '{}', NULL, 'testing', NULL),
    ('a0000000-0000-0000-0000-000000aa0c25', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c14', 1,
     'Giao việc biên gộp và doanh thu theo kênh, rồi gom một câu trả lời.', 'propose_approve', '{}', NULL, 'testing', NULL)
ON CONFLICT (id) DO UPDATE SET goal = EXCLUDED.goal, change_note = EXCLUDED.change_note, eval_run_id = EXCLUDED.eval_run_id;

INSERT INTO agent.agent_tool (id, org_id, agent_config_id, tool, permission_level, requires_checkpoint)
VALUES
    ('a0000000-0000-0000-0000-000000aa0c31', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c21', 'query.semantic', 'read', false),
    ('a0000000-0000-0000-0000-000000aa0c32', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c21', 'proposal.create', 'write_derived', true),
    ('a0000000-0000-0000-0000-000000aa0c33', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c22', 'query.semantic', 'read', false),
    ('a0000000-0000-0000-0000-000000aa0c34', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c23', 'query.semantic', 'read', false),
    ('a0000000-0000-0000-0000-000000aa0c35', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c23', 'notify.send', 'send_external', true),
    ('a0000000-0000-0000-0000-000000aa0c36', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c24', 'query.semantic', 'read', false),
    ('a0000000-0000-0000-0000-000000aa0c37', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c25', 'agent.call', 'read', false)
ON CONFLICT (id) DO UPDATE SET permission_level = EXCLUDED.permission_level, requires_checkpoint = EXCLUDED.requires_checkpoint;

INSERT INTO agent.agent_trigger (
    id, org_id, agent_id, kind, cron, event_type, enabled, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000aa0c41', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c11',
     'schedule', '0 7 * * 1', NULL, true, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0c42', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c12',
     'event', NULL, 'period.closed', true, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0c43', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c14',
     'manual', NULL, NULL, true, :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET cron = EXCLUDED.cron, event_type = EXCLUDED.event_type, enabled = EXCLUDED.enabled;

INSERT INTO agent.agent_deployment (
    id, org_id, agent_id, config_version, previous_version, traffic_pct, gates, observed,
    status, started_at, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa0c51', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c11',
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
    ('a0000000-0000-0000-0000-000000aa0c61', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c11', 1, :space::uuid,
     'schedule', NULL, NULL, 0, 'running',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '20 minutes', 'text', 'Đọc doanh thu và giá vốn Internet tháng 12/2013 theo nhóm sản phẩm.', 'state', 'done'),
        jsonb_build_object('at', now() - interval '5 minutes', 'text', 'Đang so biên có trọng số với trung bình biên từng nhóm.', 'state', 'now', 'progress', 60)
     ),
     18000, 4200, now() - interval '25 minutes', NULL, NULL, NULL, now() - interval '25 minutes'),
    ('a0000000-0000-0000-0000-000000aa0c62', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c12', 1, :space::uuid,
     'event', :engineer::uuid, NULL, 0, 'waiting_human',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '3 hours', 'text', 'Năm 2013: đại lý 33,57 triệu USD, vượt chỉ tiêu 29,51 triệu USD; Internet 16,35 triệu USD.', 'state', 'done'),
        jsonb_build_object('at', now() - interval '2 hours', 'text', 'Chờ bạn duyệt trước khi gửi báo cáo cho trưởng vùng.', 'state', 'now')
     ),
     9000, 1100, now() - interval '3 hours', NULL, NULL, NULL, now() - interval '3 hours'),
    ('a0000000-0000-0000-0000-000000aa0c63', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c13', 1, :space::uuid,
     'manual', :engineer::uuid, NULL, 0, 'failed',
     '[]', 2000, 400, now() - interval '6 hours', now() - interval '5 hours',
     'Câu hỏi cộng tồn kho sáu tháng. Tồn kho là ảnh tại một ngày, không được cộng.',
     'error', now() - interval '6 hours'),
    ('a0000000-0000-0000-0000-000000aa0c64', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c11', 1, :space::uuid,
     'schedule', NULL, NULL, 0, 'succeeded',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '2 days', 'text', 'Tháng 11/2013, biên gộp Internet 41,5 %; Bikes giữ khoảng 40 %.', 'state', 'done')
     ),
     12000, 2100, now() - interval '2 days', now() - interval '2 days' + interval '4 minutes',
     NULL, 'completed', now() - interval '2 days'),
    ('a0000000-0000-0000-0000-000000aa0c65', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c14', 1, :space::uuid,
     'manual', :engineer::uuid, NULL, 0, 'running',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '40 minutes', 'text', 'Nhận câu hỏi kết quả kinh doanh năm 2013 và giao cho hai agent.', 'state', 'done'),
        jsonb_build_object('at', now() - interval '10 minutes', 'text', 'Đang chờ biên gộp và doanh thu theo kênh trả lời.', 'state', 'now')
     ),
     6000, 800, now() - interval '40 minutes', NULL, NULL, NULL, now() - interval '40 minutes'),
    ('a0000000-0000-0000-0000-000000aa0c66', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c11', 1, :space::uuid,
     'agent_call', NULL, 'a0000000-0000-0000-0000-000000aa0c65', 1, 'running',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '15 minutes', 'text', 'Tính biên gộp Internet năm 2013 theo nhóm sản phẩm.', 'state', 'now')
     ),
     7000, 900, now() - interval '15 minutes', NULL, NULL, NULL, now() - interval '15 minutes'),
    ('a0000000-0000-0000-0000-000000aa0c67', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c12', 1, :space::uuid,
     'agent_call', NULL, 'a0000000-0000-0000-0000-000000aa0c65', 1, 'succeeded',
     jsonb_build_array(
        jsonb_build_object('at', now() - interval '12 minutes', 'text', 'Đại lý chiếm hai phần ba doanh thu năm 2013, Bắc Mỹ dẫn đầu.', 'state', 'done')
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
    'a0000000-0000-0000-0000-000000aa0c81', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c62',
    'send_external', 'a0000000-0000-0000-0000-000000aa0c82'
)
ON CONFLICT (id) DO NOTHING;

DELETE FROM agent.agent_step WHERE id IN (
    'a0000000-0000-0000-0000-000000aa0c71',
    'a0000000-0000-0000-0000-000000aa0c72',
    'a0000000-0000-0000-0000-000000aa0c73',
    'a0000000-0000-0000-0000-000000aa0c74',
    'a0000000-0000-0000-0000-000000aa0c75'
);
INSERT INTO agent.agent_step (
    id, org_id, run_id, seq, kind, name, input, output, summary, status, cost, started_at, duration_ms, child_run_id
)
VALUES
    ('a0000000-0000-0000-0000-000000aa0c71', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c61', 1,
     'query', 'query.semantic',
     '{"sql": "SELECT c.english_product_category_name AS category, sum(f.sales_amount) AS revenue, sum(f.total_product_cost) AS cogs FROM fact_internet_sales f JOIN dim_product p ON p.product_key = f.product_key JOIN dim_product_subcategory s ON s.product_subcategory_key = p.product_subcategory_key JOIN dim_product_category c ON c.product_category_key = s.product_category_key WHERE f.order_date >= DATE ''2013-12-01'' AND f.order_date < DATE ''2014-01-01'' GROUP BY 1"}',
     '{"rows": 3}', 'Doanh thu và giá vốn Internet tháng 12/2013 theo nhóm.', 'ok', 1.2, now() - interval '20 minutes', 800, NULL),
    ('a0000000-0000-0000-0000-000000aa0c72', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c61', 2,
     'skill', 'giai_thich_bien_gop_internet', NULL, NULL, 'So biên có trọng số với trung bình biên từng nhóm.', 'ok', 2.4, now() - interval '10 minutes', 1400, NULL),
    ('a0000000-0000-0000-0000-000000aa0c73', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c65', 1,
     'plan', 'Chia việc', NULL, NULL, 'Tách câu hỏi thành biên gộp và doanh thu theo kênh.', 'ok', 0.4, now() - interval '35 minutes', 200, NULL),
    ('a0000000-0000-0000-0000-000000aa0c74', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c65', 2,
     'agent_call', 'Giải thích biên gộp Internet', NULL, NULL, 'Agent biên gộp đang tính theo nhóm sản phẩm.', 'ok', 0, now() - interval '15 minutes', NULL,
     'a0000000-0000-0000-0000-000000aa0c66'),
    ('a0000000-0000-0000-0000-000000aa0c75', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c65', 3,
     'agent_call', 'So doanh thu với chỉ tiêu', NULL, '{"text": "Đại lý chiếm hai phần ba doanh thu năm 2013, Bắc Mỹ dẫn đầu."}',
     'Đại lý chiếm hai phần ba doanh thu năm 2013, Bắc Mỹ dẫn đầu.', 'ok', 0, now() - interval '20 minutes', 480000,
     'a0000000-0000-0000-0000-000000aa0c67');

INSERT INTO skill.skill (
    id, org_id, data_space_id, code, name, description, category, scope, owner_id, origin,
    current_version, run_count_30d, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000aa0d01', :org::uuid, :space::uuid, 'giai_thich_bien_gop_internet',
     'Giải thích biên gộp Internet', 'Tách thay đổi biên lợi nhuận gộp Internet theo nhóm và tiểu nhóm sản phẩm.',
     'decomposition', 'data_space', :engineer::uuid, 'manual', 1, 18, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0d03', :org::uuid, :space::uuid, 'so_doanh_thu_chi_tieu',
     'So doanh thu với chỉ tiêu', 'Đặt doanh thu đại lý cạnh chỉ tiêu doanh số theo quý tài chính và vùng.',
     'reconciliation', 'data_space', :engineer::uuid, 'manual', NULL, 4, :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, description = EXCLUDED.description, current_version = EXCLUDED.current_version,
    run_count_30d = EXCLUDED.run_count_30d;

INSERT INTO eval.eval_run (
    id, org_id, eval_set_id, subject_version, trigger, item_count, passed, failed,
    pass_rate, status, started_at, finished_at
)
VALUES (
    'a0000000-0000-0000-0000-000000aa0c98', :org::uuid, 'a0000000-0000-0000-0000-000000aa0c94',
    'v1', 'manual', 1, 1, 0, 1.0000, 'completed', now() - interval '3 days', now() - interval '3 days'
)
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, passed = EXCLUDED.passed;

INSERT INTO skill.skill_version (
    id, org_id, skill_id, version, params_schema, steps, status, eval_run_id, approved_by, approved_at
)
VALUES
    ('a0000000-0000-0000-0000-000000aa0d02', :org::uuid, 'a0000000-0000-0000-0000-000000aa0d01', 1,
     '{"type": "object", "properties": {"period": {"title": "Kỳ"}}}',
     '[{"kind": "decompose", "name": "Phân rã biên gộp Internet"}]',
     'published', 'a0000000-0000-0000-0000-000000aa0c98', :engineer::uuid, now() - interval '3 days'),
    ('a0000000-0000-0000-0000-000000aa0d04', :org::uuid, 'a0000000-0000-0000-0000-000000aa0d03', 1,
     '{"type": "object", "properties": {"fiscal_year": {"title": "Năm tài chính"}}}',
     '[{"kind": "reconcile", "name": "Đối chiếu doanh thu đại lý với chỉ tiêu"}]',
     'pending_review', NULL, NULL, NULL)
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, steps = EXCLUDED.steps;

-- The two sessions the suggestion below was read from: opening it builds the skill draft from
-- their cells (GET /v1/workspaces/{id}), so without them the page says "Workspace not found".
-- The reviewer, helios-admin, sees them as a viewer.
INSERT INTO workspace.workspace (
    id, org_id, data_space_id, owner_id, title, description, created_from, tile_count, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000aa0d11', :org::uuid, :space::uuid, :engineer::uuid,
     'Biên gộp Internet quý 4/2013', 'Doanh thu Internet theo nhóm sản phẩm, rồi phân rã biên gộp.',
     'conversation', 3, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000aa0d12', :org::uuid, :space::uuid, :engineer::uuid,
     'Biên gộp Internet quý 1/2014', 'Cùng cách phân tích cho quý kế tiếp.',
     'conversation', 3, :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, description = EXCLUDED.description,
    tile_count = EXCLUDED.tile_count, deleted_at = NULL;

INSERT INTO workspace.tile (id, org_id, workspace_id, position, kind, title, spec, created_by, updated_by)
SELECT t.id::uuid, :org::uuid, t.ws::uuid, t.pos, t.kind, t.title, t.spec::jsonb, :engineer::uuid, :engineer::uuid
FROM (VALUES
    ('a0000000-0000-0000-0000-000000aa0d21', 'a0000000-0000-0000-0000-000000aa0d11', 1, 'question',
     'Doanh thu Internet theo nhóm sản phẩm', '{"text": "Doanh thu Internet quý 4/2013 theo nhóm sản phẩm"}'),
    ('a0000000-0000-0000-0000-000000aa0d22', 'a0000000-0000-0000-0000-000000aa0d11', 2, 'skill_run',
     'Phân rã biên gộp Internet', '{"skill_id": "a0000000-0000-0000-0000-000000aa0d01", "params": {"period": "2013-Q4"}}'),
    ('a0000000-0000-0000-0000-000000aa0d23', 'a0000000-0000-0000-0000-000000aa0d11', 3, 'text',
     'Kết luận của phiên', '{"text": "Kết luận về biên gộp Internet của quý."}'),
    ('a0000000-0000-0000-0000-000000aa0d24', 'a0000000-0000-0000-0000-000000aa0d12', 1, 'question',
     'Doanh thu Internet theo nhóm sản phẩm', '{"text": "Doanh thu Internet quý 1/2014 theo nhóm sản phẩm"}'),
    ('a0000000-0000-0000-0000-000000aa0d25', 'a0000000-0000-0000-0000-000000aa0d12', 2, 'skill_run',
     'Phân rã biên gộp Internet', '{"skill_id": "a0000000-0000-0000-0000-000000aa0d01", "params": {"period": "2014-Q1"}}'),
    ('a0000000-0000-0000-0000-000000aa0d26', 'a0000000-0000-0000-0000-000000aa0d12', 3, 'text',
     'Kết luận của phiên', '{"text": "Kết luận về biên gộp Internet của quý."}')
) AS t(id, ws, pos, kind, title, spec)
ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, spec = EXCLUDED.spec, deleted_at = NULL;

INSERT INTO workspace.ws_participant (id, org_id, workspace_id, principal_type, principal_id, role)
SELECT p.id::uuid, :org::uuid, p.ws::uuid, 'user', u.id, 'viewer'
FROM (VALUES
    ('a0000000-0000-0000-0000-000000aa0d31', 'a0000000-0000-0000-0000-000000aa0d11'),
    ('a0000000-0000-0000-0000-000000aa0d32', 'a0000000-0000-0000-0000-000000aa0d12')
) AS p(id, ws)
JOIN org.app_user u ON u.org_id = :org::uuid AND u.username = 'helios-admin'
ON CONFLICT (workspace_id, principal_type, principal_id) DO NOTHING;

INSERT INTO skill.skill_suggestion (
    id, org_id, data_space_id, pattern, session_ids, user_count, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa0d05', :org::uuid, :space::uuid,
    '{"steps": [{"kind": "query", "name": "Doanh thu Internet theo nhóm sản phẩm"}, {"kind": "skill", "name": "Phân rã biên gộp Internet"}]}',
    ARRAY['a0000000-0000-0000-0000-000000aa0d11', 'a0000000-0000-0000-0000-000000aa0d12']::uuid[],
    4, 'open', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET pattern = EXCLUDED.pattern, user_count = EXCLUDED.user_count, status = EXCLUDED.status;

DO $$
BEGIN
    IF (SELECT count(*) FROM agent.agent_def WHERE data_space_id = 'a0000000-0000-0000-0000-000000000a01' AND deleted_at IS NULL) < 4 THEN
        RAISE EXCEPTION 'DS-AW is missing its agent team';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent.agent_deployment WHERE id = 'a0000000-0000-0000-0000-000000aa0c51' AND status = 'rolling') THEN
        RAISE EXCEPTION 'the margin agent has no rolling deployment';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent.agent_run WHERE parent_run_id = 'a0000000-0000-0000-0000-000000aa0c65') THEN
        RAISE EXCEPTION 'the lead run has no delegated run';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM skill.skill_version WHERE id = 'a0000000-0000-0000-0000-000000aa0d04' AND status = 'pending_review') THEN
        RAISE EXCEPTION 'no skill version is waiting for review';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM skill.skill_suggestion WHERE id = 'a0000000-0000-0000-0000-000000aa0d05' AND status = 'open') THEN
        RAISE EXCEPTION 'no skill suggestion is open';
    END IF;
END $$;
