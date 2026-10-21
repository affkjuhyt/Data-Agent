# R0-13 — Biên bản nghiệm thu R0 (draft)

> Ngày kiểm tra: 13/10/2026. Môi trường: OrbStack Kubernetes local. Trạng thái:
> **đạt kỹ thuật local**; biên bản nghiệp vụ vẫn cần người phụ trách ký.

## Kết quả

| Phạm vi | Lệnh / kiểm tra | Kết quả |
| --- | --- | --- |
| Worker-agent | `make test-unit UNIT_SUITES='worker_agent:worker-agent' TEST_JOBS=1` | **PASS — 71 passed** |
| E2E anonymous | `make e2e E2E_TESTS=tests/integration/test_v3_question_e2e.py PYTEST_ARGS='-k anonymous -v'` | **PASS — 1 passed** |
| E2E V3 question | `make e2e E2E_TESTS=tests/integration/test_v3_question_e2e.py PYTEST_ARGS='-v -s'` | **PASS — 2 passed** |
| API unit | `make test-unit UNIT_SUITES='api:api' TEST_JOBS=1` | **PASS — 236 passed, 2 skipped** |
| Contracts unit | `make test-unit UNIT_SUITES='contracts:kb-backend' TEST_JOBS=1` | **PASS — 243 passed** |
| Sandbox unit | `make test-unit UNIT_SUITES='sandbox_executor:sandbox_executor' TEST_JOBS=1` | **PASS — 218 passed** |

Không có blocker ở đường chạy V3 question ngoài cluster: cả anonymous rejection
và authenticated question stream đều đã đạt.

API A3 đã được đồng bộ cho hai endpoint Connections đang có trong A2/code.
Hai test skip là kiểm tra endpoint user-authenticated chưa được phục vụ trong
toàn bộ A2, không thuộc đường chạy R0.

## Kiểm tra lại 17/10/2026

Trên commit `68eda4d5` (đã push), sau khoảng 40 commit kể từ lần kiểm 13/10. Môi trường như trên.

| Phạm vi | Lệnh | Kết quả |
| --- | --- | --- |
| Toàn bộ unit, 12 suite | `make test-unit` | **PASS 12/12** (api, worker_agent, repo, kb_worker, kb_backend, platform_workers, auth_api, worker_document, sandbox_executor, model_service, contracts, worker_ingest) |
| Toàn bộ integration, 7 suite | `make test-integration` | **6/7 PASS**; suite `api` lỗi 1 test: `test_document_lists.py::test_documents_list_with_their_preprocessing_step` (phần tài liệu, đang tìm nguyên nhân) |
| Cổng eval offline | `python -m model.eval.run --tier offline` rồi `model.eval.gate` | **PASS**, gồm ba chỉ số "vì sao" mới (AC-03-01-07) |
| E2E trên cụm | `make e2e` | **Chưa chạy lại**: cần mật khẩu `helios-engineer` (`E2E_USER`, `E2E_PASS`) |

Còn mở trước khi ký:

1. ~~Test integration tài liệu ở trên.~~ Đã sửa (`64485c45`): test chưa theo nghĩa mới của bộ lọc
   `processing` mà `5cf61f96` đưa vào (chưa xử lý xong, gồm cả đã nhận và chờ duyệt); suite tài liệu 28/28.
2. E2E trên cụm với tài khoản thật.
3. ~~Bảo mật (cao): service `api` nhận root token của OpenBao.~~ Đã sửa và đã deploy (`e2a4ee05`,
   `64485c45`, `c5eb9f17`): `api` đăng nhập bằng AppRole riêng (`dataagent-api-policy`). Quyền đo trong pod
   `api` bằng `sys/capabilities-self`: ghi nhưng không đọc mật khẩu nguồn, chỉ đọc key provider dưới
   `providers/`, chỉ khoá Transit `exec-cond-*`; mọi đường dẫn khác `deny`. `auth-api` (dịch vụ cũ) vẫn
   giữ root token, nằm trong danh sách cắt bỏ (08).

Đã sửa trong lượt này: lineage để lộ id và tên nguồn của Data Space khác (`174c7bae`).

## Kiểm tra lại 22/10/2026

Mã: `main` tại `caee53c8`, chạy trong worktree sạch (không có thay đổi chưa commit). Cụm OrbStack chạy
`4e1a1e05` (nhánh `release/2026-10-20`), chưa có các commit từ 20/10 của `main`: hai nhánh cài trùng
ba tính năng (eval, duyệt data product, xem trước metric) theo hai thiết kế, chưa gộp được nên chưa
deploy `main`.

| Phạm vi | Lệnh | Kết quả |
| --- | --- | --- |
| Toàn bộ unit, 12 suite | `make test-unit` | **11/12 PASS**; `repo` lỗi 1 test: `test_bench_harness.py::test_worker_agent_profiles_carry_the_prompt_the_call_site_sends[planner-understand/llm_reader.py-400]` (prompt trong `understand/llm_reader.py` khác profile bench) |
| Toàn bộ integration, 7 suite | `make test-integration` | **6/7 PASS** (worker_agent 202, migrate 123, worker_document 7, worker_ingest 1, sandbox_executor 5 + 31 skip, contracts 2); `api` 639/640, lỗi `test_org_identity.py::test_list_needs_ask` (`GET /v1/data-spaces` trả 200 cho người không có `ask`), lỗi có sẵn trên `main` |
| E2E câu hỏi V3 | `make e2e` (`test_v3_question_e2e.py`, tài khoản `helios-engineer`) | **PASS 2/2**: từ chối khi không đăng nhập; câu hỏi qua Kong tới worker-agent và stream đến `done` |
| E2E JWT audience | `make e2e E2E_TESTS=tests/integration/test_jwt_audience.py` | 3/4; lỗi `/api/auth/health` 503 vì `auth_api` (dịch vụ cũ) không chạy trên cụm local, không thuộc V3 |
| E2E tenant isolation | `make e2e E2E_TESTS=tests/integration/test_tenant_isolation.py` | Không áp dụng: test của dịch vụ cũ (secret, API key, IP rule qua `auth_api`); thêm vào đó mật khẩu `helios-admin` bị Keycloak từ chối |

Mục 2 "còn mở" của lần 17/10 (E2E với tài khoản thật) đã chạy: đường câu hỏi V3 đạt.

## Kịch bản nghiệm thu theo story

Phần lớn mã R0 nằm trong `workflow_runtime`, đã xoá ở `2c1dac2c` cùng test của nó; V3 làm lại một số việc
ở `worker_agent` và `api` theo hình khác. Cột "Bằng chứng tự động" chỉ ghi test còn chạy ở `caee53c8`.
Người nghiệm thu chạy cột "Bước kiểm trên cụm", ghi Đạt/Không đạt và mức lỗi.

| Story | Còn ở đâu | Bằng chứng tự động | Bước kiểm trên cụm | Kết quả mong đợi | Đạt? | Mức lỗi |
| --- | --- | --- | --- | --- | --- | --- |
| R0-1 Kênh result set | Hợp đồng `libs/contracts/v1/result_set.py`; FE `features/ask/resultSets.ts` | `tests/unit/contracts/test_models.py`; FE `AnswerBlock.test.tsx` | Màn Hỏi: "Doanh thu thuần tháng này theo vùng" | Bảng đủ dòng, khớp nguồn | | |
| R0-2 MCP | `worker_agent/tools/mcp.py`, `tools/plan.py` | `tests/unit/worker_agent/test_mcp.py` (6 test) | Không kiểm được: adapter chưa nối vào câu hỏi hay lượt chạy | — | | |
| R0-3 HITL bền | `worker_agent/runs.py`, `hitl.py`, `resolutions.py`; `inbox.hitl_request` | `tests/integration/worker_agent/test_runs.py`, `test_hitl.py` | Chạy thử agent có checkpoint ghi → đợi ở Cần bạn → `kubectl -n dataagent-service rollout restart deployment/worker-agent` → duyệt | Lượt chạy kết thúc đúng một lần, không lặp bước | | |
| R0-4 Canvas | Cắt (chuyển R4) | — | — | — | Không áp dụng | |
| R0-5 `/run` không tin client | `api/modules/qa/schemas.py` (`extra="forbid"`) | `tests/unit/api/test_qa_contract.py::test_question_requires_text_and_refuses_fields_a3_does_not_name`; e2e từ chối ẩn danh | `POST /v1/questions` qua Kong với `{"text":"x","memory_context":"…"}` và token hợp lệ | 422 nêu tên trường | | |
| R0-6 Sandbox, OpenBao | `sandbox_executor/auth.py`, `warehouse_secrets.py`; `worker_agent/query/secrets.py`; crawler `secrets.py` | `tests/unit/sandbox_executor/test_r0_6_security.py`; `tests/unit/platform_workers/test_schema_crawler_secrets.py`; `tests/unit/worker_agent/test_secrets.py`; `test_executor.py::test_credentials_of_another_organisation_are_refused` | Trong pod `api`: `sys/capabilities-self` trên `secret/data/warehouse/…`; `git grep WAREHOUSE_PASSWORD__ -- deploy backend` | Không đọc được mật khẩu nguồn; grep rỗng | | |
| R0-7 Mã lỗi, không fallback | `libs/contracts/v1/errors.py`; `worker_agent/questions.py` `_refuse`; FE `RefusalCard.tsx` | `tests/unit/contracts/test_refusal.py`; `test_answers.py::test_an_unknown_metric_is_refused_with_a_way_forward`; FE `RefusalCard.test.tsx` | Màn Hỏi: hỏi chỉ số Data Space không có | Thẻ từ chối "Chưa nhận ra chỉ số…", không có câu trả lời bịa | | |
| R0-8 `__verification` | Chỉ còn hợp đồng `verification.py` và FE `VerifierBadge.tsx`; không nơi nào phát | FE `VerifierBadge.test.tsx` | Không kiểm được | — | | |
| R0-9 Đo model | `docs/v3/06-r0-9-model-benchmark.md`, `model/bench` | `tests/unit/repo/test_bench_harness.py`, `test_eval_gate.py` | `make bench` rồi `make bench-gate` trên GPU | Gate 0 (đạt) | | |
| R0-10 Plan IR | V3: `questions.py::_plan_ir` ghi `qa.plan` (phiên bản 2); `libs/contracts/v1/plan.py` không ai dùng | `tests/unit/contracts/test_plan_ir.py`; `test_questions.py::test_a_question_naming_no_known_metric_ends_in_a_coded_refusal`; `test_runs.py` (từ chối trước khi chạy) | Hỏi rồi `GET /v1/questions/{id}` | Có các bước của kế hoạch; câu bị từ chối có `steps: []` | | |
| R0-11 Lớp lỗi của judge | Đã xoá cùng `workflow_runtime` | — | Không kiểm được | — | | |
| R0-12 RUNBOOK, ghi chú UI | `docs/RUNBOOK.md` §8, §10a, "V3 MCP tool calls"; `docs/v3/05-ui-v3.md` "Workspace (D9)" | — | Đọc tài liệu | Đúng với mã hiện tại | | |

## Lỗi ghi nhận (đề xuất mức, người nghiệm thu chốt)

| # | Mức | Story | Lỗi |
| --- | --- | --- | --- |
| 1 | Cao | R0-8, R0-11 | Không còn mã: không có khung `__verification` và không có lớp lỗi của judge. Cần quyết định làm lại trong V3 hay đưa khỏi phạm vi R0 |
| 2 | Trung bình | R0-7 | `qa.answer` không lưu mã từ chối; FE gán cứng `code: 'REFUSED'`, nên hành vi theo mã (thử lại, hỏi lại) không tới được người dùng. `AGENT_CONFIG_ERROR`, `PLANNER_ERROR` không còn được phát; không test nào cho đường lỗi CSDL |
| 3 | Trung bình | R0-2 | Adapter MCP không nối vào đường câu hỏi hay lượt chạy; chưa có test khoá cache theo tenant |
| 4 | Trung bình | R0-10 | Bộ kiểm tĩnh kế hoạch (chu trình, kiểu cổng, quyền, ngân sách) đã xoá; kế hoạch V3 là mẫu kiểm tại chỗ |
| 5 | Trung bình | R0-1 | Luồng V3 không gửi result set; bảng dựng từ `answer.figures` |
| 6 | Thấp | R0-6 | Không có kiểm tra CI grep `WAREHOUSE_PASSWORD__` như "Xong khi" yêu cầu (grep tay hiện rỗng) |
| 7 | Thấp | R0-3, R0-12 | RUNBOOK §10a còn tả `runtime.pending_review`; migration `v3_0003` vẫn tạo bảng không ai đọc |
| 8 | Thấp | — | Suite `contracts` chạy trong image `kb-backend`, đang bị gỡ: cần chuyển image trước khi xoá |
| 9 | Thấp | — | `test_bench_harness` (prompt `llm_reader.py`) và `tests/integration/api/test_org_identity.py::test_list_needs_ask` (`GET /v1/data-spaces` trả 200 cho người không có `ask`) đỏ trên `main` |

## Ký nghiệm thu

| Vai | Họ tên | Kết luận (Đạt / Đạt có điều kiện / Không đạt) | Ngày | Chữ ký |
| --- | --- | --- | --- | --- |
| Người nghiệm thu (QA) | | | | |
| Phụ trách kỹ thuật | | | | |
| Phụ trách sản phẩm | | | | |
