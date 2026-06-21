-- Idempotent seed data for dev environment

INSERT INTO "Plans" (id, name)
VALUES (1, 'dev')
ON CONFLICT (id) DO NOTHING;

INSERT INTO "Tenants" (id, name, slug, plan_id, data_residency, is_active)
VALUES ('00000000-0000-0000-0000-000000000001', 'Dev Tenant', 'dev', 1, 'Asia-SE1', true)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "LLMProviders" (id, name, model_id, endpoint_url, type, is_default, is_active)
VALUES
  ('00000000-0000-0000-0000-000000000010', 'Dev LLM (Planner)',   'planner',   'http://litellm:4000', 'chat', false, true),
  ('00000000-0000-0000-0000-000000000011', 'Dev LLM (Responder)', 'responder', 'http://litellm:4000', 'chat', true,  true),
  ('00000000-0000-0000-0000-000000000012', 'Dev LLM (Guardrail)', 'guardrail', 'http://litellm:4000', 'chat', false, true)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "SystemPrompts" (id, tenant_id, name, content, version)
VALUES (
  '00000000-0000-0000-0000-000000000020',
  '00000000-0000-0000-0000-000000000001',
  'Default',
  'You are a helpful assistant for GTEL platform.',
  1
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "Agents" (id, tenant_id, name, description)
VALUES (
  '00000000-0000-0000-0000-000000000030',
  '00000000-0000-0000-0000-000000000001',
  'Default Agent',
  'Default dev agent'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "Workflows" (id, agent_id, name)
VALUES (
  '00000000-0000-0000-0000-000000000050',
  '00000000-0000-0000-0000-000000000030',
  'Default Workflow'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "WorkflowVersions" (id, workflow_id, version, status, published_at)
VALUES (
  '00000000-0000-0000-0000-000000000060',
  '00000000-0000-0000-0000-000000000050',
  1,
  'published',
  now()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "AgentVersions" (id, agent_id, version, status, workflow_version_id, responder_model_id, system_prompt_id)
VALUES (
  '00000000-0000-0000-0000-000000000040',
  '00000000-0000-0000-0000-000000000030',
  1,
  'published',
  '00000000-0000-0000-0000-000000000060',
  '00000000-0000-0000-0000-000000000011',
  '00000000-0000-0000-0000-000000000020'
)
ON CONFLICT (id) DO NOTHING;

UPDATE "Agents" SET published_version_id = '00000000-0000-0000-0000-000000000040'
WHERE id = '00000000-0000-0000-0000-000000000030';

UPDATE "AgentVersions" SET workflow_version_id = '00000000-0000-0000-0000-000000000060'
WHERE id = '00000000-0000-0000-0000-000000000040'
  AND workflow_version_id IS NULL;

INSERT INTO "Guardrails" (id, name, type, conditions, action, priority)
VALUES (
  '00000000-0000-0000-0000-000000000070',
  'Platform Safety',
  'combined',
  $guardrail${
    "input_patterns": [
      {
        "id": "h01", "confidence": "high",
        "pattern": "(?i)(ignore\\s+previous\\s+instructions|act\\s+as\\s+dan|jailbreak|override\\s+system\\s+prompt|bỏ\\s*qua\\s*(hướng\\s*dẫn|quy\\s*tắc|giới\\s*hạn|lệnh)|giả\\s*vờ\\s*(bạn|mày)\\s*là|hãy\\s*đóng\\s*vai|từ\\s*bây\\s*giờ\\s*(bạn|mày)\\s*là|không\\s*có\\s*giới\\s*hạn\\s*nào|bỏ\\s*qua\\s*mọi\\s*(quy\\s*tắc|giới\\s*hạn))"
      },
      {
        "id": "h02", "confidence": "high",
        "pattern": "(?i)\\b(shellcode|reverse\\s+shell|exploit\\s+code|sql\\s+injection|xss\\s+payload)\\b"
      },
      {
        "id": "h03", "confidence": "high",
        "pattern": "(?i)(\\b(dump\\s+database|extract\\s+all\\s+(user|data)|leak\\s+all\\s+(data|record))\\b|xuất\\s*toàn\\s*bộ\\s*(dữ\\s*liệu|database|db)|lấy\\s*hết\\s*(thông\\s*tin|dữ\\s*liệu)\\s*(người\\s*dùng|tài\\s*khoản|user)|liệt\\s*kê\\s*tất\\s*cả\\s*(tài\\s*khoản|người\\s*dùng)|trích\\s*xuất\\s*toàn\\s*bộ)"
      },
      {
        "id": "h04", "confidence": "high",
        "pattern": "(?i)\\b(fuck|motherfucker|chó\\s*chết|địt\\s*mẹ|đéo\\s*mày)\\b"
      },
      {
        "id": "l01", "confidence": "low",
        "pattern": "(?i)(chế\\s*tạo|tổng\\s*hợp|làm\\s*ra|hướng\\s*dẫn\\s*làm).{0,30}(bom|chất\\s*nổ|vũ\\s*khí|chất\\s*độc)"
      },
      {
        "id": "l02", "confidence": "low",
        "pattern": "giết\\s*(người|tôi|mày|hắn|nó|chúng)"
      },
      {
        "id": "l03", "confidence": "low",
        "pattern": "(?i)(hướng\\s*dẫn|cách|dạy\\s*tôi).{0,20}(tấn\\s*công|hack|xâm\\s*nhập|phá\\s*hoại)"
      },
      {
        "id": "l04", "confidence": "low",
        "pattern": "(?i)(ddos|tấn\\s*công).{0,30}(server|website|hệ\\s*thống|máy\\s*chủ)"
      }
    ],
    "llm_enabled": true,
    "allowed_topics": [],
    "max_tokens": 128,
    "timeout": 10
  }$guardrail$::jsonb,
  'block',
  0
)
ON CONFLICT (id) DO NOTHING;
