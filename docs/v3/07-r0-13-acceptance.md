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
| R0-8 `__verification` | Làm lại trong V3: `worker_agent/answer/verification.py` ghi `number_check.verification` ({checks, verdict, iteration}, hợp đồng `VerificationFrame`) một lần cùng câu trả lời; `useQuestionStream` đưa vào `VerifierBadge` | `tests/unit/worker_agent/test_verification.py`; `tests/integration/worker_agent/test_answer_verification.py`; FE `useQuestionStream.test.tsx` (`readVerification`), `VerifierBadge.test.tsx` | Màn Hỏi: hỏi "Tỷ lệ nợ xấu theo vùng tháng 9/2026", mở huy hiệu "Đã kiểm" dưới câu trả lời | Ba mục kiểm (số có nguồn, mỗi số có lần chạy, khớp số đã nộp) đều ✓; câu bị từ chối không có huy hiệu | | |
| R0-9 Đo model | `docs/v3/06-r0-9-model-benchmark.md`, `model/bench` | `tests/unit/repo/test_bench_harness.py`, `test_eval_gate.py` | `make bench` rồi `make bench-gate` trên GPU | Gate 0 (đạt) | | |
| R0-10 Plan IR | V3: `questions.py::_plan_ir` ghi `qa.plan` (phiên bản 2); `libs/contracts/v1/plan.py` không ai dùng | `tests/unit/contracts/test_plan_ir.py`; `test_questions.py::test_a_question_naming_no_known_metric_ends_in_a_coded_refusal`; `test_runs.py` (từ chối trước khi chạy) | Hỏi rồi `GET /v1/questions/{id}` | Có các bước của kế hoạch; câu bị từ chối có `steps: []` | | |
| R0-11 Lớp lỗi | Làm lại trong V3 cho lượt chạy agent: `worker_agent/failures.py` (8 lớp, bảng định tuyến tất định: từ chối kèm mã, hỏi lại, thử lại; cùng lớp quá số lần cho phép thì dừng); `runs.py` ghi lớp vào output của bước và `agent_run.stop_detail` | `tests/unit/worker_agent/test_failures.py`; `test_runs.py::test_a_goal_that_names_no_metric_fails_and_says_why`, `test_after_three_retries_the_run_fails_and_the_owner_is_told`, `test_a_wrong_query_is_not_retried` | Chạy thử agent với mục tiêu không nêu chỉ số; xem `stop_detail` của lượt chạy (`GET /v1/agent-runs/{id}`) | `error_class` `INTENT_MISMATCH`, `route` `clarify` | | |
| R0-12 RUNBOOK, ghi chú UI | `docs/RUNBOOK.md` §8, §10a, "V3 MCP tool calls"; `docs/v3/05-ui-v3.md` "Workspace (D9)" | — | Đọc tài liệu | Đúng với mã hiện tại | | |

## Lỗi ghi nhận (đề xuất mức, người nghiệm thu chốt)

| # | Mức | Story | Lỗi |
| --- | --- | --- | --- |
| 1 | ~~Cao~~ Đã sửa | R0-8, R0-11 | Làm lại trong V3 (nhánh `r0-8-11`): kết quả kiểm đi cùng câu trả lời vì A1.6 không có sự kiện verification; lớp lỗi áp cho lượt chạy agent, vì đường hỏi nhanh V3 không có vòng judge. `EVIDENCE_INSUFFICIENT` và `MATERIALIZATION` có đường đi nhưng chưa có điểm nào phát ra |
| 2 | ~~Trung bình~~ Đã sửa | R0-7 | Mã từ chối và hành động tiếp theo được lưu cùng câu trả lời (`number_check.refusal`, vì `qa.answer` không có cột cho mã), `answerMessage.readRefusal` đưa vào `RefusalCard`, nên nút thử lại / diễn đạt lại theo mã có cả khi tải lại trang. Còn: `AGENT_CONFIG_ERROR`, `PLANNER_ERROR` không được V3 phát ra, và chưa có test cho đường lỗi CSDL |
| 3 | Trung bình | R0-2 | Adapter MCP không nối vào đường câu hỏi hay lượt chạy; chưa có test khoá cache theo tenant |
| 4 | Trung bình | R0-10 | Bộ kiểm tĩnh kế hoạch (chu trình, kiểu cổng, quyền, ngân sách) đã xoá; kế hoạch V3 là mẫu kiểm tại chỗ |
| 5 | Trung bình | R0-1 | Luồng V3 không gửi result set; bảng dựng từ `answer.figures` |
| 6 | Thấp | R0-6 | Không có kiểm tra CI grep `WAREHOUSE_PASSWORD__` như "Xong khi" yêu cầu (grep tay hiện rỗng) |
| 7 | Thấp | R0-3, R0-12 | RUNBOOK §10a còn tả `runtime.pending_review`; migration `v3_0003` vẫn tạo bảng không ai đọc |
| 8 | Thấp | — | Suite `contracts` chạy trong image `kb-backend`, đang bị gỡ: cần chuyển image trước khi xoá |
| 9 | ~~Thấp~~ Đã sửa | — | `test_bench_harness` và `test_org_identity::test_list_needs_ask` xanh lại trên `main` (`6eaba3fe`, `84beabbd`; test danh sách Data Space theo A2 mới) |

## Chạy kịch bản trên cụm, 23/10/2026

Cụm OrbStack chạy `a5d2b51f` (`main` `491e50ae` cộng email báo cáo, bản tin, mã từ chối). Các bước gọi qua Kong
(30100) bằng tài khoản `helios-engineer`, Data Space `DS-DEMO`; người kiểm: Claude (agent), chưa phải người nghiệm thu.

| Story | Đã làm | Kết quả | Đạt? |
| --- | --- | --- | --- |
| R0-1 | Hỏi "Doanh thu thuần theo Khu vực tháng này" | `answered`, 6 số, mỗi số có lần chạy truy vấn; "theo vùng" được hỏi lại "Khu vực" (BR-80) | Đạt |
| R0-2 | — | Adapter MCP chưa nối, không kiểm được trên cụm | Không áp dụng |
| R0-3 | — | Lượt chạy thử không ghi nên không dừng ở checkpoint; cần một agent chạy thật có bước ghi. Chưa kiểm tay (integration `test_runs.py` xanh) | Chưa kiểm |
| R0-5 | `POST /v1/questions` kèm `memory_context` | 422 `validation_failed` nêu tên trường | Đạt |
| R0-6 | `env` của pod `api`, `worker-agent`; `git grep WAREHOUSE_PASSWORD__ -- deploy backend` | 0 biến, 0 tệp | Đạt |
| R0-7 | Hỏi "Doanh thu thuần" theo số đã nộp mà không nêu kỳ | `refused`, `number_check.refusal` = `NO_DATA_FOR_PERIOD` / `rephrase` | Đạt |
| R0-8 | Câu trả lời R0-1 | `verification.verdict` = PASS, ba mục ✓ | Đạt, xem lỗi 10 |
| R0-9 | — | Cần GPU (`make bench`), không chạy | Chưa kiểm |
| R0-10 | `GET /v1/questions/{id}` của câu R0-1 | Có kế hoạch (`chips`, bước semantic) | Đạt |
| R0-11 | Chạy thử agent "Báo cáo định kỳ" với mục tiêu không nêu chỉ số | `failed`, `stop_detail` = `INTENT_MISMATCH` / `clarify` / `UNRESOLVED_ENTITY` | Đạt |
| R0-12 | Đọc RUNBOOK §8, §10a, MCP | §10a còn tả `runtime.pending_review` (lỗi 7) | Đạt có điều kiện |

Lỗi phát hiện khi chạy (đề xuất mức):

| # | Mức | Story | Lỗi |
| --- | --- | --- | --- |
| 10 | Cao | R0-7, R0-8 | "Doanh thu phí bảo hiểm nhân thọ tháng này" (Data Space không có chỉ số này) được hiểu là `NET_REVENUE` và trả lời "Doanh thu phí bảo hiểm nhân thọ tháng này là 262.000.000 VND": sai chỉ số, nhắc lại cụm từ của người hỏi như tên chỉ số, và kết quả kiểm vẫn PASS vì không mục nào so chỉ số đã hiểu với câu hỏi |
| 11 | Trung bình | R0-7 | Hỏi kỳ không có dữ liệu ("Doanh thu thuần tháng 1/2015") trả lời "… là CHƯA CÓ" với kiểm PASS, thay vì từ chối `NO_DATA_FOR_PERIOD` |
| 12 | Trung bình | R0-7 | "Tỷ lệ nợ xấu tháng này" được hỏi lại với hai chỉ số không liên quan (Doanh thu thuần, Số khiếu nại) thay vì báo không có chỉ số này |

## Ký nghiệm thu

| Vai | Họ tên | Kết luận (Đạt / Đạt có điều kiện / Không đạt) | Ngày | Chữ ký |
| --- | --- | --- | --- | --- |
| Người nghiệm thu (QA) | | | | |
| Phụ trách kỹ thuật | | | | |
| Phụ trách sản phẩm | | | | |
