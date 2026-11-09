# Research kit: open-source data agents

Notes from reading other agents' code for the dynamic analyst agent
(`docs/superpowers/specs/2026-11-02-dynamic-agent-design.md`, `backend/services/worker_agent/agentic/`).
Verbatim quotes come only from permissive sources (Apache-2.0, MIT, CC BY), with the licence and
source named at the top of each document. AGPL, ELv2, Collate and non-commercial sources are
described in our own words, never quoted beyond identifiers, and nothing of theirs goes into the
product; `licences.md` has the matrix and what each licence allows.

| File | What |
| --- | --- |
| `dataagent-spring-ai-alibaba.md` | spring-ai-alibaba/DataAgent: its graph, planner, plan executor, Python sandbox, report and streaming UI, human review; appendix A has all 17 prompts |
| `agent-repos.md` | Data-Analysis-Agent, gpt-researcher, RD-Agent, WrenAI, ragflow (prompts), Memori; databend has no agent part |
| `ragflow-go.md` | ragflow's Go backend in depth: retrieval and rerank fusion, citations, chunkers, parent/child chunks, knowledge compilation, agentic RAG, the agent canvas runtime; what to port to documents/ and agentic/ |
| `ragflow-python.md` | ragflow's Python era (v0.20.5, v0.27.3), what the Go study could not read: structured data (row-as-chunk tables, `use_sql`, aggregate drill-through), DeepDoc table structure, chunk templates, metadata filters, RAPTOR/GraphRAG/TOC; judged against the 04/11/2026 retrieval baseline |
| `airbyte.md` | the Airbyte protocol, the Python CDK, the low-code YAML connector framework, state and incremental sync, PyAirbyte; what worker_ingest, source_drivers and the provider registry should take |
| `licences.md` | the licence of every component studied and what it allows a closed product (copy code, prompts, schemas, or study only); Airbyte's ELv2 parts (PyAirbyte, certified connectors, Connector Builder) studied clean-room; the audit of what this repo took, with fixes |
| `openmetadata-ingestion.md` | OpenMetadata's ingestion (Collate Community License: behaviour only, paraphrased, no code): query-log lineage and usage, profiler, data-quality runner and impact score, PII classification, dbt and semantic-view import, connector framework; gaps it exposed in our scans and a ranked build list |
| `openmetadata-skipped.md` | the three parts first marked "skip", in depth: the Flowable governance workflow engine, auto-approval, Elasticsearch/OpenSearch search; adapt a declarative review policy per proposal kind and risk tier, approval thresholds and guards, search boosts and RBAC-in-query on Postgres FTS + Qdrant |
| `openmetadata.md` | OpenMetadata's entity model (glossary, metric, data product, data contract, lineage, data quality), ingestion and governance workflows |
| `hybrid-specialist-probe.md` | Checklist + DS-AW Ask questions to verify `investigate_numbers` ∥ `investigate_documents` after deploy; agent-eval follow-up |
| `agent-ops-from-system-prompts.md` | Vận hành harness Ask (OP-1…8): clarify, blast-radius, present_gate, stream_contract, specialist cards, operator_view, deep_skill, correction_gate |

The clones are not committed. Paths in the notes are relative to a shallow clone of each repo
(`git clone --depth 1 <url>`), made on 2026-11-02; WrenAI's `wren-ai-service` was read from a
branch that still has it, as the notes say.
