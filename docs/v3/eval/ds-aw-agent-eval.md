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
