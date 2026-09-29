Bạn đang ở trên VM dev `10.10.55.101` (single-node k3s, có Docker). Nhiệm vụ: chạy kiểm tra cho nhánh
`feat/p0-09-data-contract` của repo Data-Agentt và báo lại kết quả. **Chỉ chạy kiểm tra, không sửa code,
không commit, không push, không deploy, không đụng tới cụm k8s.** Máy này cũng là cụm dev đang chạy, nên
chạy từng lệnh một, không chạy song song.

## 0. Chuẩn bị

- Tìm bản checkout repo Data-Agentt trên máy (ví dụ `~/Data-Agentt`, `~/*/Data-Agentt`). Nếu không có,
  clone vào `~/p0-09-check`: `git clone <origin của repo> ~/p0-09-check`.
- Nếu bản checkout có thay đổi chưa commit: **không** stash hay reset nó; clone riêng vào `~/p0-09-check`.
- `git fetch origin && git switch --detach origin/feat/p0-09-data-contract`, ghi lại `git rev-parse HEAD`.
- Tạo worktree baseline để so sánh: `git worktree add --detach /tmp/p0-09-base origin/main`.

Khai báo hàm dùng chung (chạy trong thư mục repo; container không để lại cache trong repo):

```bash
py311() { docker run --rm -v "$PWD:/src" -w /src -e PYTHONPATH=/src/backend:/src -e PYTHONDONTWRITEBYTECODE=1 python:3.11.9-slim sh -c "$1"; }
```

## 1. Unit test của suite mới

```bash
py311 "pip install -q -r backend/services/indicator_api/requirements.txt -r requirements-test.txt && python -m pytest -q -p no:cacheprovider -m unit tests/unit/indicator_api"
```

## 2. mypy strict (giống bước BLOCKING của CI)

```bash
py311 "pip install -q -r backend/services/indicator_api/requirements.txt mypy && mypy --cache-dir=/tmp/mypy --disallow-untyped-defs --disallow-any-generics --ignore-missing-imports --follow-imports=silent backend/services/indicator_api"
```

## 3. ruff 0.6.9 trên file mới và file đã sửa

```bash
py311 "pip install -q ruff==0.6.9 && ruff check --no-cache backend/services/indicator_api tests/unit/indicator_api; ruff format --no-cache --check backend/services/indicator_api tests/unit/indicator_api"
```

## 4. import-linter: nhánh mới và baseline

Nhánh này bật `include_external_packages = True` trong `.importlinter`, ảnh hưởng mọi contract. Chạy ở
cả hai nơi và so kết quả:

```bash
py311 "pip install -q import-linter && cd /src && PYTHONPATH=backend lint-imports --no-cache"
(cd /tmp/p0-09-base && py311 "pip install -q import-linter && PYTHONPATH=backend lint-imports --no-cache")
```

## 5. Image build được và import được

```bash
docker build -f backend/services/indicator_api/Dockerfile -t p0-09-check/indicator-api:test .
docker run --rm p0-09-check/indicator-api:test python -c "import services.indicator_api.main, services.indicator_api.domains.observation; print('import ok')"
docker rmi p0-09-check/indicator-api:test
```

## 6. Dọn dẹp

`git worktree remove /tmp/p0-09-base`. Nếu đã clone `~/p0-09-check` thì giữ lại, ghi đường dẫn vào báo cáo.

## Báo cáo trả về (đúng khuôn này, không tóm tắt lại output dài)

```
commit: <sha của HEAD>
1 pytest:        PASS/FAIL  <dòng tổng kết của pytest, vd "17 passed in 1.2s">
2 mypy strict:   PASS/FAIL  <dòng tổng kết>
3 ruff check:    PASS/FAIL  <số lỗi>
  ruff format:   PASS/FAIL  <số file cần format>
4 lint-imports nhánh:    <số contract kept / broken>
  lint-imports baseline: <số contract kept / broken>
5 docker build:  PASS/FAIL
  import:        PASS/FAIL
```

Với mỗi bước FAIL: dán nguyên văn phần lỗi (traceback, tên test, dòng mypy/ruff có đường dẫn và số dòng,
tên contract bị broken kèm chuỗi import). Không tự sửa. Nếu một lệnh không chạy được vì môi trường (không
kéo được image, không có mạng tới PyPI…), ghi rõ lệnh và thông báo lỗi, rồi chạy tiếp các bước còn lại.
