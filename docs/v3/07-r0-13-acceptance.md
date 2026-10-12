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
