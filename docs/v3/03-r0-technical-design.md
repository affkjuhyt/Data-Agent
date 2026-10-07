# 03. Thiết kế kỹ thuật R0: nền mã

> Ngày 06/10/2026. Hiện thực mục 4.3 của [00](00-product-strategy.md) và các bổ sung của [01](01-agent-core-and-ui.md)
> §7 trên mã ở HEAD `33e963d`. Mỗi mục: vì sao (bằng chứng trong mã), hợp đồng dữ liệu, chỗ sửa, test,
> xong khi. Quy tắc repo (test trong container, so baseline lint, outbox, naming) theo `CLAUDE.md`.
>
> R0 là điều kiện để mọi đo đạc sau đó có nghĩa. Không thêm tính năng mới trong R0; chỉ làm đúng cái UI
> và tài liệu đang hứa.

## 0. Phạm vi và ranh giới

| Trong R0 | Ngoài R0 (R1+) |
| --- | --- |
| Kênh result set; MCP chuẩn; checkpointer bền; canvas một nguồn sự thật; `/run` không tin client; sandbox có xác thực; secret qua OpenBao; lỗi có cấu trúc; frame `__verification`; Plan IR + validator; lớp lỗi trong judge; đo model (D1) | Capability Registry bảng; chuyên gia SQL (N-version, value retrieval); Workspace UI; Catalog; semantic join; Verified Query; Monitor |

Nguyên tắc chung cho mọi mục:

- Hợp đồng mới đặt ở `backend/libs/contracts/v1/` (Pydantic, có fixture, đi qua cổng tương thích
  NOP-152). `workflow_runtime` **bắt đầu import** `libs.contracts` từ R0 (hôm nay chỉ `indicator_api` dùng).
- Không giữ đường cũ "để phòng": đường cũ sai thì xoá (CLAUDE.md §2), có test chứng minh đường mới.
- Mỗi R0-x một PR, một commit chính, test unit trong `tests/unit/workflow_runtime/` (hoặc service tương
  ứng), so baseline ruff/mypy, cập nhật `04-backlog.md` cột TT trong cùng commit.

## 1. R0-1 Kênh result set

**Vì sao.** `rrf_ranking.py:16` gộp `db_results` với chunk văn bản; `reranker.py` xếp lại bằng
cross-encoder, `reranker_top_n=5`; `responder.py:257` chỉ đọc `reranked_chunks`; `judge.py:47` cũng chỉ
xem `reranked_chunks`. Kết quả SQL là JSON trong `content` (`handlers/data.py:598`), được chấm điểm
"liên quan về chữ" rồi cắt. Canvas không có rrf/reranker thì responder không thấy kết quả SQL.

**Hợp đồng.** `libs/contracts/v1/result_set.py`:

```python
class ResultColumn(BaseModel):
    name: str
    type: Literal["text", "number", "date", "datetime", "bool", "json"]
    unit: str | None = None

class ResultProvenance(BaseModel):
    connection_id: str
    engine: Literal["postgres", "clickhouse", "snowflake"]
    sql: str                     # đã redaction theo libs/shared/audit
    sql_hash: str                # sha256
    tables: list[str]
    executed_at: datetime
    watermark: datetime | None   # freshness.describe đã có
    truncation: dict | None      # {"limit": 10000, "truncated": true}
    generated_by: Literal["semantic_layer", "verified_query", "llm"]

class ResultSet(BaseModel):
    result_id: str               # đã có: answer_exports `_RESULT_ID_RE`
    kind: Literal["table", "scalar", "series"]
    columns: list[ResultColumn]
    row_count: int
    rows_preview: list[dict]     # ≤ 50 dòng
    stats: dict[str, dict]       # tất định: min/max/sum/nulls theo cột số, distinct theo cột text ≤ 20
    provenance: ResultProvenance
    trust: Literal["VERIFIED", "GOVERNED", "EXPLORATORY"]
    warnings: list[str]          # aggregation_check, truncation, unit conflict (đã có trong responder)
```

Dòng đầy đủ: MinIO `tenant/{tenant}/ask-results/{result_id}` (tái dùng `answer_exports.create_answer_export`,
chuyển từ "chạy kèm export xlsx" thành "lưu luôn khi có kết quả"). Tối đa 10.000 dòng (đã có
`result_limits.py` ở sandbox).

**Chỗ sửa.**

| File | Thay đổi |
| --- | --- |
| `domain/state.py` | Thêm `result_sets: List[ResultSet-dict]`; `db_results` xoá sau khi mọi node đọc `result_sets` |
| `tool_executor/handlers/data.py` `_text_to_sql_handler`, `_pandas` | Trả `ResultSet` (tool_results vẫn nhận phần văn bản thuần của tool khác) |
| `nodes/sandbox/db_connector.py` | Ghi `result_sets` |
| `nodes/model_harness/rrf_ranking.py` | Chỉ gộp `kb_chunks + mcp_results + tool_results` dạng văn bản; result set không bao giờ vào |
| `nodes/model_harness/responder.py` | Ngữ cảnh = `result_sets` (schema + stats + preview, giới hạn ký tự riêng `context_max_result_rows`) **cộng** `reranked_chunks`; `evidence.runs` sinh từ `result_sets` (thay vì từ metadata chunk) |
| `nodes/model_harness/judge.py` | `EvidenceItem` từ `result_sets` (mọi số trong preview + stats) và từ chunk; `numbers_grounded` kiểm cả hai |
| `nodes/model_harness/visualization.py` | Đọc `result_sets` |
| `runtime_services/conversation_runner.py` | Frame `__result_set` (một frame/result set, không kèm rows ngoài preview) trước `__evidence` |
| `frontend/src/features/ask/` | `DataGrid` và `EvidencePanel` đọc `__result_set`; `__evidence.runs` giữ tương thích một đợt rồi bỏ |

**Test.** `test_result_set_channel.py`: (1) canvas planner→db_connector→responder (không rrf/reranker):
responder nhận bảng; (2) canvas có rrf+reranker với 6 chunk + 1 result set: result set không bị cắt;
(3) judge: số trong câu trả lời có trong `stats.sum` → PASS, số lạ → GROUNDING_ERROR; (4) frame
`__result_set` không chứa quá 50 dòng. Sửa `test_evidence_frame.py` cho nguồn mới; `test_db_connector_node.py`.

**Xong khi.** Câu hỏi SQL với canvas mặc định của Playground ra đúng bảng; `make test-unit
UNIT_SUITES=workflow_runtime:workflow_runtime` xanh; `db_results` không còn trong mã.

## 2. R0-2 MCP chuẩn

**Vì sao.** `nodes/sandbox/mcp.py:34` so `endpoint_name`, planner trả `tool_name`
(`prompts/planner_system.txt:22`); planner không được cho biết server/tool nào tồn tại; giao thức là
`POST {url}/execute` tự chế. Không lời gọi nào khớp.

**Thiết kế.**

- Dependency: `mcp>=1.0` (SDK chính thức) trong `workflow_runtime/requirements.txt`.
- `infrastructure/mcp_client.py`: `MCPRegistry` theo tenant: `list_tools(endpoint)` qua Streamable HTTP,
  cache 60 s (Redis, key `mcp:tools:{endpoint_id}`), `call_tool(endpoint, tool, arguments, timeout)`.
  Auth: `Authorization: Bearer` từ `api_key_ref` của bảng `MCP` qua OpenBao (`warehouse_secrets.py`
  đã có pattern `_resolve_warehouse_secret`); token không bao giờ nằm trong config agent hay Postgres
  (đã chốt ở commit 371b9c4).
- `config_loader.load_agent_config`: `mcp_endpoints` → `mcp_tools: [{server_id, server_name, tool,
  description, input_schema}]` đã lọc `allowed_tools`.
- Planner: khối "MCP TOOLS" trong prompt liệt kê `server/tool` + `input_schema` rút gọn; schema
  `tool_calls[]` cho `type="mcp"`: `{server_id, tool, arguments}`; validator Pydantic từ chối tool không
  có trong danh sách (lỗi có cấu trúc, không rơi về kb_search — xem R0-7).
- `mcp.py`: gọi `call_tool`; content blocks `text` → `MCPResult.content`, `resource`/`image` → bỏ qua
  có log; lỗi → `mcp_errors[{code: TOOL_UNAVAILABLE, server, tool, detail}]`.
- Bảng `MCP`: cột `protocol` mặc định `mcp`; bản ghi cũ `legacy_http` bị **từ chối khi publish agent**
  (409 `MCP_PROTOCOL_UNSUPPORTED`) với thông báo cấu hình lại; không giữ đường POST cũ trong runtime.

**Test.** `test_mcp_client.py` dùng `mcp.server.fastmcp.FastMCP` chạy in-process (ASGI qua httpx
`ASGITransport`): list_tools → planner prompt chứa tool; planner trả `{type: mcp, server_id, tool}` →
kết quả về `mcp_results`; tool không whitelist → lỗi có mã; token không xuất hiện trong state/log.

**Xong khi.** Một MCP server mẫu (ví dụ `filesystem` hoặc server test của repo) gọi được từ Playground;
không còn chuỗi `"/execute"` trong `nodes/sandbox/mcp.py`.

## 3. R0-3 Checkpointer bền cho agent đơn

**Vì sao.** `conversation_runner.py:197` `MemorySaver()`, `:200` `_pending_reviews` dict; restart pod
hoặc 2 replica mất phiên chờ duyệt. Orchestrator đã dùng `AsyncPostgresSaver` (`orchestrator/graph.py:106`).

**Thiết kế.**

- Chuyển `_open_checkpointer`, `_checkpointer_dsn`, serde sang `infrastructure/checkpointer.py`; cả hai
  graph dùng chung pool.
- `thread_id = f"{tenant_id}:{conversation_id}"`; mọi resume đi qua `conversation_owned_by` (đã có).
  Bảng checkpoint của LangGraph do thư viện quản, không RLS được: ghi rõ trong RUNBOOK, role
  `app_user` là chủ sở hữu schema `langgraph`, không service nào khác đọc.
- `_pending_reviews` → bảng `runtime.pending_review(conversation_id PK, tenant_id, agent_id, query,
  payload JSONB, suspended_at, version)` với RLS qua `libs/shared/tenant_rls.py`; migration Alembic;
  `GET /api/reviews/pending` và `/api/reviews/{conv_id}/resume` đọc bảng, xoá dòng khi resume xong
  (trong cùng transaction với ghi message).

**Test.** Integration `test_human_approval_resume.py` (suite workflow_runtime, Postgres thật): chạy tới
`human_approval`, tạo runner mới (mô phỏng pod khác), resume → graph tiếp tục; `pending_review` của
tenant khác không thấy (RLS). Unit: `test_reviews_tenant.py` cập nhật.

**Xong khi.** `MemorySaver` không còn trong runtime; `kubectl rollout restart` giữa lúc chờ duyệt không
mất phiên (ghi vào RUNBOOK §HITL).

## 4. R0-4 Canvas: một nguồn sự thật, lưu an toàn

**Vì sao.** Frontend có `trigger, output, guardrail, condition, db_query, send_notification, loop`;
`graph.py:34 NODE_TYPE_MAP` không có → bị bỏ âm thầm (`graph.py:288` "chỉ lấy node có trong
NODE_TYPE_MAP"). `canvas.py:api_save_canvas` không validate, `save_canvas` xoá-rồi-chèn, không chặn
version đã publish; cache graph theo version id.

**Thiết kế.**

- `application/node_registry.py` là nguồn duy nhất: mỗi mục có `executable: true` và phải có handler
  trong `NODE_TYPE_MAP` (test kiểm hai tập bằng nhau). `GET /api/nodes/registry` trả thêm `style`
  (màu, icon) để frontend không giữ `NODE_STYLE` riêng. 7 loại không chạy được: **xoá khỏi frontend**
  trong R0 (condition/loop/notification quay lại R2–R3 khi có handler thật).
- `domain/authoring_schemas.CanvasPayload` validate: `nodeType ∈ registry`, config theo schema của
  registry, edges trỏ node tồn tại, có đúng một `planner`, không chu trình ngoài các cạnh judge→planner
  /responder cho phép (dùng cùng hàm dựng graph `_build_dynamic_graph` ở chế độ "dry-run"). Lỗi 422 có
  đường dẫn trường.
- Lưu: một document Mongo cho cả canvas `{workflow_version_id, nodes, edges, canvas_hash, saved_at}`,
  `replace_one(upsert)`; bỏ hai collection `flow_nodes`/`flow_edges` (migration một lần, script trong
  `jobs/`).
- Version đã publish: 409 `VERSION_PUBLISHED`; muốn sửa thì tạo draft mới (API đã có).
- Cache graph: key `(workflow_version_id, canvas_hash)`.

**Test.** `test_canvas_validation.py`: node lạ → 422; chu trình → 422; publish rồi save → 409; lưu hai
lần → một document; `test_graph_cache.py` cập nhật key. Frontend: `Builder.test.tsx` lấy node từ API.

**Xong khi.** Mọi node kéo được trên canvas đều chạy; `knip` không báo `NODE_STYLE` thừa.

## 5. R0-5 `/api/conversations/run` không tin client

**Vì sao.** `domain/schemas.RunRequest` nhận `orchestrated`, `memory_context`, `guardrail_cfg` từ bất kỳ
client nào (`conversation_runner.py:377`).

**Thiết kế.** Tách `RunRequest` (công khai: `query, agent_id, conversation_id, messages,
clarification_answers`) và `InternalRunRequest` (thêm ba trường) chỉ chấp nhận khi caller là service
`orchestrator` theo `libs/shared/service_identity.verify_service_assertion` (đã có; `resolve_caller`
trong `libs/shared/auth.py` đã phân biệt service). Route công khai không có đường nào set ba trường.

**Test.** `test_run_request_hardening.py`: client gửi `memory_context` → bị bỏ (422 hoặc ignore có log,
chọn 422 cho rõ); orchestrator với assertion hợp lệ → nhận.

## 6. R0-6 Sandbox có xác thực, secret qua OpenBao

**Vì sao.** `sandbox_executor/main.py` không có `Depends` xác thực trên `/execute/*`; mật khẩu DB đi trong
body; `schema_crawler/metadata.py:115 _password` đọc env `WAREHOUSE_PASSWORD__*`.

**Thiết kế.**

- Sandbox: dependency `require_service(("workflow-runtime",))` dùng `verify_service_assertion`; env
  `SANDBOX_ALLOWED_SERVICES`. Health không cần.
- `ExecuteSQLRequest`: thay `password` bằng `connection_ref` (id của `KBWarehouseConfig`/connection);
  sandbox đọc secret từ OpenBao bằng token của nó (role riêng, chỉ đọc path `warehouse/*`). Ansible
  thêm policy OpenBao cho sandbox (deploy/ansible, không script).
- Schema crawler: `_password` → `warehouse_secrets.resolve(engine, warehouse_id)` cùng pattern; xoá
  các biến `*_CRAWLER_PASSWORD` khỏi manifest và `secrets.env.example`.

**Test.** Unit sandbox: không assertion → 401; integration `test_sandbox_handler_wiring.py` cập nhật
với `connection_ref`. Grep CI: không còn `WAREHOUSE_PASSWORD__` trong `deploy/`.

## 7. R0-7 Lỗi có cấu trúc, không rơi về agent khác

**Vì sao.** `config_loader.py:80` rơi về `DEV_CONFIG` khi DB lỗi; `planner.py:244` rơi về `kb_search`
khi không parse được.

**Thiết kế.**

- `libs/contracts/v1/errors.py` (đã có) mở rộng thành enum mã lỗi hợp nhất (01 §3.5): thêm
  `AGENT_CONFIG_ERROR`, `PLANNER_ERROR`, `CONTRACT_MISMATCH`, `NO_JOIN_PATH`, `INSUFFICIENT_EVIDENCE`,
  `TOOL_UNAVAILABLE`. Mỗi mã có `user_message_vi`, `next_action` (hỏi lại, liên hệ steward, thử lại).
- `load_agent_config`: lỗi DB → raise `AgentConfigError`; `DEV_CONFIG` chỉ khi `RUNTIME_DEV_FALLBACK=1`
  (không đặt trong manifest dev/production).
- Planner: parse thất bại → thử lại một lần `temperature=0`; vẫn thất bại → `PLANNER_ERROR`; không có
  `_FALLBACK_TOOL_CALLS`.
- Stream: frame `__refusal {code, message, next_action, detail_id}`; `[ERROR]` chỉ còn cho lỗi hạ tầng.
- Frontend: `RefusalCard` một thẻ/mã (09 V2 đã đặc tả).

**Test.** `test_structured_errors.py`: DB lỗi → `__refusal AGENT_CONFIG_ERROR`, không có câu trả lời
của DEV_CONFIG; planner trả rác hai lần → `PLANNER_ERROR`; snapshot mã lỗi ↔ thông điệp.

## 8. R0-8 Frame `__verification`

**Vì sao.** `judge.py:84` tính `verifier_report` vào state nhưng `conversation_runner.py` không phát.

**Thiết kế.** Sau `__evidence`: `__verification {checks: [{name, passed, detail}], verdict, iteration,
confidence?}` từ `state["verifier"]` + judge verdict. Confidence để trống trong R0 (đến từ R1 N-version).
Frontend `VerifierBadge` cạnh câu trả lời; mở ra danh sách kiểm.

**Test.** Mở rộng `test_evidence_frame.py`: frame phát đúng một lần, chỉ khi có câu trả lời.

## 9. R0-10 Plan IR và validator tĩnh

**Vì sao.** Orchestrator planner DPTS xuất `Target/Task{title, input, agent_id, depend_on}`
(`planner/dpts/plan.py`); agent đơn xuất `tool_calls[]`. Không có kiểu cổng, không có output contract,
không kiểm tĩnh; executor chạy rồi mới biết lỗi.

**Hợp đồng.** `libs/contracts/v1/plan.py`:

```python
class Port(BaseModel):
    key: str
    type: Literal["result_set", "evidence_list", "text", "chart_spec", "report", "scalar"]
    schema_: dict | None = Field(None, alias="schema")

class OutputContract(BaseModel):
    shape: Literal["table", "scalar", "series", "report", "text"]
    columns: list[ResultColumn] = []
    order_by: list[str] = []
    precision: dict[str, int] = {}
    units: dict[str, str] = {}

class PlanNode(BaseModel):
    id: str
    kind: Literal["SQL_QUERY", "RETRIEVE_DOC", "EXTRACT_EVIDENCE", "COMPUTE", "VALIDATE",
                  "SYNTHESIZE_REPORT", "CLARIFY", "HUMAN_APPROVAL", "AGENT"]
    capability: str              # R0: tên tool/agent hiện có; R1: id trong Capability Registry
    inputs: dict[str, Any]
    outputs: list[Port]
    depends_on: list[str] = []
    evidence_requirements: list[dict] = []
    trust: Literal["VERIFIED", "GOVERNED", "EXPLORATORY", "DOCUMENT"] | None = None

class PlanEdge(BaseModel):
    from_: str = Field(alias="from"); to: str
    kind: Literal["HARD_DEP", "SOFT_DEP", "ALT_GROUP"] = "HARD_DEP"

class PlanIR(BaseModel):
    plan_id: str
    output_contract: OutputContract
    nodes: list[PlanNode]
    edges: list[PlanEdge]
    budget: dict[str, int] = {}  # max_nodes, max_parallel, max_replans, max_llm_calls
```

**Validator** `application/plan/validate.py` (thuần, không I/O): không chu trình; mọi `depends_on`/edge
trỏ node tồn tại; cổng vào của node sau khớp kiểu cổng ra node trước; `capability` nằm trong tập cho
phép của agent (`allowed_tools` + agent catalog + mcp_tools); node `SQL_QUERY` có `inputs` là
AnalysisSpec hợp lệ **hoặc** câu hỏi tự do kèm `connection_id` (EXPLORATORY); `budget` không vượt
cấu hình tenant; `output_contract.shape` khớp node cuối. Trả `list[PlanIssue{code, node_id, path, message}]`.

**Nối vào runtime (R0 chỉ làm phần không đổi hành vi):**

- Orchestrator: `plan_formulate` → thêm bước `to_plan_ir(plan)` (Task → PlanNode kind `AGENT`,
  capability = agent_id, outputs `[text]`) → `validate` → lỗi → `__refusal PLANNER_ERROR` (không chạy).
  Executor nhận PlanIR; `task_run` ghi trạng thái từng node (bảng `runtime.task_run`, dùng cho resume
  và cho "chạy lại xuôi dòng" ở R2).
- Agent đơn: `tool_calls` → PlanIR một lớp (mỗi call một node) chỉ để **phát frame `__plan`** và
  validate capability; thực thi vẫn theo graph canvas.
- Frame `__plan` (PlanIR) và `__step {node_id, status, started_at, ended_at, error_code}`.
- `output_contract` trong R0 do hệ thống suy từ loại câu (scalar/table/text); người sửa được từ R2.

**Test.** `tests/unit/contracts/test_plan_ir.py` (fixture + cổng tương thích); `test_plan_validate.py`
(chu trình, cổng lệch kiểu, capability lạ, budget); orchestrator: plan không hợp lệ → không có task nào chạy.

## 10. R0-11 Lớp lỗi trong judge

**Vì sao.** Judge chỉ có `PLANNING_ERROR | GROUNDING_ERROR | DATA_QUALITY` và luôn quay về planner/
responder; không phân biệt thiếu dữ liệu, sai ý định, sai vật chất hoá (01 §3.5).

**Thiết kế.** `judge_phase1` và `judge` gán `error_class ∈ {DATA_MISSING_DIRECT, DATA_MISSING_INDIRECT,
PLAN_LOGIC, TOOL_GRAMMAR, INTENT_MISMATCH, MATERIALIZATION, EVIDENCE_INSUFFICIENT}`; bảng định tuyến
tất định: `DATA_MISSING_*` → `__refusal NO_DATA|NO_JOIN_PATH` (không replan); `PLAN_LOGIC`,
`TOOL_GRAMMAR` → planner/tool một lần với chỉ thị của checker; `INTENT_MISMATCH` → `CLARIFY`;
`MATERIALIZATION` → ép lại theo `output_contract` (tất định, không LLM); `EVIDENCE_INSUFFICIENT` → một
vòng retrieval sâu hơn rồi `INSUFFICIENT_EVIDENCE`. Cùng lớp lặp lại → dừng. Phát hiện lặp bằng chữ ký kế hoạch trong state
(`attempted_plans` + `_plan_signature`, đã bỏ ở R0-7 cùng plan dự phòng; lấy lại từ commit trước R0-7).

**Test.** `test_judge_error_classes.py`: mỗi lớp → đúng đường; lớp lặp → dừng sau 2.

## 11. R0-9 Đo model (D1)

Không đổi mã. Chạy `make bench` (TTFT/decode/throughput theo vai) và `model/eval` tier online trên hai
cấu hình: hiện tại (Qwen3-14B-AWQ main, 4B fast) và một ứng viên lớn hơn cho vai planner/SQL (GPU riêng
hoặc API theo policy). Báo cáo một trang: chất lượng theo nhãn tin cậy, chi phí, độ trễ; khuyến nghị cho
D1. Việc này chạy song song, không chặn R0-1…R0-11.

## 12. Thứ tự và phụ thuộc trong R0

```text
R0-7 lỗi có cấu trúc ──┐
R0-1 result set ───────┼──► R0-8 __verification ──► R0-11 lớp lỗi judge
R0-10 Plan IR ─────────┘            │
R0-2 MCP chuẩn ◄── R0-6 secret OpenBao (api_key_ref)
R0-3 checkpointer      R0-4 canvas      R0-5 /run      R0-9 model (song song)
```

R0-1 và R0-10 là hai việc lớn nhất và độc lập nhau; làm song song bởi hai người. R0-7 làm trước R0-8/
R0-11 vì frame refusal là đầu ra của chúng.

## 13. Definition of done của R0

- Tất cả test unit `workflow_runtime`, `sandbox_executor`, `contracts`, `platform_workers` xanh trong
  container; integration `workflow_runtime` (resume HITL) xanh.
- Không `db_results`, `MemorySaver`, `_pending_reviews`, `_FALLBACK_TOOL_CALLS`, `"/execute"` (mcp),
  `WAREHOUSE_PASSWORD__` trong mã/manifest.
- `GET /api/nodes/registry` là nguồn duy nhất của Builder; mọi node trên canvas chạy được.
- Playground: một câu hỏi SQL ra bảng đúng và đủ; `__plan`, `__step`, `__result_set`, `__verification`,
  `__refusal` hiện trong trace.
- Không finding ruff/mypy mới so với baseline; strict mypy surfaces xanh; `make frontend-lint` xanh.
- RUNBOOK cập nhật: HITL bền, MCP cấu hình, OpenBao policy cho sandbox/crawler.
- Bảng điểm D1 nộp cho anh Linh.
