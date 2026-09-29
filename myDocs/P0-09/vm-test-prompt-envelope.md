Bạn đang ở trên VM dev `10.10.55.101` (single-node k3s, có Docker). Nhiệm vụ: chạy kiểm tra cho nhánh
`feat/p0-09-event-envelope` của repo Data-Agent và báo lại kết quả. **Chỉ chạy kiểm tra, không sửa code,
không commit, không push, không deploy, không đụng tới cụm k8s.** Máy này cũng là cụm dev đang chạy: chạy
từng lệnh một, và nếu thấy một phiên khác đang chạy cùng bộ kiểm tra thì dừng lại và báo, không chạy song song.

Người dùng đã `git fetch origin` sẵn trong `~/Data-Agent` (phiên của bạn không có credential GitHub, đừng
tự fetch).

## 0. Chuẩn bị

Không switch nhánh của `~/Data-Agent`. Làm trong worktree riêng:

```bash
cd ~/Data-Agent
git worktree add --detach ~/p0-09-envelope origin/feat/p0-09-event-envelope
git worktree add --detach ~/p0-09-envelope-base origin/main
cd ~/p0-09-envelope && git rev-parse HEAD
```

Hàm dùng chung (chạy trong thư mục worktree; container không để lại cache trong repo):

```bash
py311() { docker run --rm -v "$PWD:/src" -w /src -e PYTHONPATH=/src/backend:/src -e PYTHONDONTWRITEBYTECODE=1 python:3.11.9-slim sh -c "$1"; }
```

## 1. Suite `platform_workers` đầy đủ, trong image thật của worker

Chạy cả suite (không chỉ file mới) để chắc không làm hỏng test cũ:

```bash
cd ~/p0-09-envelope && make test-unit UNIT_SUITES=platform_workers:platform-workers
```

Nếu `make` báo không kéo được image `localhost:5000/dataagent/platform-workers:latest`, ghi lại lỗi và chạy
thay bằng:

```bash
py311 "pip install -q -r backend/services/platform_workers/requirements.txt -r requirements-test.txt && python -m pytest -q -p no:cacheprovider -m unit tests/unit/platform_workers"
```

## 2. mypy strict cho `libs/shared` (bước BLOCKING của CI)

```bash
cd ~/p0-09-envelope && py311 "pip install -q pydantic==2.9.2 mypy && mypy --cache-dir=/tmp/mypy --disallow-untyped-defs --disallow-any-generics --ignore-missing-imports --follow-imports=silent backend/libs/shared"
cd ~/p0-09-envelope-base && py311 "pip install -q pydantic==2.9.2 mypy && mypy --cache-dir=/tmp/mypy --disallow-untyped-defs --disallow-any-generics --ignore-missing-imports --follow-imports=silent backend/libs/shared"
```

Chạy ở cả hai nơi: nếu baseline đã có lỗi thì chỉ lỗi **mới** ở nhánh là vấn đề.

## 3. ruff 0.6.9 trên file mới

```bash
cd ~/p0-09-envelope && py311 "pip install -q ruff==0.6.9 && ruff check --no-cache backend/libs/shared/event_envelope.py tests/unit/platform_workers/test_event_envelope.py; ruff format --no-cache --check backend/libs/shared/event_envelope.py tests/unit/platform_workers/test_event_envelope.py"
```

## 4. import-linter: nhánh và baseline

```bash
cd ~/p0-09-envelope && py311 "pip install -q import-linter && PYTHONPATH=backend lint-imports --no-cache"
cd ~/p0-09-envelope-base && py311 "pip install -q import-linter && PYTHONPATH=backend lint-imports --no-cache"
```

## 5. Dọn dẹp

```bash
cd ~/Data-Agent && git worktree remove ~/p0-09-envelope && git worktree remove ~/p0-09-envelope-base
```

## Báo cáo trả về (đúng khuôn này, dán nguyên dòng tổng kết của từng công cụ)

```
commit: <sha>
1 platform_workers suite: PASS/FAIL  <dòng tổng kết pytest, vd "212 passed in 9.8s">  (chạy bằng make / py311)
2 mypy strict libs/shared: nhánh <dòng tổng kết> | baseline <dòng tổng kết>
3 ruff check:  PASS/FAIL <số lỗi>   ruff format: PASS/FAIL <số file>
4 lint-imports: nhánh <kept/broken> | baseline <kept/broken>
```

Với mỗi FAIL: dán nguyên văn lỗi (tên test + traceback, dòng mypy/ruff kèm đường dẫn và số dòng, contract
bị broken kèm chuỗi import). Không tự sửa. Lệnh nào không chạy được vì môi trường thì ghi lệnh + lỗi rồi chạy
tiếp bước sau.
