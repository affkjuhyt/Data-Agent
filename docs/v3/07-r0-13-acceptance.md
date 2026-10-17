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
