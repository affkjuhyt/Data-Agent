# CODING_AGENT_INSTRUCTIONS — Quy tắc cho agent triển khai (Part 9)

> Đọc theo thứ tự: `CLAUDE.md` gốc của repo (luật chung, bắt buộc) → tài liệu này → [IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md)
> (lấy task) → spec của task → [API_AND_SCHEMA_CONTRACTS.md](API_AND_SCHEMA_CONTRACTS.md) (tên, shape). Khi hai nơi
> mâu thuẫn: `CLAUDE.md` > tài liệu này > CONTRACTS > spec > backlog cũ `docs/v3/04-backlog.md`.

## 1. Những thành phần phải giữ nguyên (không được "cải tiến" nếu task không yêu cầu)

| Thành phần | Vì sao giữ |
| --- | --- |
| Kiến trúc khối ADR-28: `api` modular monolith + `worker_agent`/`worker_ingest`/`worker_document`/`crawler` theo schema | Đúng cho on-prem; mọi task đều nằm trong khối sẵn có |
| Outbox trong cùng giao dịch + relay `worker_agent/events.py` (`FOR UPDATE SKIP LOCKED`) | Bất biến; chỉ **thêm** attempts/dead-letter (PL-xx), không đổi mô hình |
| `scoped_session(role, org_id)` + RLS `org_isolation`; vai `svc_<schema>` | Cách ly tenant; mọi truy cập DB đi qua đây |
| OpenBao `secret_ref`; không literal credential ở đâu | §6 CLAUDE.md |
| Cổng A2 (`catalogue/a2_endpoints.json`, `check.py`): route không có trong A2 fail lúc import | ADR-48 |
| Semantic compiler `worker_agent/semantic/compiler.py` và test đối chiếu `test_adventureworks_reconciliation.py` | Đã đúng, đo được |
| `libs/sql_guard` chỉ SELECT; `query/executor.py` READ ONLY + statement_timeout + cap 10 000 dòng | Chỉ mở rộng (qualify theo catalog), không nới |
| Sandbox là **pod** `python-sandbox` (NetworkPolicy no-egress, RO fs); không mở lại hardening in-process | Quyết định bảo mật 02/11/2026 |
| Đường governed (`questions.py`) | ADR-50: là engine mặc định; **không xoá** dù ghi chú cũ nói ngược lại |
| Eval kỷ luật: case JSONL có id, `run.py` luôn exit 0, `gate.py` quyết; ngưỡng online chỉ chốt từ lượt chạy thật | ADR-57 |
| Generator CSDL `backend/schema/csdl` (tab 3/3b → `definitions.json` → DDL/ORM) | Không sửa tay file sinh |

## 2. Những thành phần cần thay thế hoặc đổi hành vi (theo task)

| Hiện tại | Thành | Task |
| --- | --- | --- |
| `da_materialize_upload(..., true)` → `DROP TABLE`; upload `dataset_version=1` cố định | bảng `workspace.<name>__v<n>` + view + snapshot Parquet (`libs/snapshot`) | DF-* (F-01) |
| `quality_state` chỉ thành caveat ở `agentic/signals.py` | `worker_agent/quality_gate.py` chặn governed/report/product | DF-* (F-02) |
| Nhãn tin cậy chuỗi tiếng Việt ở `workspace.TRUST_LABEL`, `questions.py:2175`, `sqlgen/answer.py` | `qa.answer.trust_code` enum + `answer/envelope.py`; FE đọc `trust_code` | AE-* (F-03) |
| `_seal_receipt` chỉ ở governed | `receipts.py` dùng chung | AE-* |
| `ask_user` trả `Clarify` thẳng (`agentic/tools/__init__.py:29`, `engine.py:495`) | qua `clarify_policy.apply` | AE-* |
| `present_gate.no_figure` coi toàn 0 là không có dữ liệu | phân biệt NULL/0 bằng coverage | AE-* (F-06) |
| audit SQL chỉ LLM, chỉ ở agentic | `sqlgen/audit_rules.py` tất định, cả hai đường | AE-* (F-05) |
| pdfminer layout; bảng dẹt `"a | b"` | `worker_document/docling_adapter.py` → `document.element/table/table_cell` | DI-* (F-07) |
| FTS `'simple'` | `chunk.text_folded` + `unaccent` | DI-* (F-08) |
| `retrieval_eval.py` `halves` = cấu hình | `halves` = kết quả thực chạy | DI-/EV-* |
| JSON 10k dòng vào sandbox; pandas | Arrow/Parquet + DuckDB (`libs/code_sandbox/duck.py`); gVisor | AL-* (F-09, F-12) |
| `turn_trace._write` cuối lượt | upsert theo bước | AE-* (F-30) |
| Outbox không attempts | `attempts/last_error/dead_at` + savepoint mỗi sự kiện | PL-* (F-11) |
| `sqlglot==25.24.5` | `30.x` (PoC-5 trước) | PL-* |
| `CONNECTORS` card cứng (`catalog/routes.py:97`) | sinh từ `libs/source_drivers/registry.py` | DF-* |
| `AGENT_PYTHON_SANDBOX=local` | xoá nhánh | AL-* |
| Route `exec.publication`, `knowledge_run`, `event_bulletin` 202 rồi im | 501 `UNSUPPORTED` | PL-* |

Danh sách file:dòng đầy đủ theo domain ở `evidence/audit-*.md` §"Top 10 gaps".

## 3. Quy ước mã (bổ sung `CLAUDE.md` §3–§4)

- Mọi tên bảng/cột/sự kiện/module/mã lỗi mới lấy từ [ARCHITECTURE.md](ARCHITECTURE.md) §9 và CONTRACTS. Cần tên mới →
  sửa §9 **trong cùng PR**, kèm lý do; không đặt tên "tạm".
- Thêm bảng: sửa nguồn `backend/schema/csdl/source/tab3*.md` → `python3 -m csdl.parse_d4 && python3 -m csdl.gen_ddl &&
  python3 -m csdl.gen_orm` (từ `backend/schema`) → migration `backend/alembic/versions/v3_00NN_*.py` áp SQL sinh ra;
  routine viết tay (view, trigger) vào `backend/schema/csdl/routines/<schema>.sql` với `-- covers:`; không viết hàm chỉ
  trong migration (lỗi `da_materialize_upsert` v3_0035 phải được đưa về generator ở DF-*).
- Thêm endpoint: sửa tab 4 A2 (`catalogue/source`) và `a2_endpoints.json` trong cùng PR; route dùng
  `endpoint("METHOD", "/v1/...")`; `make api-types` để sinh `schema.ts`; không sửa tay `openapi.json`/`schema.ts`.
- Sự kiện: ghi vào `<schema>.outbox` trong giao dịch đổi dữ liệu; handler đăng ký ở `HANDLERS` của module; handler
  idempotent theo trạng thái.
- Chuỗi hiển thị: không hard-code tiếng Việt trong worker/api mới; dùng `message_key` (CONTRACTS §1) và catalog vi/en.
- Số: LLM không bao giờ sinh số vào văn bản; mọi số là `Figure` có nguồn (`query_run | compute_run | table_cell |
  verified_query`).
- Test gắn mã tiêu chí: `@pytest.mark.req("AC-..")` khi task nêu AC; khai `status.tsv` + `make design-checklist` trong
  cùng commit khi đổi trạng thái UC/màn/ADR.
- Comment giải thích **vì sao**, ngôn ngữ theo file xung quanh. Không `# TODO` không chủ.

## 4. Tương thích API và migration

- `GET /v1/answers/{id}` trả thêm `envelope`; các trường cũ (`text`, `figures[].trust` chuỗi) giữ **một release** rồi
  bỏ; FE đọc `trust_code` trước, fallback chuỗi. Ghi deprecation trong CONTRACTS §8.
- Migration thứ tự: (1) cột/bảng mới nullable → (2) code ghi cả cũ lẫn mới → (3) backfill (`trust_code` suy từ
  `generated_by`/chuỗi; `dataset_snapshot` cho version hiện hành) → (4) code đọc mới → (5) bỏ cũ ở release sau.
  Mỗi migration có `downgrade` thật (không `pass`) trừ khi xoá dữ liệu — thì ghi rõ trong docstring và trong PR.
- Versioned workspace: tạo view tên cũ **trước** khi đổi writer; mọi reader (pipeline CTE, tiles, receipts, sandbox
  input) chạy test với view trước khi merge.
- Không đổi mã lỗi đã có; thêm `AUDIT_REJECTED`, `SOURCE_CONFLICT` ở `libs/contracts/v1/errors.py` kèm test.

## 5. Lệnh kiểm thử (chỉ trong container; host Python 3.10 ≠ target 3.11)

```bash
make dev-tools                       # một lần: image ruff/mypy/import-linter
make lint                            # ratchet: chỉ findings MỚI so với LINT_BASE
make type-v3 && make lint-imports    # strict surfaces
make test-related                    # test của file đổi và test import chúng (dùng khi lặp)
make test-unit UNIT_SUITES=worker_agent:worker-agent PYTEST_ARGS="tests/unit/worker_agent/test_x.py"
make test-integration INTEGRATION_SUITES="worker_agent:worker-agent" PYTEST_ARGS="tests/integration/worker_agent/test_y.py"
make frontend-lint && make frontend-test
make design-checklist                # khi đổi status.tsv / test có mã AC
python -m model.eval.run --tier offline --out eval-report.json && python -m model.eval.gate --report eval-report.json --tier offline
make eval-retrieval RETRIEVAL_ORG=<org> RETRIEVAL_SPACE=DS-AW          # cần cụm dev; ghi rõ halves thật
make agent-eval AGENT_EVAL_ORG=<org> AGENT_EVAL_AS=<user> AGENT_EVAL_ARGS="--engines agentic,governed --limit 20"
```

Quy tắc: chạy test **có liên quan** khi lặp (không chạy cả suite mỗi lần); trước khi báo xong chạy suite của
service đã đụng; eval thật chỉ chạy trên cụm dev local (`make deploy-local` từ worktree sạch) và **ghi kết quả vào
`docs/v3/eval/`** kèm SHA + ngày. Test đỏ là regression: sửa hoặc giải thích, không `skip`, không sửa expected để
qua, không xoá test (xem §9).

## 6. Cài phụ thuộc

- Backend: mỗi service có `requirements.txt` pin chặt; thêm lib → pin version chính xác, thêm vào image (`backend/
  services/<svc>/Dockerfile`), rebuild `docker build -t localhost:5001/dataagent/<svc>:latest -f
  backend/services/<svc>/Dockerfile .` từ worktree sạch; `tests/dev-tools.Dockerfile` nếu type-check cần.
- Thư viện mới được phép (giấy phép đã kiểm, [OPEN_SOURCE_RESEARCH.md](OPEN_SOURCE_RESEARCH.md)): `docling` (MIT),
  `duckdb` 2.0.x (MIT), `sqlglot` 30.x (MIT), `dbt-semantic-interfaces` (Apache-2.0), `pandera` (MIT),
  `prometheus_client`, `opentelemetry-sdk` (Apache-2.0). **Cấm**: AGPL, ELv2 (PyAirbyte, soda-core), Collate
  (OpenMetadata ingestion), CC BY-NC, Surya weights, bất kỳ `*-ee/` path. Mọi lib mới vào `THIRD-PARTY-NOTICES.md`
  (RES-34).
- Docling model weights: tải một lần bằng `docling-tools models download` vào PVC, đặt `DOCLING_ARTIFACTS_PATH`; không
  tải lúc chạy; image worker-document giới hạn 4 GiB.
- Frontend: `npm ci` trong `frontend/`; không thêm ESLint (CLAUDE.md §1).

## 7. Secrets và môi trường

- Không commit `.env`, `secrets.env`, `*.key`, `.openbao-keys.json`. Credential nguồn = `secret_ref` KV v2 tại
  `secret/tenants/{org}/sources/{source}/...`; worker đọc bằng AppRole của chính nó.
- Biến môi trường mới: `SCREAMING_SNAKE` có tiền tố (`INGEST_`, `AGENT_`, `DOC_`, `SANDBOX_`, `EVAL_`); khai ở manifest
  dev **và** production, có giá trị mặc định an toàn (fail-closed). Ví dụ: `AGENT_BUDGET_TOKENS`, `AGENT_BUDGET_SECONDS`,
  `DOC_EXTRACTOR=docling|legacy`, `SANDBOX_RUNTIME_CLASS=gvisor`.
- E2E/eval cần `E2E_USER`/`E2E_PASS` từ vault Ansible; không ghi vào tài liệu hay log.
- Cụm dev local: kiểm `~/.dataagent-deploy/k8s-local/last_deployed_sha.txt` trước khi đo; deploy từ worktree sạch.

## 8. Definition of Done (một task)

1. Mã + test (unit; integration khi đụng DB/outbox; contract khi đổi shape) xanh trong image của service.
2. Không findings ruff/mypy **mới** (so baseline worktree); strict surfaces xanh; `frontend-lint` xanh nếu đụng FE.
3. Tiêu chí chấp nhận của task đo được và **đã chạy**: ghi lệnh + kết quả trong PR/commit message.
4. Docs cập nhật cùng commit: spec (nếu hành vi khác spec), `docs/v3/04-backlog.md` cột TT hoặc
   `IMPLEMENTATION_BACKLOG.md` cột TT, RUNBOOK nếu vận hành đổi, `status.tsv` + checklist nếu đổi trạng thái UC.
5. Không `# TODO` không chủ; mã chết xoá, không comment-out; migration có downgrade.
6. Nếu task cần đo trên cụm (eval, bake-off): kết quả vào `docs/v3/eval/` hoặc `model/eval/` kèm SHA, ngày, cấu hình
   (model, TEI có/không).

## 9. Quy tắc bắt buộc

- **Không tự đổi kiến trúc đã duyệt**: không thêm schema/service/connector/agent kind; không đổi mô hình outbox, RLS,
  sandbox pod, governed-first. Thấy cần đổi → dừng, viết đề xuất ADR ngắn (bối cảnh, lựa chọn, hệ quả) vào
  `docs/blueprint/adr-proposals/`, báo blocker; không làm trước.
- **Không bỏ qua hay sửa test để hợp thức hoá**: không `xfail/skip` mới, không nới tolerance, không đổi đáp án vàng
  (đáp án vàng chỉ đổi qua quy trình giải mù ở DATASET_AND_EVALUATION §5, tạo phiên bản case mới).
- **Không hạ ngưỡng gate**; không bỏ `provisional` nếu chưa có lượt chạy thật ghi trong repo.
- **Không chạm raw**: không viết code sửa dữ liệu thô hay snapshot đã niêm phong; sửa = correction có version.
- **Shared checkout**: thư mục chính có thể đang được phiên khác sửa. Làm trong `git worktree add` riêng; không
  `stash`, `reset`, `pull --rebase --autostash` ở checkout chính; stage theo đường dẫn, không `git add -A`; kiểm
  `git diff --cached --stat` không có `node_modules`; fast-forward `main` sau khi file đụng tới sạch ở checkout chính.
- **Một task một PR/commit**, tiêu đề mang ID (`DF-03: ...`); không gộp "tiện tay" sửa chỗ khác; `make fmt` toàn
  tree không đi cùng thay đổi hành vi.
- **Báo cáo**: cuối mỗi task ghi ba mục bắt buộc — *Blocker* (cái gì chặn, cần ai), *Regression* (test nào đỏ sau
  thay đổi, kể cả không do mình, với bằng chứng baseline), *Nợ kỹ thuật* (cái gì làm tắt, ở đâu, ảnh hưởng gì, task
  nào trả). Không có thì ghi "không".
- **Không tuyên bố production-ready**; chỉ tuyên bố "đạt tiêu chí X với lượt chạy Y".
- Nếu chưa truy cập được một file nêu trong spec (đã đổi tên/xoá), **đối chiếu lại bằng `graphify query`** trước khi
  tạo file mới; ghi khác biệt vào PR.

## 10. Thứ tự bắt đầu cho agent mới

1. `cat CLAUDE.md`; `graphify query "<module của task>"`; đọc spec của task và CONTRACTS mục liên quan.
2. Tạo worktree; `make dev-tools`; chạy `make test-related` trên trạng thái sạch để có baseline.
3. Viết test đỏ cho tiêu chí chấp nhận → mã → xanh → lint/type → docs → commit theo §8–§9.
