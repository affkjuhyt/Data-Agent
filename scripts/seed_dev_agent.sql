-- Seed a minimal PUBLISHED agent so the orchestrator/workflow-runtime can run a
-- turn locally (the Agents table is empty on a fresh DB, and load_agent_config
-- returns None → "Agent chưa được publish" for an unknown id).
--
--   docker exec -i dataagent-postgres psql -U admin -d dataagent < scripts/seed_dev_agent.sql
--
-- agent_id = DEV_AGENT_ID (00000000-0000-0000-0000-000000000030), tenant = Dev
-- Tenant (a0000000-…-001). responder model "responder" routes via the litellm
-- wildcard to the host ollama (see infra/docker-compose.agent-test.yml).
BEGIN;

-- model_id needs the litellm provider prefix ("openai/") — the workflow-runtime
-- responder calls the litellm SDK directly (litellm.acompletion(model=…)), which
-- errors "LLM Provider NOT provided" for a bare name. "openai/responder" → the SDK
-- treats it as an OpenAI-compatible endpoint and routes to the litellm proxy, whose
-- wildcard maps it to ollama.
INSERT INTO "LLMProviders"(id, name, model_id, endpoint_url, type, is_default, is_active)
VALUES ('00000000-0000-0000-0000-0000000000a1', 'local-ollama', 'openai/responder',
        'http://litellm:4000', 'openai', true, true)
ON CONFLICT (id) DO NOTHING;

-- The runtime connects as the least-privilege runtime_writer role, which on a fresh
-- DB lacks SELECT on the agent-config tables → "permission denied" → empty answers.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO runtime_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO runtime_writer;

-- Agents ↔ AgentVersions is a circular FK: insert the agent without a published
-- version first, then the version, then point the agent at it.
INSERT INTO "Agents"(id, tenant_id, name, is_active)
VALUES ('00000000-0000-0000-0000-000000000030',
        'a0000000-0000-0000-0000-000000000001', 'loadtest-agent', true)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "AgentVersions"(id, agent_id, version, status, responder_model_id,
                            llm_config, retrieval_config, memory_enabled)
VALUES ('00000000-0000-0000-0000-0000000000b1',
        '00000000-0000-0000-0000-000000000030', 1, 'published',
        '00000000-0000-0000-0000-0000000000a1', '{}'::jsonb, '{}'::jsonb, false)
ON CONFLICT (id) DO NOTHING;

UPDATE "Agents" SET published_version_id = '00000000-0000-0000-0000-0000000000b1'
WHERE id = '00000000-0000-0000-0000-000000000030';

COMMIT;
