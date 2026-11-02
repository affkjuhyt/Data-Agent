# RAGFlow's Go backend in depth, and what to port into Data Agent's Python

> Quoted material © InfiniFlow and the ragflow authors, Apache-2.0, https://github.com/infiniflow/ragflow at 69fc4c9e.

Source: `infiniflow/ragflow` at commit `69fc4c9e` (2 Nov 2026), shallow clone at
`/private/tmp/claude-501/-Users-thiennlinh-Dev-Projects-Data-Agent/76e267ba-9fd2-4402-80b8-becc5eab55e7/scratchpad/research2/repos/ragflow`.
It is symlinked as `research3/ragflow`. Paths below are relative to that root.

## Three things to know first

**The rewrite is finished.** `AGENTS.md` says the Go module now owns:
- the API, admin, ingestor and syncer modes (`cmd/ragflow_server.go`);
- ingestion, parsing, the agent runtime and the CLI.

The Python directories (`rag/`, `deepdoc/`, `agent/`, `graphrag/`, `api/`) are already gone from this checkout. Everything below is Go, about 497k lines in `internal/`. Many Go comments still cite the Python line they reproduce (for example `rag/nlp/search.py:331`), so the algorithms are the familiar RAGFlow ones, made explicit.

**Python GraphRAG/LightRAG is gone.** There is no Leiden, no PageRank build, no gleaning loop and no community reports in Go. Its replacement is a family of template-driven "knowledge compilers" (Structure, Tree, Mindmap, Wiki, PageIndex) plus an agentic-RAG loop.

**How I read it.** I read retrieval, reranking, chat, citations and deep research myself. Three read-only sub-agents covered ingestion and chunking, the agent canvas, and knowledge compilation plus agentic RAG. I spot-checked their constants against the source: `defaultMaxIterations = 120`, `maxSubAgentDepth = 8`, `DefaultTreeOrder = 4`, `navMergeThreshold = 0.80`, `wikiTopicCandidateThreshold = 0.72` and the GeneralChunker `(100-overlap)/100` threshold all match.

### Our side, for comparison (located with `graphify query`, then read)

- **`backend/services/worker_agent/documents/retrieve.py`:**
  - Postgres `to_tsquery('simple', w1 | w2 …)` (top 40) and Qdrant dense (top 40), fused by RRF with k=60.
  - TEI rerank with `MIN_RERANK=0.2`.
  - Without a reranker, a passage is kept if word overlap is at least 0.6 or the dense score is at least 0.55.
  - Pre-filters: validity, OCR-usable pages, PII-masked text, injection flag.
- **`backend/services/worker_document/processing.py`:**
  - The chunker is specific to Vietnamese legal structure (Phần/Chương → Mục → Điều → Khoản).
  - `MAX_CHUNK_CHARS = 4000`.
  - Text outside an Điều is packed by paragraph.
  - `context_header(title, doc_no, section_path)` is prefixed before embedding.
  - `LayoutBlock(kind, text, box, level)` already exists in the extractor output.
- **`documents/answer.py` and `documents/claims.py`:** claims with a verbatim quote, verified against the passage (fuzzy 0.9), numbered by first use. This is already stricter than RAGFlow's chat path.
- **`worker_agent/agentic/`** has:
  - `engine.py`: prepare → plan → act⟲ → review → compose → persist, with `MAX_REPLANS=2`, `MAX_REVIEWS=1` and `MAX_STEP_RUNS=14`;
  - `graph.py`: our own `StateGraph`;
  - `tools/`: tools whose observations are capped at `MAX_OBSERVATION=3000`;
  - `tools/documents.py`: `search_documents` gives the first 6 passages, 600 characters each, numbered `[n]` by appending to `ws.citations`. Numbers are not deduplicated.
  - `memory.py`: extraction plus BM25 ranking.

---

## 1. Retrieval: hybrid search, fusion and rerank (`internal/service/nlp/`)

### What it does
`RetrievalService.Retrieval` (`retrieval.go:97`) is the single retrieval entry point. Chat, the agent's Retrieval tool and the agentic tools all call it.

### Defaults (verbatim)

| Setting | Default |
|---|---|
| `KNNTopK` | 1024 |
| `KNNNumCandidates` | 2048 |
| `SimilarityThreshold` | 0.2 |
| `VectorSimilarityWeight` | 0.3 (1.0 when `VectorOnly`) |
| `RankFeature` | `{"pagerank_fea": 10}` |
| `RerankCandidatesCount` | 64; the first search fetches this many, and rerank requires page=1 |

### Control flow
1. **`Search`** (`retrieval.go:699`). It filters `available_int=1` and builds a weighted text query with `QueryBuilder.Question` (below). The match expressions are `[matchText, matchDense, fusion]`.
   - The fusion is `weighted_sum` with the fixed pair `"0.001,1"` on Elasticsearch and OpenSearch. The comment explains that the first pass is a *vector recall pass*. The caller's `vector_similarity_weight` is applied afterwards, in reranking. Infinity gets `(1-vw, vw)` directly.
   - `minMatch`: the text leg requires 30% of terms to match (`minimum_should_match`). It requires 0% when vw ≥ 0.8.
2. **Empty-result retry.** If the search finds nothing, it rebuilds the text query with min_match 0.1 and dense similarity floor **0.17**, then searches again. If that also finds nothing and `AllowDenseFallback` is set, it runs dense-only.
3. **`PruneDeletedChunks`.**
4. **`scoreSearchResult`** (`retrieval.go:463`), with `tkWeight = 1 - vw`:
   - **With a rerank model, `RerankByModel`** (`reranker.go:94`):
     - `tsim` = keyword token similarity against tokens built from content + title×2 + important_kwd×5 + question_tks×6 (`titleRepeat=2, importantRepeat=5, questionRepeat=6`).
     - `modelSim` = cross-encoder score, then `NormalizeRerankScores`. If the scores are already in [0,1] they are kept; otherwise min-max normalized; if the span is under 1e-3 they are clipped.
     - **`sim = tkWeight*tsim + vtWeight*modelSim`**, plus rank features.
   - **Without a reranker, `RerankWithKNN` / `RerankStandard`:** `sim = tkWeight*tsim + vtWeight*cosine`. If every vector similarity is 0, `sim` falls back to `tsim`.
   - **Rank features** (`applyRankFeatureScores`): `sim += tag_score*10 + pagerank`. Here `tag_score` is the cosine between the query's tag distribution and the chunk's `tag_feas`, normalized by query norm.
5. **Threshold, sort and page.** The 0.2 threshold is skipped when vw ≤ 0 (term-only scores) and for vector-only search. Then results are sorted (stable) and paged.
6. **Small-to-big** (`RetrievalByChildren`, `retrieval.go:1053`). Hits that carry a `mom_id` are grouped by parent, and the parent row is fetched; parents are stored with `available_int=0`, so search never returns them directly. The output chunk is the **parent text**, with `similarity = mean(child similarities)` and the children's keywords merged in.

### Term weighting (`term_weight.go:351`, `query_builder.go:249`)
This is the original RAGFlow weighted BM25 query, written out:
- Each token weight is `(0.3*idf(freq) + 0.7*idf(df)) * ner(t) * pos(t)`, where `idf(s,N) = log10(10 + (N-s+0.5)/(s+0.5))`, with N = 1e7 for frequency and 1e9 for document frequency.
  - **NER weights:** numbers 2; short letter runs 0.01; corp/loca/sch/stock 3.
  - **POS weights:** r/c/d (pronoun, conjunction, adverb) 0.3; ns/nt 3; n 2.
  - Weights are normalized to sum to 1.
- **Query string.** For each token: `(tk^w "syn1"^(w/4) …)`, using up to 8 WordNet/dictionary synonyms per token. Each adjacent pair also adds a phrase `"a b"^(2*max(w))`. At most 256 tokens.
- **Field boosts:** `title_tks^10, title_sm_tks^5, important_kwd^30, important_tks^20, question_tks^20, content_ltks^2, content_sm_ltks`.
- **Token similarity used in rerank** (`tokensToDict`). Each unigram contributes 0.4·w and each bigram 0.6·max(w). The similarity is the share of the query's weight that the document covers, `Σ_{q∈d} w / Σ w`. So it is coverage, not cosine.

### Chat-path query refinement (`chat_pipeline.go`, "Phase 8")
The steps run in this order, each gated by `prompt_config`:
1. `refine_multiturn` (`FullQuestion`): condense the history into one question.
2. `cross_languages`: translate the query into configured languages.
3. `meta_data_filter`: an LLM produces `{"logic":"and|or","conditions":[{"key","value","op"}]}` from the document-metadata value→doc_id map, which narrows `doc_ids` before search. The prompt is `internal/rag/prompts/meta_filter.md`.
   - Operators: `contains, not contains, in, not in, start with, end with, empty, not empty, =, ≠, >, <, ≥, ≤`.
   - Date ranges become `[≥ start, < next_start]`.
   - Values must match the metadata's value keys exactly.
4. `keyword`: an LLM extracts the top 3 keywords, which are appended to the query (`keyword_prompt.md`: "…top {{ topn }} important keywords/phrases… same language… delimited by ENGLISH COMMA. Output keywords ONLY.").
5. Then come retrieval, children promotion, optional web search (Tavily and others, appended), and optional KG (one synthetic chunk prepended).
6. `CompiledServeChunks` swaps in compiled knowledge.
7. `kbPrompt` packs chunks until 97% of the model's max tokens. The format is `ID: i\n├── Title: …\n├── URL: …\n├── <metadata k>: v\n└── Content:\n…`.
8. `messageFitIn`: if over budget, keep the system message and the last message. Whichever of the two is over 80% of the tokens gets trimmed.

Also: **`useSQL`**. For tables ingested as structured rows (`field_map`), the chat first asks an LLM for SQL over the chunk index, with execution-error and missing-column repair prompts. It falls back to vector search if that fails. We already have a governed semantic layer and SQL experts, so this part is not relevant to us.

### Port to our Python
`documents/retrieve.py` is already structurally close: two legs, fusion, a cross-encoder. What RAGFlow does differently, and what is worth taking:

1. **Mix the reranker score with a lexical coverage term instead of using it alone.** Today we use `sorted(s for s in reranked if s >= MIN_RERANK)`.
   - Proposal: `final = 0.3*coverage + 0.7*norm(rerank)`, where coverage is the weighted share of question terms present in title + section_path + text. Our `overlap()` is the unweighted version.
   - RAGFlow's comments show the reason. Cross-encoders mis-rank number- and code-heavy passages; a term-coverage floor fixes that. For Vietnamese legal text, "Điều 12" and "Khoản 3" are exactly these tokens. Give digits and article numbers a weight of 2, as RAGFlow's `nerWeight` does for numbers.
   - Keep `MIN_RERANK`, but apply it to the mixed score. Port `NormalizeRerankScores`: TEI returns raw logits for some models, and min-max normalization makes the threshold meaningful.
2. **Empty-result relaxation.** When both legs return nothing (or nothing passes the bar), retry once with an AND→OR relaxation (we already OR) and a lower dense floor. RAGFlow uses 0.17; ours would be about 0.45 against `MIN_DENSE=0.55`. The retry must log that it is relaxed, so "not found" stays honest.
3. **Weighted text query.** Postgres `ts_rank` with an OR of all words treats "quy định" like "lãi suất". Two cheap steps:
   - Use `setweight()` on a `tsvector` over `title || section_path` (A) and text (D). That needs a migration and an index change.
   - Drop high-df words using a df table computed from `chunk` (a nightly `ts_stat`).
   The full RAGFlow IDF+NER+POS weighting needs their dictionaries; skip it.
4. **Skip:** the `0.001,1` ES fusion workaround, Infinity specifics, and `pagerank_fea`/tag rank features (we have no curated tag sets). RRF k=60 is a fine replacement for their weighted sum.

---

## 2. Citations

There are three citation mechanisms in the Go tree, used in different paths.

### a. Prompted markers plus repair (chat path)
- `CitationPrompt()` is `internal/rag/prompts/citation_prompt.md`, about 128 lines.
  - Format rules: `[ID:i]` or `[ID:i] [ID:j]`, "NEVER merge consecutive citations into a range", "Place citations at the end of sentences, before punctuation", "Maximum 4 citations per sentence", "DO NOT cite content not from <context></context>".
  - It also lists what must be cited: numbers, dates, causal claims, comparisons, definitions, attributions, forecasts, and each list element.
- After generation (`service/citation.go`):
  - `RepairBadCitationFormats` rewrites `(ID: 12)`, `【ID:12】`, `ref12`, `(**ID: 12**)` and Arabic-Indic digits into `[ID:12]`.
  - `ExtractCitationMarkers` keeps only indices below `len(chunks)`, deduplicated.
  - `doc_aggs` is filtered to cited documents.
  - A streaming `citationStreamFilter` holds back a partial tail (`[ID:4` mid-stream) so half-markers never flash.

### b. Post-hoc insertion by embedding (`InsertCitations`)
This runs only when the answer has **no** markers at all.
1. Split the answer into sentences. Code fences are kept whole. The regex is `([^\|][；。？!！,؛؟.\n]|[a-z؀-ۿ][.?;!،؛؟][ \n])`, and fragments shorter than 5 characters are dropped.
2. Embed the sentences and take the cosine against each chunk vector.
3. `findCitations`: start at threshold 0.63. Per sentence, the matches are chunks scoring above 0.99×(row max), and the row max must reach the threshold. Cap at 4 per sentence. If nothing is cited at all, multiply the threshold by 0.8 and repeat while it stays above 0.3.
4. Append `[ID:j]`, each chunk at most once overall.

### c. Agentic run-level handles (`internal/agentic_rag/evidence_registry.go`, `citations.go`)
- **Stamping.** Every passage *shown* to the model is stamped `ref=n` in first-seen order, and the same chunk keeps its number across tool calls.
- **Deduplication.** `ServeText` deduplicates by chunk id plus `sha256(text)[:16]`. A repeat is replaced by a pointer ("already shown as ref n"). The comment says "a third to a half of all served passages were repeats".
- **Publishing.** `ExpandCommaCitations` splits `[ID:47, ID:115]`. `ResolveCitations` renumbers run handles into 0-based positions of the published `reference.chunks`, in first-citation order, and **drops handles that never reached the context**.
- **Agent canvas grounding pass** (`agent/component/agent.go: applyCitationGrounding`). A second LLM call uses the "You are an agent for adding correct citations to the given text by user…" system prompt, with sources capped at `citationGroundingSourceTokenBudget = 30000`. The result is used only if it still has real text once markers are stripped.

### Port to our Python
- **Our verified path (`documents/answer.py`) is better than (a) and (b).** Claims carry a passage number and a verbatim quote, and both are checked. Keep it. Do not port the embedding-based insertion: it makes citations look present without proving them, which goes against our invariant "a trust label on every answer".
- **Port (c) into the agentic engine.** This is the clear gap. In `agentic/tools/documents.py`, every call appends new `[n]` numbers to `ws.citations`, so the same chunk found by two steps gets two numbers, and the reporter sees duplicate text twice.
  - Add an `EvidenceRegistry` to `Workspace` (`agentic/workspace.py`) with three methods:
    - `register(chunk_id, text) -> int` returns a stable number; first seen wins.
    - `serve(chunk_id, text) -> str` returns the full text the first time and `"(đã hiển thị ở [n])"` after.
    - `resolve(answer) -> (answer, citations)` renumbers cited handles 1..k in first-citation order, drops unknown handles and filters `ws.citations` to the cited ones.
  - This is about 80 lines plus tests.
- **Port `RepairBadCitationFormats` (≈ 20 lines) into whatever parses the reporter's text.** It should normalize `(1)`, `[ 1 ]`, `【1】` and `[1-3]` to `[1] [2] [3]` before validation.
- **Port the stream tail filter if blocks stream text.** It stops `[1` from flashing mid-stream.

---

## 3. Deep researcher (`internal/service/deep_researcher.go`)

### What it does
It is the chat "reasoning" mode: recursive query decomposition with a sufficiency judge.

```
_research(question, query, depth=3):
  kb = retrieve(query) (+ web if enabled, + KG); merge into shared chunk set
  suff = LLM(sufficiencyCheckTemplate)   -> {"is_sufficient","reasoning","missing_information":[...]}
  if sufficient: return content
  mg = LLM(multiQueriesGenTemplate)      -> {"reasoning","questions":[{"question","query"}] (1-3)}
  parallel for each: _research(q.question, q.query, depth-1)
```

- Knowledge for the judge is packed to half the model's context.
- `genJSON` retries the JSON parse twice.
- Progress strings ("Searching by `q`…", "Checking the sufficiency…") are streamed.

Prompt rules worth copying verbatim:
- Sufficiency: "If the retrieved content contains key information needed to answer the query, judge as sufficient… If key information is missing, judge as insufficient (false), and list the missing information."
- Multi-query: "generate 2-3 complementary queries… Focus on different missing information points… Each query length is between 1-5 keywords… MUST be in the same language as the retrieved content… DO NOT generate question and query that is similar to the original query."

The agentic-RAG authors later argued *against* a sufficiency check before answering. Their comment says it "refuses pages that hold every needed fact… costs the loop 300-600k tokens". They replaced it with answer → deterministic floors → verify (section 7).

### Port to our Python
- Our `engine.review` (max 1) is already "ragflow's sufficiency check", in the engine's own docstring. Two refinements:
  1. Make the reviewer return `missing_information[]`, and have the replanner turn each missing item into a separate `search_documents` / `query_metric` step **run in parallel**. Today the plan is walked step by step.
  2. Keyword-style sub-queries (1–5 words, same language as the corpus) for document search. They suit our Postgres FTS leg far better than full questions do.
- Skip the recursion depth 3 with fan-out 3. That can reach 27 retrievals plus 13 LLM judge calls, which is too expensive for our Gateway budget. Our `MAX_STEP_RUNS=14` already bounds it.

---

## 4. Ingestion pipeline and task executor (`internal/ingestion/`, `internal/service/ingestion_task_service.go`)

### What it does
One task row per document drives a component DAG: `File → Parser → <X>Chunker → Extractor → Tokenizer`. It is compiled to an Eino workflow from a built-in template (`internal/ingestion/pipeline/template/*.json`), selected by `parser_id` (naive, book, laws, paper, manual, qa, table, presentation, one, picture, audio, email). The document's `parser_config` is passed as per-component `override_params`.

### Queue and state
- **State machine** (`validateTransition`): CREATED → SCHEDULED/RUNNING/STOPPED/FAILED; RUNNING → STOPPING/COMPLETED/FAILED; FAILED or STOPPED → CREATED (retry).
- **Queue.** NATS JetStream pull consumer on `tasks.RAGFLOW`, with `MaxDeliver 16`, `AckWait 60s`, `BackOff [5s,15s,30s,60s]`.
  - The worker sends an `InProgress()` heartbeat every 2 s. Concurrency defaults to `NumCPU`.
  - A Redis key `<taskID>-cancel` is polled every 500 ms.
  - A redelivered message for an in-flight task only renews the lease.
- **No component checkpoint and resume.** `pipeline.go` says: "Resume-from-checkpoint is intentionally not supported". Replays are made cheap by memoizing instead:
  - `chunkcache` stores each chunk's LLM and embedding results in Redis under `xxhash(kind, model_id, chunk_id, config…)`, with TTL 7 days. It is purged after a durable index write.
  - **Deterministic chunk ids:** `ChunkID = %016x(xxhash64(text + doc_id))`. Re-indexing overwrites instead of duplicating.
- **Writes.** Bulk batches of 32. A partial failure is compensated by deleting what was written.
- **Retries.** The extractor LLM gets 3 tries with 2 s backoff and a 180 s timeout per call; 4xx is not retried. The embedder gets 6 tries with backoff 5 s → 120 s and 0.2 jitter.

### Port to our Python
`worker_document` already has `handler.py`, `events.py`, `reprocess.py` and outbox events. Worth taking:
- **Content-addressed chunk ids**, for example `uuid5(NAMESPACE, f"{document_id}:{sha256(text)}")`, as both the Postgres `chunk.id` and the Qdrant point id. Reprocessing (`reprocess.py`) then becomes an upsert plus deleting ids that are no longer produced, rather than delete-all-and-reinsert.
  - Check this against `page_hash` and `source_hash`, which already exist in `processing.py`; they may cover part of it.
- **An LLM and embedding memo keyed by `(kind, model, chunk_hash, prompt_hash)`** in Postgres or Redis. Once we add chunk enrichment (section 6), a re-run after a crash or a model-unrelated change costs nothing. It is cheap and important.
- **Skip:** the Eino DAG for ingestion. A fixed Python function pipeline with a `parser_id` → config table is enough; RAGFlow needs the DAG only because users edit ingestion pipelines in the UI.

---

## 5. Parsing and DeepDOC (`internal/ingestion/component/parser*.go`, `internal/parser/parser/`, `internal/deepdoc/parser/pdf/`)

### What it does
- **Family routing:** pdf, docx, slides, spreadsheet, csv, html, markdown, text/code, epub, json, email, audio, video, image.
- **PDF `parse_method`:**
  - local DeepDOC (ONNX layout, OCR and table-structure models via cgo);
  - remote MinerU, Docling, PaddleOCR, TCADP, SoMark or OpenDataLoader;
  - a vision LLM.
- **Output.** `ParseResult` holds a list of `DocItem`s: `TextItem`, `TableItem`, `ImageItem`, each with `doc_type_kwd` (text/table/image), `layout` (title, text, figure, table, caption…), `page_number` and `positions [[page,left,right,top,bottom]]`.

DeepDOC page flow:
1. Extract characters at 216 DPI.
2. Run full OCR if the text layer is garbled.
3. Run layout analysis and table-structure recognition.
4. Retry at up to 9× zoom if no boxes are found.
5. Assemble with `AssignColumn → Dedup → FilterWatermark → RemoveHeaderFooter → RemoveTOC → TextMerge → ReadingOrder → Dehyphenate → VerticalMerge → MergeTablesAcrossPages → … → MergeCaptions`.

**Header and footer removal** (`layout/toc_header_footer.go`) runs in three tiers:
1. Layout tags in margin zones, page-number patterns.
2. Recurrence on at least `(numPages+1)/2` pages, also tracked by parity (odd/even) and locality.
3. Zones at 10% and 90% of page height, widened to 14% and 86% when separated from the body by a gap of at least 18 pt.

A further "sequence track" catches bare numbers stepping by 1 at a stable y across 3 or more pages. The roman-numeral page-number regex:
```
(?i)^([0-9０-９]{1,4}|((l|xl|x{1,3})(ix|iv|v?i{1,3}|v)?|ix|iv|v?i{1,3}|v))$
```

**TOC removal** combines PDF outline pages with a box-shape test: at least 4 short boxes, at least 3 entries, page ≤ 10 only. Pages with more than 2 prose boxes of over 60 characters are never dropped. The dot-leader regex:
```
(\.{2,}|…{2,}|⋯{2,}|·{2,})\s*([0-9０-９]{1,4}…)
```

**Vision enhancement** (`vision_enhancement.go`):
- Figures and tables are sent to the tenant's image-to-text model with a two-mode prompt: structured fields (Visual Type / Title / Axes / Legends / Data Points…) or verbatim prose. Concurrency 10, timeout 30 s.
- An image's description is appended to its text.
- A table gets a separate "note" item inserted right after it.

### Port to our Python
- **Header/footer recurrence and TOC removal go in `worker_document/processing.py`, before `_paragraphs`.** We get `LayoutBlock.box` from the text layer and from OCR TSV. Repeated running headers ("CÔNG BÁO/Số …/Ngày …", the bank's name, "Trang 3/12") currently end up inside chunks and pollute both FTS and embeddings. A recurrence test is about 100 lines: normalize digits to `#`, take blocks in the top or bottom 10% of the page, and drop those seen on at least half the pages. It needs no models.
- **Table note items.** If we add VLM descriptions for tables and figures (we OCR pages today), keep the description as a **separate chunk linked to the table**, not mixed into it, as RAGFlow does. Retrieval can then cite the table and its description separately.
- **Skip DeepDOC's ONNX stack.** If layout quality becomes the bottleneck, call Docling or MinerU as a sidecar; RAGFlow itself offers them as `parse_method` options.

---

## 6. Chunking strategies (`internal/ingestion/component/chunker/`)

### Shared machinery
- Every chunker is wrapped by `imageUploadDecorator`, which sets the deterministic id and uploads image crops to `<kb>-<chunk_id>`.
- Tokens are counted with tiktoken `cl100k_base`.
- Inline position tags `@@page\tl\tr\tt\tb##` are stripped.

### GeneralChunker ("naive", `general.go`)
- **Defaults:** `chunk_token_size 512`, delimiters `["\n","!","?",";","。","；","！","？"]`, `overlapped_percent 0` (clamped to 0–90).
  - Backtick-wrapped custom delimiters switch to "one chunk per segment, no merge".
- **Merge** (`general.go:1222`): split on delimiters, keeping the delimiter, then merge greedily. A new chunk starts once the current one exceeds `target*(100-overlap)/100` tokens.
  - A unit larger than the target stays whole.
  - Tables and images are atomic chunks.
  - Overlap prepends the last `overlap%` of the previous chunk's characters.
- **Media context** (`attachGeneralMediaContext`): table and image chunks get `context_above` / `context_below`, made of whole neighbouring sentences up to `table_context_size` / `image_context_size` tokens. The Tokenizer indexes `context_above + text + context_below`.
- **Parent/child** (`children_delimiters`): each merged chunk is split into children carrying `mom = parent text`.
  - At persist time (`ingestion/task/indexdoc/parent_child.go`) the parent becomes a row with `id = xxhash(dataset\0doc\0mom)` and `available_int=0`; children get `mom_id`.
  - At retrieval, children are promoted to the parent with their mean similarity (section 1).
- **Spreadsheets:** split on whole rows, never inside a rowspan. Header and caption are repeated in every part and not counted against the budget.

### TitleChunker family (`title.go` → `group.go` / `hierarchy.go`)
It covers the book, laws, paper and manual templates. The method is chosen per template:

| Template | method | hierarchy | include_heading_content |
|---|---|---|---|
| book | hierarchy | 5 | true |
| laws | hierarchy | 2 | false |
| paper | group | 0 | false |
| manual | group, after a (page, top, left) sort | – | – |

**Level resolution** (`resolveTitleLevels`):
1. The PDF outline first: character-bigram similarity above 0.8 to outline entries, used only if the outline covers more than 3% of lines.
2. Otherwise pick, among five regex families, the one with the most hits. Lines that look like bullets, or are longer than 12 words (or 32+ characters with no space), are not counted. The families are:
   - Markdown `^#[^#]`, …;
   - Chinese `第…编/章/节/条`;
   - numeric `[0-9]{1,2}\.[0-9]{1,2}…`;
   - Chinese numerals;
   - English `PART (ONE|…)`, `Chapter (I+V?|…)`, `Section [0-9]+`, `Article [0-9]+`.
3. Fallbacks: layout "heading" from Markdown/HTML/DOCX (trusted), then the PDF model's "title" label, filtered by `notTitle` (punctuation `[,;，。；！!]` or the length rule).

**Group method.** The target level is the `hierarchy`-th smallest heading level, or the most frequent one when hierarchy is 0.
- Each heading at or above the target level opens a section.
- Records merge while the group is under 32 tokens, or under 1024 tokens and within the same section.
- `enforceTitleTokenCap` (default 512, range 128–8000) re-splits on sentence boundaries `([。!?？；！\n]|\. )`, with a hard split as a last resort.

**Hierarchy method.** Build a stack tree of headings, then emit by DFS. Each chunk is `ancestor headings + node title + body`; when `include_heading_content=false`, only leaves emit. A cap split re-prepends the heading prefix to every piece. `root_chunk_as_heading` prepends the document's root chunk to every chunk.

### The other chunkers
- **QAChunker:**
  - Q/A pairs from CSV/TSV, Markdown (the heading stack is the question, the body the answer), HTML tables or spreadsheets.
  - Prefixes such as `Q:`, `问题：` and `Question` are stripped.
  - Output text is `"Question: {q}\tAnswer: {a}"`, and **the lexical fields index only the question**.
- **TableChunker:** one chunk per data row, rendered as its own HTML `<table>` with the full header rows and caption.
- **PageChunker** (presentation): one chunk per slide.
- **OneChunker:** the whole document is one chunk.
- **TokenChunker:** a port of `naive_merge`.
- **No "resume" chunker exists in Go.**

### Port to our Python (`worker_document/processing.py`)
Our chunker is excellent for Vietnamese legal documents (Điều/Khoản aware, cross-page) and naive for everything else: paragraphs packed up to 4000 characters. RAGFlow's design tells us how to generalize without losing that.

1. **A generic heading hierarchy** — the most valuable chunking port. We already have `LayoutBlock(kind="heading", level=n)` from DOCX styles and HTML (`extractors.py: _style_level`, `html_blocks`). Port `hierarchy.go`:
   - build the stack of headings;
   - chunk = section text, with `section_path = " > ".join(ancestors)` (we already store `section_path` and put it in `context_header`);
   - cap at about 512 tokens (our 4000 characters is roughly 1000+ tokens), splitting on sentence boundaries and repeating the heading prefix.
   - For PDFs with no style levels, add the regex-family vote. Vietnamese families: `^(Phần|Chương)\s+[IVXLC\d]+`, `^Mục\s+\d+`, `^Điều\s+\d+`, `^\d+\.\s`, `^[a-zđ]\)\s`, `^\d+\.\d+(\.\d+)?\s`, `^[IVX]+\.\s`, Markdown `#`. Pick the family with the most hits, as `selectLevelGroup` does.
   - The legal path stays a specialized family whose level 3 (Điều) is the atomic unit.
2. **Parent/child, small-to-big.** Index Khoản-level (or about 128-token sentence-group) **children** in Qdrant and FTS, and keep the Điều as the **parent** that is shown and cited.
   - Schema: `chunk.parent_id` nullable; parents flagged `searchable=false`.
   - In `retrieve.py`, after reranking children, group them by `parent_id`, take the mean (or max) score and return the parent text, with the best child's quote span highlighted.
   - This removes the trade-off in our `MAX_CHUNK_CHARS` comment between precise matching and whole-article citation.
3. **Tables as atomic chunks with neighbouring context.** Neighbouring sentences become `context_above/below`; they are embedded but not cited. Very large tables are split by rows with the header repeated (port `splitLargeTable`).
4. **QA chunker for FAQ-type documents.** Index the question only for lexical match and embed the question plus the answer. Cheap, and a big win when users upload FAQs.
5. **Skip:** Chinese-specific level families, `@@…##` position tags (we have `page_from`/`page_to` columns), and the operator engine in `internal/parser/chunk` (used only by the CLI).

---

## 7. Tokenizer and enrichment fields (`tokenizer.go`, `extractor.go`)

### What gets written per chunk
- **Lexical fields:**
  - `content_with_weight` (display text);
  - `content_ltks` / `content_sm_ltks` (coarse and fine tokens), computed from the summary if one exists, otherwise from `context_above + text + context_below`;
  - `title_tks` (document name with its extension stripped);
  - `important_kwd` / `important_tks` (LLM keywords, comma-split);
  - `question_kwd` / `question_tks` (LLM questions, one per line).
- **Position fields:** `page_num_int[]`, `top_int[]`, `position_int` (N×5), `chunk_order_int`.
- **Embedding.** The text has table tags stripped by `</?(table|td|caption|tr|th)( [^<>]{0,12})?>`. The document name is embedded once, and each stored vector is **`0.1*title_vec + 0.9*content_vec`** (`filename_embd_weight=0.1`). Embedding batch size is 16 and the limit 2048 tokens.

### Extractor (all off by default in every template)
Temperature 0.2, 10 concurrent calls, results memoized per chunk.
- **Keywords:** "Extract the most important keywords/phrases… top {{ topn }}… same language… delimited by ENGLISH COMMA. Output keywords ONLY."
- **Questions:** "propose the top {{ topn }} important questions… SHOULD NOT have overlapping meanings… cover the main content… One question per line. Output questions ONLY."
- **Summary:** replaces the content tokens.
- **Metadata:** schema-driven extraction ("strict evidence… no inference… `{}` if empty… JSON only"). Results are filtered to the declared keys and merged up to document metadata.
- **Tagging:** a lexical match against a user tag set, with an LLM fallback that scores tags 1–10, stored in `tag_feas` and used as a rank feature.

Retrieval uses all this through the field boosts `important_kwd^30, question_tks^20, title_tks^10, content^2`, and through the rerank token repeats (section 1).

### Port to our Python
1. **"Doc2query" enrichment** in `worker_document` after chunking, behind a per-collection flag. One Gateway call per chunk (or a batch of about 5) returns `{"keywords":[…≤8], "questions":[…≤3]}` in Vietnamese.
   - Store them in `chunk.keywords text[]` and `chunk.questions text[]`, and add them to the FTS document at weight A or B.
   - Also embed `questions` as **extra Qdrant points** (payload `chunk_id`, `kind="question"`) that resolve to the chunk. Do not concatenate them into the chunk vector.
   - User questions are phrased like generated questions, so recall on "how do I…" queries rises sharply. Memoize as in section 4.
2. **Document-title vector mixing.** We already prefix `context_header` (title | Số | section_path), which is a stronger form of the same idea. Nothing to port.
3. **Schema-driven metadata extraction**, then the metadata filter at query time (`meta_filter.md`). Our documents have `doc_no`, `effective_from/to` and `supersedes`, extracted deterministically in `validity.py`. A per-collection metadata schema (issuer, document type, product line) plus the filter-condition generator would give "chỉ văn bản của NHNN năm 2025" proper pre-filtering. This is medium value, after the above.
4. **Skip:** `content_sm_ltks` fine-grained tokens (a Chinese segmentation artifact) and tag rank features.

---

## 8. Agentic RAG (`internal/agentic_rag/`)

### What it does
The chat "smart-reasoning" mode (`reasoningLevelAgentic = 5`) runs a ReAct agent over corpus tools. Templates live in `conf/agentic_rag.yaml`, which is hot-reloaded; the shipped tools are `think, todo_write, grep_chunks, search_bm25_chunks, search_semantic_chunks, list_chunks, run_javascript, search_metadata`. Graph tools are injected only when compiled rows exist.

System prompt themes (quoted): "Evidence-First"; "Navigate before you scan"; "once a locate step points at a document, read it with list_chunks before answering"; "Stop when… new reads that stop changing the answer"; "end every line that states a corpus- or web-backed fact with… `[ID:n]`".

### Control flow (`agent.go`)
1. **Pre-loop claim prefetch**, only when compiled claim rows exist:
   - Hybrid search returns the top 10 anchor chunks.
   - All claims compiled from those chunks (up to 48 per chunk) plus a claim-text KNN (top 50) are gathered.
   - `answerFromClaims` returns `{"answerable","answer"}`.
   - Deterministic floors: at least one `[ID:n]`, and a `derived_from:` line citing at least 2 handles.
   - A verification call checks "Check ONE thing: is this candidate answer the answer to the question that was asked? … Do not rewrite the answer" and returns `{"supported","reason"}`.
   - If it passes, the turn ends without the loop.
2. **The loop.** Eino `ChatModelAgent` with `defaultMaxIterations = 120`, same-round parallel tool calls, and an unknown-tool handler instead of an abort.
3. **Recovery.** If the final message is empty, reuse the last non-empty assistant message. Otherwise `finalizeAnswer`:
   - 90 s timeout, evidence capped at 40,000 runes;
   - instruction: "You have reached the end of your investigation. Based strictly on the evidence above, produce your final answer… with the [ID:n] handles";
   - retry once if the answer is under 12 runes.

### Tools and constants

| Tool | Behaviour and constants |
|---|---|
| `grep_chunks` | One POSIX ERE, case-insensitive, pushed down to the engine's regexp; max 30 results |
| `search_bm25_chunks` | 1–5 queries, pure BM25, topN cap 50 |
| `search_chunks` | Hybrid: topN 12, threshold 0.2, keyword weight 0.7, candidate pool 4×topN |
| `search_semantic_chunks` | Vector-only. Description: use it after 3 failed lexical queries, "write each query as a description of the thing sought" |
| `list_chunks` | Deep read of one document: 1–20 anchor chunk ids, up to 100 neighbours in `chunk_order_int` order; returns compiled claims first, `raw=true` for original text |
| `search_metadata` | Metadata predicate → doc_ids scope |

- Snippet windows: whole chunk if ≤ 600 runes, otherwise up to 1200 runes with 240 runes of context.
- Results render as XML `<search_results>`, with claims already shown this turn suppressed.

### The decision filter (`decision_*.go`, `decisionhead/`)
It is **not** a stop judge. It prunes the **tail** of each tool result before the model sees it:
- `keepTopN = 3`: the first three results are never judged. The comment says zero exemption "cost the most tokens (+48.7%)… and still lost most of the gold-document evidence".
- **Judges**, chosen per run:
  - a remote yes/no "decision model" (`MinProb 0.30`, 8 s timeout, step-aware phrasing when the tool call carries an `intent` argument);
  - a local ONNX head (512-token segments, `MinProbability 0.6`).
- A circuit breaker opens after 3 consecutive failures for 60 s, during which everything passes.
- **Safety invariant:** unjudged evidence is kept.

### Port to our Python (`worker_agent/agentic/`)
1. **The evidence registry** (section 2c). Take it.
2. **A tail filter on `search_documents` results, using the reranker we already have.** Always keep the top 3; drop the rest below a rerank threshold that is stricter than retrieval's.
   - It shrinks observations, so `MAX_OBSERVATION=3000` holds more distinct evidence.
   - If TEI is down, keep everything: their breaker semantics.
3. **Split the documents tool:**
   - `search_documents(query)`: hybrid, as today.
   - `grep_documents(pattern)`: Postgres `~*` over `chunk.text`, scoped by the same pre-filters, max 30. Exact numbers, codes and "Điều 12" are poorly served by embeddings.
   - `read_document(chunk_id, neighbours=n)`: neighbours by `(document_id, page_from, ordinal)`.
   The deep-read tool matters most. Legal answers often need Khoản 1–3 around the hit, and today the model can only re-search.
   - **Respect our retrieval invariants:** every new tool must go through the same base query (validity, PII mask, injection flag, OCR-usable pages). Refactor `retrieve.py`'s `base` into a shared `_eligible_chunks()`.
4. **The "answer from compact evidence first, verify, else loop" pattern.** It maps onto our claims (`documents/claims.py` already produces quote-verified claims per passage). For document questions:
   - prefetch the claims of the top passages;
   - try a one-shot answer that must cite at least one claim;
   - run one verification call;
   - only then fall into plan/act.
   This could make many document-only turns one or two LLM calls instead of the full graph.
5. **Skip:** 120 iterations (keep our 14), the goja JS sandbox (we have `run_python`), the remote decision model and the ONNX head.

---

## 9. Knowledge compilation: graph, tree/RAPTOR, wiki, navigation (`internal/ingestion/component/knowledge_compiler/`, `internal/ingestion/knowledge_compile/`)

### Structure compiler (graph, timeline, list)
1. **Batching.** Chunks are packed into token batches (about 0.5× the context), rendered `[CHUNK_ID: id]\n…\n[END_CHUNK]`.
2. **Two-stage extraction at temperature 0.1:**
   - Entities first.
   - Then relations, with "## Known Entities" and the rule "Only create relations between entities listed in 'Known Entities'". Stage 2 is skipped if stage 1 found nothing.
   - Every item returns `source_chunk_ids` and `evidence[{quote, chunk_id}]`.
   - No gleaning rounds.
3. **Evidence gate.** Each quote must occur verbatim, after normalization, in the cited chunk or failing that in any chunk of the batch. Offsets are recorded. In `soft` mode an item without evidence is kept; in `hard` mode it is dropped.
4. **Entity resolution.**
   - An exact normalized name, or a cosine of at least 0.99, sends the pair to an LLM merge judge: "merge two JSON objects representing the same entity… Do not invent new information".
   - On a merge, the existing id is kept and `source_chunk_ids` are unioned. An alias map rewrites relation endpoints, and self-loops are dropped.
   - Dataset-level merging uses a KNN threshold of 0.85.
5. **Chains** (timelines). An edge with more than one predecessor, or a cycle (strongly connected components), is sent to an LLM correction call: "must form a strict linear chain… Do not invent new relations".

**Legacy KG retrieval** (`internal/service/graph/`):
- LLM query → `{answer_type_keywords, entities_from_query}`.
- Entity, type and relation searches.
- Scoring: `sim × pagerank`, n-hop boost `sim/(2+i)`, ×2 for an entity found both ways.
- The top 6 entities and 6 relations become CSV blocks, plus 1 community report, as one synthetic chunk prepended to context and not citable.

**`graph_explore` agent tool** (no LLM call):
- Seed: embed the query, KNN (cosine ≥ 0.8), re-sort by `mention_count`, keep the top 2.
- Walk 2 hops, up to 128 neighbours per hop.
- Return the entities' and relations' **source chunks** as citable passages, at most 6, 1500 runes each.

### Tree compiler (RAPTOR as implemented, `tree/raptor.go`, `watershed.go`)
- **Not UMAP+GMM.** The "watershed" works on cosine similarity between *adjacent* chunks in document order.
  - The threshold is the `100/treeOrder` percentile (`DefaultTreeOrder=4`, so the 25th percentile).
  - A new cluster starts wherever adjacent similarity falls below it. O(N), contiguous segments.
- **Top-down.** Each cluster is summarized, then re-segmented into sub-clusters.
- **Summary prompt** (verbatim): "Please summarize the following paragraphs. Be careful with the numbers, do not make things up. … give a title at the first line of your summarization. Must be in the same language as the paragraphs." `max_token` is 512.
- **Optional claim view.** Atomic claims are harvested per chunk, 4 chunks per call: "Self-contained… Faithful… Atomic", quotes under 240 characters. The claim digest's embedding replaces the chunk's embedding for clustering.
- Nodes are stored as rows next to the chunks, with `compile_kwd`, `level` and `source_chunk_ids`. The ordinary retriever excludes them; agent tools serve them through lineage, "compiled rows whose `source_chunk_ids` contain recalled chunk ids".

### Wiki compiler (about 3.3k lines)
The pipeline is MAP → REDUCE → PLAN → RECONCILE → REFINE:
1. **MAP.** Each batch of 2048 tokens yields entities, concepts, claims, relations and topics, every item citing a chunk. The map results are cached per chunk version.
2. **PLAN.** Target page count is clamp(8, items/3, 60). Items are grouped by embedding (threshold 0.72), and the LLM assigns pages.
3. **RECONCILE** against existing pages, using KNN top-3:
   - score ≥ 0.92 → update the page;
   - score < 0.78 → create a page;
   - in between, the LLM decides.
4. **REFINE.** One writer call per page.
   - The merge prompt "preserves all factual content from both versions".
   - Shrink guard: if the merged page falls below 0.7× its length, the merge is conservative.

Wiki answers then **backfill citations** to the source chunks through lineage (`internal/service/wikisearch`).

### Dataset navigation (`service/nlp/datasetnav.go`)
An online topic → document tree is built from document summaries:
- similarity ≥ 0.80 → join the cluster (the LLM merges its description);
- ≥ 0.65 → descend;
- ≥ 0.50 → new sibling;
- otherwise → new root.

Limits: depth 6, fan-out 64, 50 documents per leaf. The authors note the model rarely chose the navigation tools.

### Scheduling
- After its own compile, each document publishes an event; a MySQL backlog per knowledge base is the source of truth.
- A consumer leases a knowledge base (32 documents per claim) and reconciles the last event per document, with tombstones for deletes.
- A wiki becomes "dirty" on chunk edits, with a 20 s trailing debounce; only the affected pages are recompiled.

### Port to our Python
- **We already have the key idea.** `documents/claims.py` produces subject/attribute/value claims with verbatim quotes, checked at 0.9, and opens value conflicts across documents. That matches RAGFlow's claim harvester with its evidence gate, and goes further (conflict detection, steward resolution). Do not port the Structure compiler wholesale.
- **Worth adding later, in this order:**
  1. **Claims as retrieval objects.** Embed claims as Qdrant points with `source_chunk_ids` and use them for the prefetch answer (section 8.4). This is RAGFlow's newest and most measured path.
  2. **Watershed section summaries** for long non-legal documents (reports, manuals). About 30 lines of numpy plus one Gateway call per segment, stored as `chunk.kind='summary'` with lineage to its source chunks and excluded from normal retrieval. Serve them only when a question asks for an overview ("tóm tắt báo cáo"). L effort overall because of schema, UI and evaluation, not the algorithm.
  3. **Lineage-based "compiled rows".** One table `compiled_item(kind, text, source_chunk_ids uuid[], embedding_ref)` with a GIN index on `source_chunk_ids` is the general pattern behind claims, summaries, wiki pages and entities. Design the claims storage with this in mind.
- **Skip:**
  - The GraphRAG legacy path, which RAGFlow itself abandoned. Also, by strategy invariant, an LLM proposes and deterministic checks decide; KG synthetic chunks are uncitable.
  - The wiki compiler (too heavy for our scope).
  - Dataset navigation (unused, by their own measurement).

---

## 10. Agent canvas runtime and component model (`internal/agent/`)

### DSL
```json
{ "globals": {"sys.query":"", "sys.user_id":"", "sys.conversation_turns":0, "sys.files":[]},
  "components": {"Agent:Foo": {"upstream":[…], "downstream":[…], "obj":{"component_name":"Agent","params":{…}}}},
  "graph": {...React-Flow layout...}, "variables": {...}, "history": [...], "memory": [...] }
```

**Variable references.** The regex (identical to Python's) is:
```
\{+\s*([a-zA-Z:0-9_]+@[A-Za-z0-9_.-]+|sys\.[A-Za-z0-9_.]+|env\.[A-Za-z0-9_.]+|item|index)\s*\}+
```
- Forms: `{cpn_id@output.path}`, `{sys.query}`, `{env.x}`, `{item}`, `{index}`.
- Jinja is used (via gonja) when the text contains `{%` or a filter.
- `ResolveTemplate` fails loudly with "Can't find variable"; the display variant substitutes "".

**Per-node error handling.** `exception_method` is `goto` (sets `_next` to the error targets) or `comment` (emits a default value). Also `max_retries` and `delay_after_error`.

### Compilation (`canvas/scheduler.go: BuildWorkflow`)
The canvas compiles to an Eino `compose.Workflow`, which is a DAG; cycles are rejected.
- Each component becomes a lambda node. Its pre-handler injects a state snapshot; its post-handler flattens every output key into `Outputs[cpn][key]`.
- Multiple upstreams: the first is a data input, the others are ordering-only dependencies.
- Switch and Categorize nodes (and nodes with error gotos) route through a multi-branch on `out["_next"]`. An empty `_next` prunes the branch.
- Loop and Parallel are macro subgraphs:
  - **Loop:** do-while over `loop_termination_condition`, capped by `maximum_loop_count` (1024 as a safety guard).
  - **Parallel:** per-item copies of the state with `{item}`/`{index}`; `max_concurrency` 0 means sequential.
- **Human in the loop** (`UserFillUp`): `compose.Interrupt` returns an event `waiting_for_user {cpn_id, tips, inputs}`.
  - The next message is resumed with `ResumeWithData` against an Eino checkpoint in Kvrocks (`agent:cp:{run_id}`, TTL 24 h).
  - Run status lives in the hash `agent:run:{id}`: 0 running, 1 succeeded, 2 failed, 3 cancelled, 4 waiting.
- **Cancel:** a Redis flag polled every 500 ms.
- **Streaming:** SSE events `node_started`, `node_finished`, `message`, `message_end`, `waiting_for_user`, `error`, `cancelled`.
  - The "deferred stream" trick: an Agent feeding a Message node returns `{"content": DeferredStream}`, so the Message node relays the deltas, and the Agent's `node_finished` is held back until the stream closes.

### Component interface (`component/base.go`)
```go
type Component interface { Name() string; Invoke(ctx, db, inputs map[string]any) (map[string]any, error)
  Stream(ctx, db, inputs) (<-chan map[string]any, error); Inputs() map[string]string; Outputs() map[string]string }
type ParamBase interface { Update(map[string]any) error; Check() error; AsDict() map[string]any }
```

**Registry.** Registration is case-insensitive, with categories `agent`, `ingestion` and `shared`, and requires metadata (version, inputs, outputs).

**Components:** Begin, Message, LLM, Agent, Categorize, Switch, Loop, Parallel, Invoke (HTTP, SSRF-pinned), Retrieval, CodeExec, VariableAssigner and VariableAggregator, Data/List/String operations, DocsGenerator, ExcelProcessor, UserFillUp, plus about 45 tool-backed components.

### The Agent component (`component/agent.go`)
- **Defaults:** `max_rounds 5` and `message_history_window_size 13` (both templates use lower values, 3 and 12).
- **Step budget.** The ReAct budget is `maxSteps = 2*max_rounds + 1`, plus 2 when a code tool is attached. `roundLimitedChatModel` sets `ToolChoiceForbidden` when the budget runs out and injects:
  > "The configured tool-call budget is exhausted. Do not call any more tools. Produce your final answer using the evidence already collected. State any missing evidence or uncertainty; do not claim that unperformed searches were completed."
- **Sub-agents.** Nested Agent nodes become tools taking `user_prompt`, `reasoning` and `context` (all required), with depth ≤ 8.
- **MCP.** Tool schemas are snapshotted at configuration time; there is no live `tools/list` at run time. Permission is checked per server.
- **After the run:**
  - a tool-call memory summary: "output ONE short sentence (max 30 words) describing what the assistant did, suitable for a future-turn conversation history";
  - a multi-turn rephraser: "rewrite the latest input as a self-contained question that does not require the history to understand. Output ONLY the rephrased question".
- **Reference capture.** `referenceRecordingTool` wraps every tool. If the tool implements `ReferenceBuilder`, its chunks are recorded into `state.Retrieval` for citations, and invocations are logged (input capped at 8192 runes, result at 4096, at most 64 entries, secrets redacted).

### Port to our Python
Our engine is a fixed graph (`engine.py`), planned by an LLM, not a user-drawn canvas, and it should stay one: the strategy requires a statically validated plan DAG, not arbitrary user graphs. Worth taking:
1. **Round budget with forced finalization.** When `MAX_STEP_RUNS` is near, the reporter call (or a last executor call) runs with `tool_choice="none"` and the budget-exhausted message above, verbatim, translated. Our `_append_caveats` already speaks about missing evidence; this makes the model say it, not just the server.
2. **`exception_method` on plan steps.** A failing step has either a declared fallback step (`goto`) or a declared default observation (`comment`). Today any failure goes to replan. Declared fallbacks cost no LLM call; for example, `query_metric` fails → `explore_sql`, which commit `3dcc4359` already hard-codes.
3. **`ReferenceBuilder`-style capability on tools.** Each `Tool` may implement `references(result) -> list[Citation]`. The workspace then collects citations generically, instead of `tools/documents.py` writing into `ws.citations` by hand. This pairs with the evidence registry.
4. **The deferred-stream pattern** if the reporter streams blocks through a separate publisher.
5. **Tool-call memory sentence for history.** Our `HISTORY_TURNS=5` replays prior turns; a 30-word "what I did" line per turn is a cheap, high-value history compressor. Compare with `agentic/memory.py`, which extracts facts, not actions.
6. **Skip:** the Eino workflow, Redis checkpoints and UserFillUp. Our "ask → persist" node already covers the human-in-the-loop need, and a turn is short enough to restart rather than resume.

---

## 11. Harness, memory, MCP and tools

### `internal/harness/` (unused by the product)
A Go port of LangGraph and deepagents: Pregel BSP supersteps, channels with reducers, memory and NATS checkpointers, interrupts, and a ReAct middleware chain. It has no importers outside itself. The middleware algorithms are the useful part:
- **summarization.** Triggers at 160k estimated tokens (the compactor at 100k) and preserves the last 4 messages.
  - `findSafeSplit` never separates a tool call from its result.
  - An existing `<summary>` is merged with the newly compacted part.
  - The instruction: "Summarize… preserving key context, decisions, and action items… do not add information not in the conversation… same language".
- **patchtoolcalls.** An assistant tool call with no following result gets a placeholder `"[Tool call was not completed: name(call_id)]"` before the next model call.
- **reduction.** Tool output is cut to 2000 bytes plus `"\n...(truncated)"`. Note an apparent bug: it blanks results after the 20th from the start, which wipes the newest, not the oldest.
- **toolsearch.** Above 10 tools, the model sees a `tool_search` meta-tool and the first 5 tools.
  - Scoring: name-part exact match +10, name-part substring +5, whole-name exact match +10, name contains +3, description contains +2.
  - Top 5 results.

### Memory (`internal/service/memory.go`)
- **Types:** raw message, plus LLM-extracted semantic, episodic and procedural memories, each with `valid_at`/`invalid_at` in ISO 8601 and at most 5 items per type.
- **Extraction** is asynchronous, as an ingestion-queue task.
- **Recall** is a weighted sum (keyword 0.7), top 5, threshold 0.2. Forgetting is FIFO.

### MCP client (`internal/mcp/client`)
- Supports streamable-http (protocol 2025-03-26) and legacy SSE (the SSE endpoint must be same-origin).
- 10 s timeouts; `{{var}}` header substitution.
- The SSRF guard resolves every A/AAAA record and rejects private, loopback, link-local and metadata IPs, then **dials the pinned IP** to prevent DNS rebinding.

### Port to our Python
- **patchtoolcalls:** 15 lines in our Gateway message builder, if the executor ever uses native tool calls. Today it writes JSON arguments, so the risk is low.
- **Memory validity windows.** Add `valid_from`/`valid_to` to `agentic/memory.py` facts. This is the same invariant as our `available_at` point-in-time rule. Episodic and procedural memory are not needed.
- **SSRF with IP pinning** for any future HTTP or MCP tool; the crawler may already have its own guard, so check before adding one. Use the official `mcp` Python SDK rather than porting the client.
- **Skip the harness itself.** We have `agentic/graph.py`, and LangGraph exists if we ever need more.

---

## Ranked: the 8 most valuable things to port

1. **Evidence registry with stable first-seen handles, repeat-text pointers and compaction at publish** (section 2c, section 8). **Effort S.**
   - Files: `agentic/workspace.py`, `agentic/tools/documents.py`, the reporter's citation parsing in `engine.compose`, plus `RepairBadCitationFormats`.
   - Fixes duplicate numbering and duplicate passages in the observation budget today, and makes every published citation provably shown to the model.
2. **Parent/child (small-to-big) chunks** (sections 1 and 6). **Effort M.**
   - Index Khoản-level or about 128–256-token children in Qdrant and FTS; return and cite the Điều or section parent, scored by the mean (or max) of its children.
   - Files: `worker_document/processing.py` (emit children with `parent_id`), migration (`chunk.parent_id`, `searchable`), `worker_document/indexing.py` (index children only), `documents/retrieve.py` (promote to parent).
3. **A generic heading-hierarchy chunker for non-legal documents** (section 6, TitleChunker hierarchy and group). **Effort M.**
   - Uses our existing `LayoutBlock.level` and a Vietnamese regex-family vote for PDFs, plus a token cap with the heading prefix repeated, and atomic tables with neighbouring context.
   - Keeps the legal Điều/Khoản path as one family. Files: `worker_document/processing.py`, `extractors.py`.
4. **A better score in `retrieve.py`** (section 1). **Effort S.**
   - `final = 0.3·weighted_coverage + 0.7·normalized_rerank`; min-max normalization of TEI scores; weight 2 for numbers and article ids; one logged relaxed retry before declaring "not found".
   - Add `section_path` and title to the lexical document.
5. **Document-agent tools: `read_document` (neighbours in order) and `grep_documents` (regex)** next to `search_documents`, all through one shared eligibility query, plus a "keep top 3, rerank-prune the tail" filter on observations (section 8). **Effort M.**
6. **Chunk enrichment, doc2query** (section 7). **Effort M.**
   - LLM keywords and questions per chunk, stored, added to FTS, with the questions embedded as extra Qdrant points pointing to the chunk.
   - Includes the per-chunk LLM memo keyed by content hash, and content-addressed chunk ids (section 4) so reprocessing stays cheap.
7. **Agent-loop hygiene** (sections 3, 10 and 11). **Effort S.**
   - A forced final round with tools forbidden and the budget-exhausted message.
   - Declared step fallbacks (`exception_method` goto/comment).
   - The reviewer returns `missing_information[]`, turned into parallel keyword sub-queries.
   - A 30-word tool-call summary per turn for history.
8. **Header/footer and TOC removal before chunking** (section 5). **Effort S–M.**
   - A recurrence test on the top and bottom 10% boxes across at least half the pages (digits normalized), page-number patterns, and TOC dot-leader detection on front-matter pages.
   - File: `worker_document/processing.py`. It cleans both FTS and embeddings for every PDF we ingest.

**Next after these:** claims as first-class retrieval objects with an "answer from claims → deterministic floors → one verification call" fast path, which builds on `documents/claims.py`; watershed section summaries for long reports, which are L because of schema and evaluation work; and the schema-driven metadata extractor plus the filter-condition generator (`meta_filter.md`).

**Deliberately not ported:**
- Eino and the canvas DSL: we run an LLM-planned, statically validated DAG, not user-drawn graphs.
- GraphRAG/LightRAG retrieval: abandoned upstream, and its synthetic chunks cannot be cited.
- The wiki compiler and dataset navigation.
- The DeepDOC ONNX stack: use Docling or MinerU as a sidecar if layout quality becomes the bottleneck.
- Embedding-based post-hoc citation insertion: it contradicts our verified-quote invariant.
- RAGFlow's 120-iteration loop.
- The harness, a Go copy of LangGraph.
