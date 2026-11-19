# DS-AW: the dynamic agent beside the governed path (baseline, 03/11/2026)

`make agent-eval AGENT_EVAL_ORG=a0000000-0000-0000-0000-000000000001 AGENT_EVAL_AS=helios-admin
AGENT_EVAL_ARGS="--engines agentic,governed"` on the local cluster, worker-agent at 01138192 (plus the
eval CLI from a /tmp overlay), eval set "Câu hỏi AdventureWorks" (60 items), one run per engine.

| engine | passed (eval scorer) | values right, any order | by the agent | handed over | governed | mean steps | mean tokens | mean seconds |
|---|---|---|---|---|---|---|---|---|
| agentic | 3 | 14 | 35 | 25 | 0 | 9.4 | 19 739 | 11.8 |
| governed | 3 | 15 | 0 | 0 | 60 | 4.7 | 6 460 | 4.2 |

No item passes on one engine and fails on the other.

## What decides the score

- **The semantic model, not the engine.** The items expect 15 metrics (DOANH_THU, DOANH_THU_INTERNET,
  LAI_GOP_INTERNET, BIEN_GOP_INTERNET, SO_DON_INTERNET, TON_KHO, …); the space had 5 certified
  (SO_LUONG_BAN_INTERNET, CHI_TIEU_DOANH_SO, SU_CO_TREN_CUOC_GOI, TAI_CHINH_NGAN_SACH, and
  SO_KHACH_HANG_INTERNET from dbc93967). The scorer also checks the metric an item was read as, so
  an item answered with the right value by exploratory SQL still fails: "passed" stays near zero
  on both engines until those metrics are approved.
- **Values right** is the engine-level signal: 14 vs 15.

## The agent's failures, by kind

1. A wrong table in exploratory SQL: "Doanh thu năm 2013" answered with the sales quota
   (29.507.000), "Biên lợi nhuận gộp" with gross profit, "Số cuộc gọi" with a count of rows.
2. A filter dropped: "Doanh thu Internet nhóm hàng Bikes" answered with the total.
3. Nothing to answer, answered anyway: "Doanh thu năm 2009 là 0", a churn rate computed from raw
   tables, a 2015 forecast; these should say there is no data, or that it cannot be answered.
4. Cost: twice the steps, three times the tokens and seconds of the governed path.

## Second run, first 20 items, agentic only (03/11/2026)

Worker-agent code at 3751df40 plus RES-11/12 (from a /tmp overlay), after the DS-AW metrics were
approved (f79cb2b6 unblocked the review page). Same 20 items as the first 20 of the baseline:

| | passed (eval scorer) | values right, any order | by the agent / handed over | mean steps | mean tokens | mean seconds |
|---|---|---|---|---|---|---|
| baseline (first 20) | 2 | 7 | 12 / 8 | 8.8 | 17 250 | 10.6 |
| this run | 17 | 17 | 14 / 6 | 7.2 | 10 945 | 7.2 |

Both moved at once, so the gain is not the code's alone: most of "passed" comes from the metrics
being certified (the scorer checks the metric an item was read as), the cost from RES-05 (simple
questions skip the two reviews; memories are read only when the turn says something lasting).
Still wrong: "Doanh thu năm 2013" (4 057 065, one cut of the total); "Doanh thu nhóm hàng
Components" handed over and the governed path found no figure. "Lãi gộp Internet" has the right
total but also the breakdown it showed as figures, so the scorer counts it a miss.

## RES-02 (SQL audit + ban rewrite)

After the explore_sql semantic audit and `refused_sql` ban (fingerprint + one in-step retry), re-run:

```bash
make agent-eval AGENT_EVAL_ORG=… AGENT_EVAL_AS=… AGENT_EVAL_ARGS="--engines agentic --limit 20"
```

Watch for kind-1 misses from the baseline (quota as revenue, gross profit as margin, unfiltered
channel). A refused SQL must not win again in the same turn; the timeline step detail shows
`audit` and `refused`.

## AE-07 catalogue rules for exploratory SQL (PoC-3, 16/11/2026)

`sqlgen/audit_rules.py` holds a statement to the metric the deterministic reading of the question
names (its tables, all of them when nothing is filtered, its measure columns, its division, its
distinct counts) and to the question's own filter values and period, before the statement runs;
on both paths (`answer_by_sql` and `explore_sql`). `SQL_AUDIT_RULES=off` turns it off.

Measured offline, no model (`tests/integration/worker_agent/test_adventureworks_audit_rules.py`, the
real AdventureWorksDW and `ds-aw.yaml`):

| | result |
| --- | --- |
| reference SQL of the eval set refused (false refusals) | 0 / 48 |
| questions the rules held to a metric | 42 / 48 |
| recorded misses refused on the real model (quota for revenue, one channel for both, gross profit for margin, order lines for orders) | 4 / 4 |

Two rules were narrowed on that run: a filtered slice is not held to every table of its metric
(aw020: Components are sold to resellers only), and a reading that cuts a metric along a dimension
it does not allow holds nothing (aw045: "doanh số" is a revenue synonym, Trade Sales a finance
account). Not measured yet: the same 20 items through `make agent-eval` with the served model (the
local cluster's workers wait on migration v3_0035, 16/11/2026).

## The two stock misses of 12/11/2026 are not coverage's (16/11/2026)

"Tồn kho cuối tháng 12/2013" and "Giá trị tồn kho cuối tháng 6/2014" failed on the agentic engine
only. Compiling TON_KHO and GIA_TRI_TON_KHO by month on the real AdventureWorksDW and reading them
with `semantic/coverage.py`: both cover 2013-12 and 2014-06 (43 months, the last one not cut off),
so `query_metric` reads the asked month and moves nothing. The miss is in the agentic turn (plan,
tool choice or reporter); `present_gate.no_figure` is not shown to be the cause. AE-09 waits for a
turn trace of these two items from a live run before anything in the gate changes.

## Live runs after the Phase 0 fixes (16/11/2026)

`make agent-eval … --engines agentic,governed --limit 20` on the local cluster, the same 20 items as
12/11. Three runs, each after a deploy: main at bc177e32 (sqlglot 30, AE-07 rules), 0ad096a8
("cuối" read as part of a period), 33cb5869 (a "none" argument read as null).

| run | agentic passed | governed passed | agentic tokens | agentic seconds |
| --- | --- | --- | --- | --- |
| 12/11 (9e503407, before) | 17 | 20 | 22 626 | 16.1 |
| bc177e32 | 15 | 20 | 21 658 | 12.7 |
| 0ad096a8 | 14 | 20 | 23 551 | 13.4 |
| 33cb5869 | 14 | 20 | 23 243 | 12.8 |

None of the agentic misses went through `explore_sql`, so the AE-07 rules did not touch them; the
run-to-run spread on the same code is ±2 items (planner variance). What the turn traces showed and
what was fixed:

- Both stock items were refused by the RES-04 check: "cuối" in "cuối tháng 12/2013" was left over
  after the period was read and taken for a quantity no metric names. Fixed in the vi pack
  (0ad096a8); `query_metric` now reads TON_KHO for 2013-12. The half of RES-04 that forbade
  exploratory SQL for a quantity no metric names is withdrawn: that question is exploratory SQL
  on the space's tables, or “không có cột để tính” when `link` finds none. The “refused correctly”
  threshold has to be re-scored and is not re-run in this change.
- "Giá trị tồn kho cuối tháng 6/2014" then failed on `analysis: "none"` written by the planner;
  fixed (33cb5869). The agent now answers 23 603 975.57, the gold value.
- Both stock items still fail the scorer: the planner adds what was not asked (a same-period
  comparison, a Python trend, explain_change) and the answer carries extra figures, or only tables
  with no sentence. "Số đơn Internet năm 2013" answers the gold 21 289 and fails the same way.
  "Doanh thu tháng 6/2013" and "Giá trị đơn bình quân" fail on planner choices (a Python step that
  errors; a fiscal-year cut with two date paths).

Reading: on single-metric questions the agentic engine now gets the numbers right and loses on
over-answering, at 43× the governed path's tokens. This is the case for AE-01 (governed-first
router, ADR-50), not for more fixes in the agent's planning.
