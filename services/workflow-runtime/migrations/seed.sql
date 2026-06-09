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
      {"id": "i01", "pattern": "\\b(bomb|bom\\s*xăng|chất\\s*nổ|vũ\\s*khí|khủng\\s*bố|giết\\s*người)\\b"},
      {"id": "i02", "pattern": "(?i)\\b(ddos|sql\\s+injection|xss|shellcode|reverse\\s+shell|exploit\\s+code)\\b"},
      {"id": "i03", "pattern": "(?i)(ignore\\s+previous\\s+instructions|act\\s+as\\s+dan|jailbreak|override\\s+system\\s+prompt)"},
      {"id": "i04", "pattern": "(?i)\\b(dump\\s+database|extract\\s+all\\s+(user|data)|leak\\s+all\\s+(data|record))\\b"},
      {"id": "i05", "pattern": "(?i)\\b(fuck|motherfucker|chó\\s*chết|địt\\s*mẹ|đéo\\s*mày)\\b"},
      {"id": "i06", "pattern": "giết\\s*(người|tôi|mày|hắn|nó)"}
    ],
    "output_patterns": [
      {"id": "o01", "pattern": "(?i)(password|passwd|mật\\s*khẩu)\\s*[=:]\\s*\\S{4,}"},
      {"id": "o02", "pattern": "(?i)(api[_\\-]?key|secret[_\\-]?key|private[_\\-]?key)\\s*[=:]\\s*\\S{8,}"},
      {"id": "o03", "pattern": "\\bsk-[A-Za-z0-9]{20,}\\b"},
      {"id": "o04", "pattern": "\\beyJ[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]+\\b"},
      {"id": "o05", "pattern": "(?i)(postgres|mysql|mongodb|redis|mssql)://\\S{10,}"}
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
