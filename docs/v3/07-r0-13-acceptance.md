# R0-13 — Biên bản nghiệm thu R0 (draft)

> Ngày kiểm tra: 13/10/2026. Môi trường: OrbStack Kubernetes local. Trạng thái:
> **chưa ký nghiệm thu**; tài liệu này ghi bằng chứng chạy được và các blocker còn lại.

## Kết quả

| Phạm vi | Lệnh / kiểm tra | Kết quả |
| --- | --- | --- |
| Worker-agent | `make test-unit UNIT_SUITES='worker_agent:worker-agent' TEST_JOBS=1` | **PASS — 71 passed** |
| E2E anonymous | `make e2e E2E_TESTS=tests/integration/test_v3_question_e2e.py PYTEST_ARGS='-k anonymous -v'` | **PASS — 1 passed** |
| E2E V3 question | `make e2e E2E_TESTS=tests/integration/test_v3_question_e2e.py PYTEST_ARGS='-v -s'` | **PASS — 2 passed** |
| API unit | `make test-unit UNIT_SUITES='api:api' TEST_JOBS=1` | **BLOCKED — 234 passed, 2 failed** |
| Contracts unit | `make test-unit UNIT_SUITES='contracts:kb-backend' TEST_JOBS=1` | **PASS — 243 passed** |
| Sandbox unit | `make test-unit UNIT_SUITES='sandbox_executor:sandbox_executor' TEST_JOBS=1` | **PASS — 218 passed** |

## Blocker cần xử lý

1. API: thay đổi R1-01 đang có trong working tree đã thêm `GET /v1/connectors`
   và `GET /v1/secret-refs` vào A2/code nhưng chưa cập nhật A3 snapshot. Hai test
   fail ở `test_a3_contract.py` và `test_catalogue.py`.
Các blocker trên cần được xử lý trước khi cập nhật R0-13 thành `done <commit>`.
Không có blocker ở đường chạy V3 question ngoài cluster: cả anonymous rejection
và authenticated question stream đều đã đạt.
