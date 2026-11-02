# Research kit: open-source data agents

Notes from reading other agents' code for the dynamic analyst agent
(`docs/superpowers/specs/2026-11-02-dynamic-agent-design.md`, `backend/services/worker_agent/agentic/`).
They quote prompts and code verbatim so a port can start from the original text.

| File | What |
| --- | --- |
| `dataagent-spring-ai-alibaba.md` | spring-ai-alibaba/DataAgent: its graph, planner, plan executor, Python sandbox, report and streaming UI, human review; appendix A has all 17 prompts |
| `agent-repos.md` | Data-Analysis-Agent, gpt-researcher, RD-Agent, WrenAI, ragflow, Memori (databend, OpenMetadata, Airbyte in brief) |

The clones are not committed. Paths in the notes are relative to a shallow clone of each repo
(`git clone --depth 1 <url>`), made on 2026-11-02; WrenAI's `wren-ai-service` was read from a
branch that still has it, as the notes say.
