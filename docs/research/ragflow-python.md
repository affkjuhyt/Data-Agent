# RAGFlow's Python era: structured data, DeepDoc, templates, metadata, RAPTOR/GraphRAG, TOC

> Quoted material © InfiniFlow and the ragflow authors, Apache-2.0, https://github.com/infiniflow/ragflow
> at tags v0.20.5 (P20) and v0.27.3 (P27, the last full-Python release) and commit 2c4bbbc (GO).

What `ragflow-go.md` could not cover: the Go checkout had already deleted `rag/`, `deepdoc/`,
`graphrag/` and `agent/`. This note reads them at P20 and P27 and judges each part against our
invariants: structured data never goes through chunk/embedding, answers carry verified quotes and a
trust label, every number traces to a query, Vietnamese with and without diacritics.

## Baseline it is judged against (04/11/2026)

`make eval-retrieval` on DS-AW, full stack (keyword + dense + rerank), 156 cases: 124 passed,
recall@1 0.75, recall@10 0.80, MRR 0.77, not-found accuracy 0.74, forbidden 0. Weakest tags:
no diacritics 1/10, cross-language 0/3, paraphrase 3/7, product guides 8/15, PDF 5/8, English 16/26,
no answer 5/10. Most misses return no passage at all: thresholds and the lexical half, not ranking.

## 1. Structured data

**Table chunk method** (P20 `rag/app/table.py`): one CSV/Excel row = one chunk `"col:val; col:val"`;
each column's type by a vote over its values (int, float, bool, datetime, text, `:263-299`); the
header, romanised, becomes a typed index field with a suffix (`_tks _long _kwd _flt _dt`) and the map
`{field: header}` is stored on the knowledge base (`:348-390`). P27 flattens multi-row merged headers
to `"Parent-Child"` (`_build_hierarchical_headers`) and gives columns roles (indexing, vectorize,
metadata).

**`use_sql` chat path** (P20 `api/db/services/dialog_service.py:364-372, 597-711`; P27 `:682-718,
1107-1525`): a knowledge base with a field map is asked by SQL first, vector search when it returns
nothing. The prompt opens "You are a Database Administrator…", temperature 0.06. The check is a regex
(strip to `select `, cut at `;`); the scope is appended as text, and P27's `add_kb_filter` is skipped
when the SQL already contains `"kb_id ="`. One repair round with the database error. P27 counts rows
without the model for "how many rows" questions (`:1143-1148`). The answer is the raw result as a
Markdown table citing files. Worth keeping: for an aggregate, P27 reruns its `WHERE` as
`select doc_id, docnm_kwd … limit 20` and cites those rows as the evidence behind the number
(`:1476-1525`; on GaussDB by AST surgery that refuses `HAVING`, `rag/utils/gaussdb_text_to_sql.py:146-160`).

**Agent Text2SQL**: `agent/tools/exesql.py` runs every `;`-split statement with no read-only check
(GO later added one); the `sql_assistant.json` template retrieves from three knowledge bases (DDL,
question→SQL samples, table descriptions) into one prompt, with no check or repair.

Verdict: skip row-as-chunk and `use_sql` (invariant, regex check, bypassable scope) and the Text2SQL
template (`sqlgen/` already links schema, uses examples and checks with sqlglot). Port aggregate
drill-through provenance. Adapt merged-header flattening: our Excel load
(`api/modules/ingest/routes.py`, `load_workbook(read_only=True)`) cannot see merged cells.

## 2. DeepDoc

Layout labels Text, Title, Figure, Figure caption, Table, Table caption, Header, Footer, Reference,
Equation (`deepdoc/vision/layout_recognizer.py:31-44`); header/footer/reference boxes dropped unless
confident and outside the top/bottom 10% (`:58-120`). Table structure recognition labels rows,
columns, column headers, projected row headers and spanning cells; OCR boxes get R/C/H/SP tags by
overlap ≥ 0.3 (`parser/pdf_parser.py:206-296`); a row is a header when most cells overlap a header
region, or in a numeric table when its cells are not numbers (`:326-339`). Two outputs of one grid:
HTML with colspan/rowspan, repeated header rows on continuation pages skipped (`:354-398`), and
linearised text, each cell `header：value`, multi-level headers joined, the caption suffixed
(`__desc_table`, `:400-499`), 10 rows per chunk. Cross-page merge: adjacent pages, nothing between,
gap ≤ mean height × 23 (`:719-736`). Paragraph joining by an XGBoost model. Runtime: onnxruntime,
xgboost, models from HuggingFace, OCR tuned for Chinese and English.

Verdict: adapt the header-bound linearisation for the tables we already parse (DOCX, HTML:
`worker_document/extractors.py`, today `"a | b"` rows with no header); port the cross-page merge and
repeated-header rules when PDF tables arrive; skip the ONNX/XGBoost stack.

## 3. Chunk templates (P20 `rag/app/*.py`)

naive (delimiter split, greedy merge to 512 tokens), book (TOC removed, hierarchical merge over
bullet regex families), laws (Chinese/English article families, none for Điều/Khoản: ours is better),
manual (PDF outline levels when they cover > 3% of sections, `manual.py:198-215`), paper, qa, table,
presentation (one slide, optional VLM), resume (a remote proprietary service), one, picture, email
(headers + body, attachments apart), audio, tag. Verdict: fold "outline levels when they cover
enough" into the heading chunker (RES-13); skip paper, resume, audio.

## 4. Metadata, tags, keywords and questions

Metadata filters: P20 filters in memory; P27 `common/metadata_utils.py:153-280` has auto (the model
writes the filter), semi-auto (an admin picks keys and operators) and manual, pushed down and
intersected so it only narrows. When auto matches nothing it returns None: an unscoped search, the
filter silently widens. The prompt `rag/prompts/meta_filter.md` outputs
`{"logic", "conditions": [{key, value, op}]}`, splits date ranges into ≥ start and < next start,
infers a missing year from today, matches value keys exactly and skips a condition with no match.
Extraction (`meta_data.md`) keeps a fixed key set, filters enums strictly, never infers.

Tag sets: a chunk is tagged by full-text searching its own text in a tag knowledge base and scoring
tags by lift over the prior (`rag/nlp/search.py:475-486`); queries are tagged the same way and ranked
with `cosine(tags)*10 + pagerank`. Auto keywords (0–32) and questions (0–10) are RES-15.

Verdict: port a semi-auto filter generator over our deterministic document fields (number, issuer,
type, effective dates), with value snapping and the date rules, and reverse the fallback: a filter
that matches nothing says "không tìm thấy" and shows the filter, never widens. An LLM-extracted field
is a proposal a person approves (invariant 3). Skip tag rank features.

## 5. RAPTOR, GraphRAG, TOC

RAPTOR (`rag/raptor.py`): UMAP to ≤ 12 dimensions, Gaussian mixture with k by BIC, soft assignment
> 0.1, the model summarises each cluster, recursively. GraphRAG (`graphrag/`): up to four calls per
chunk (extraction plus two gleanings and loop checks), one knowledge-base-wide graph merged under a
Redis lock with PageRank recomputed per document, search returns one synthetic chunk. Both were
deprecated in v0.27.0 for "Graph and Tree" knowledge compilation (GO `docs/release_notes.md:150`).

TOC (P27 `rag/svr/task_executor.py:690-740`, `rag/nlp/search.py:1034-1100`): the model labels
headings batch by batch and assigns levels; at query time it scores the top document's titles and
pulls the chunks under the relevant ones.

Verdict: skip GraphRAG and RAPTOR (synthetic, uncitable); adapt section expansion from a
deterministic TOC (our `section_path`, the PDF outline), every cited passage a real chunk.

## 6. Cross-language, whole-corpus questions, evaluation

Cross-language (P27 `generator.py:307-333`): the query is translated into each configured language
and searched as one query. No diacritic folding anywhere in RAGFlow. "Ask the whole knowledge base"
is retrieval plus a summary prompt; counting exists only through `use_sql`. A retrieval-test API
exposes every knob per request (`chunk_api.py:396-500`); `rag/benchmark.py` reports ndcg/map/mrr and
a per-query report sorted worst first.

Verdict: our lexical half is `to_tsvector('simple', …)` with NFC and lower case only
(`documents/retrieve.py:73,141-143`) and the database has no `unaccent`: an `unaccent`-folded
tsvector closes the 1/10 no-diacritics gap, which RAGFlow does not solve either. Adapt query
translation only where a Data Space declares an English corpus. Port the worst-first report into
`make eval-retrieval`.

## Ranked: what to bring over (not in `ragflow-go.md`'s list)

1. Diacritic-insensitive lexical index: `unaccent`, a folded tsvector, both queried (S).
2. Header-bound linearisation of document tables, caption suffixed, repeated headers dropped (S).
3. Aggregate drill-through: from a checked aggregate SQL, the contributing rows' query by sqlglot,
   `HAVING` refused, capped at 20, under the same guard (S).
4. Worst-first per-query report in `make eval-retrieval`; an admin retrieval-test screen (S).
5. Semi-auto metadata filters over deterministic document fields, never widening (M).
6. Merged multi-row Excel headers flattened to "Parent-Child" (S–M).
7. Section expansion from a deterministic TOC (M).
8. Deterministic shortcuts before SQL generation: count rows, distinct values of a column (S).

Not brought over: row-as-chunk tables and `use_sql`, ExeSQL, the three-knowledge-base Text2SQL
template, the DeepDoc ONNX/XGBoost stack, GraphRAG, RAPTOR, tag rank features, LLM-built TOCs.
