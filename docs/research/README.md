# Research kit: open-source data agents

Notes from reading other agents' code for the dynamic analyst agent
(`docs/superpowers/specs/2026-11-02-dynamic-agent-design.md`, `backend/services/worker_agent/agentic/`).
They quote prompts and code verbatim so a port can start from the original text.

| File | What |
| --- | --- |
| `dataagent-spring-ai-alibaba.md` | spring-ai-alibaba/DataAgent: its graph, planner, plan executor, Python sandbox, report and streaming UI, human review; appendix A has all 17 prompts |
| `agent-repos.md` | Data-Analysis-Agent, gpt-researcher, RD-Agent, WrenAI, ragflow (prompts), Memori; databend has no agent part |
| `ragflow-go.md` | ragflow's Go backend in depth: retrieval and rerank fusion, citations, chunkers, parent/child chunks, knowledge compilation, agentic RAG, the agent canvas runtime; what to port to documents/ and agentic/ |
| `airbyte.md` | the Airbyte protocol, the Python CDK, the low-code YAML connector framework, state and incremental sync, PyAirbyte; what worker_ingest, source_drivers and the provider registry should take |
| `openmetadata-ingestion.md` | OpenMetadata's ingestion (Collate Community License: behaviour only, paraphrased, no code): query-log lineage and usage, profiler, data-quality runner and impact score, PII classification, dbt and semantic-view import, connector framework; gaps it exposed in our scans and a ranked build list |
| `openmetadata-skipped.md` | the three parts first marked "skip", in depth: the Flowable governance workflow engine, auto-approval, Elasticsearch/OpenSearch search; adapt a declarative review policy per proposal kind and risk tier, approval thresholds and guards, search boosts and RBAC-in-query on Postgres FTS + Qdrant |
| `openmetadata.md` | OpenMetadata's entity model (glossary, metric, data product, data contract, lineage, data quality), ingestion and governance workflows |

The clones are not committed. Paths in the notes are relative to a shallow clone of each repo
(`git clone --depth 1 <url>`), made on 2026-11-02; WrenAI's `wren-ai-service` was read from a
branch that still has it, as the notes say.
