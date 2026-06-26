"""Production endpoint test for the DPTS planner.

Exercises the real HTTP API against a running stack (docker compose up):
  POST /api/plans
  GET  /api/users/{user_id}/plans/{plan_id}
  POST /api/users/{user_id}/plans/{plan_id}/node-failed

Run:  PLANNER_URL=http://localhost:8080 python tests/test_api.py   (or: pytest tests/test_api.py)
A fresh user_id per run keeps the cache assertions deterministic.
"""

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PLANNER_URL", "http://localhost:8080")
TIMEOUT = float(os.environ.get("PLANNER_TIMEOUT", "180"))
USER = f"test_{int(time.time())}"
QUERY = "Build a daily sales report from a Postgres orders table"


def _post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def _get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def test_production_endpoints():
    # 1. create a plan
    code, d = _post("/api/plans", {"user_id": USER, "user_input": QUERY})
    assert code == 201, (code, d)
    plan = d["plan"]
    pid = plan["plan_id"]
    assert plan["user_id"] == USER
    assert plan["targets"] and all(t["node_id"] and t["tasks"] for t in plan["targets"])
    assert plan.get("expires_at"), "plan must carry a TTL"

    # 2. executor fetches the ready plan
    code, got = _get(f"/api/users/{USER}/plans/{pid}")
    assert code == 200 and got["plan_id"] == pid

    # 3. another user cannot read it
    code, _ = _get(f"/api/users/someone_else/plans/{pid}")
    assert code == 404

    # 4. identical request for the same user is a cache hit
    code, d2 = _post("/api/plans", {"user_id": USER, "user_input": QUERY})
    assert code == 201 and d2["cached"] is True and d2["plan"]["plan_id"] == pid

    # 5. report a broken node -> Planner gate trims + re-plans
    broken = plan["targets"][-1]["node_id"]
    code, r = _post(f"/api/users/{USER}/plans/{pid}/node-failed", {"user_id": USER, "node_id": broken})
    assert code == 200, (code, r)
    assert r["recovered"] is True and r["trimmed"]
    assert r["plan"]["plan_id"] != pid
    assert not (set(r["trimmed"]) & set(r["plan"]["node_path"]))


if __name__ == "__main__":
    print(f"target: {BASE}  user: {USER}")
    t = time.time()
    test_production_endpoints()
    print(f"PASS test_production_endpoints  ({time.time() - t:.0f}s)")
