# PoC-2: snapshot of a dataset version (DF-04)

What `libs/snapshot/writer.py` costs on two million rows, to set `SNAPSHOT_MAX_ROWS`, the writer's
defaults and the memory of the pods that call it (DATA_PIPELINE_SPEC §1 C).

Measured 19/11/2026 by `tests/integration/worker_agent/test_snapshot.py::test_two_million_adventureworks_rows_within_the_budget`
(`make test-integration INTEGRATION_SUITES="worker_agent:worker-agent" PYTEST_ARGS="-m performance -s tests/integration/worker_agent/test_snapshot.py"`).
It ran in the worker-agent image on the dev machine (OrbStack, arm64), against the suite's
PostgreSQL 16 container.

## Data

`fact_internet_sales` of AdventureWorksDW (60 398 rows) taken 34 times: 2 053 532 rows,
29 columns. Each copy gets its own number, order number and md5 key, so the file is as large as
two million distinct rows make it. A plain copy compresses to 1 MB and measures nothing.

## Results

Peak memory is VmHWM of a process that runs only the writer. `ru_maxrss` keeps the peak of the
pytest process that forked it, which is why the first runs read ~330 MiB more.

| CSV block | Row group | Parser threads | Seconds | Peak RSS | File |
| --- | --- | --- | --- | --- | --- |
| 16 MiB | 1 048 576 | per core | 6.6 | 723 MiB | 38.2 MB |
| 16 MiB | 131 072 | per core | 7.7 | 680 MiB | 41.4 MB |
| 1 MiB | 1 048 576 | per core | 8.9 | 445 MiB | 38.1 MB |
| 1 MiB | 131 072 | per core | 8.2 | 220 MiB | 41.2 MB |
| **1 MiB** | **131 072** | **one** | **7.8** | **179 MiB** | **41.2 MB** |

The memory is in the parsing. COPY into the temporary CSV peaks at 77 MiB, and the interpreter
with pyarrow imported at 68 MiB. Writing the Parquet adds little. Arrow's system allocator in
place of its default doubles the peak (1.2 GiB). jemalloc is not built into pyarrow's arm64
wheel.

## Decisions

- The writer's defaults are the last row: 1 MiB blocks, 131 072-row groups and a single parser
  thread. The spec's 1 048 576-row groups cost 4× the memory for a file 8 % smaller.
- At ~4 s per million rows, `SNAPSHOT_MAX_ROWS` keeps its 20 000 000 default (~80 s, under the
  120 s budget). The temporary CSV of that many rows is ~5 GB of the pod's disk, which DF-05 must
  give it (an `emptyDir` with a `sizeLimit`) before raising the bar.
- worker-ingest's memory limit is 256Mi, too tight for the writer's 179 MiB on top of the worker.
  DF-05 therefore takes snapshots in worker-agent (`services/worker_agent/snapshots.py`, on
  `dataset.version_created`), whose limit went from 512Mi to 768Mi; its request is unchanged.
