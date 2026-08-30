"""Pretty-print an orchestrator SSE stream (stdin) — used by e2e.sh smoke."""
import json
import sys

for line in sys.stdin:
    line = line.rstrip("\n")
    if not line.startswith("data: "):
        continue
    raw = line[6:]
    if raw == "[DONE]":
        print("\n-- [DONE]")
        continue
    try:
        v = json.loads(raw)
    except Exception:
        print("?", raw)
        continue
    if isinstance(v, str):
        sys.stdout.write(v)
        sys.stdout.flush()
        continue
    k = next(iter(v))
    body = v[k]
    if k == "__evidence":
        runs = body.get("runs", [])
        asks = body.get("clarifications", [])
        print("\n-- __evidence: %d run(s), %d clarification(s)" % (len(runs), len(asks)))
        for r in runs:
            metric = (r.get("metric") or {}).get("metric")
            print("   %s | %s | %s rows | metric=%s | freshness=%s" % (
                r.get("generated_by"), r.get("db_name"), r.get("row_count"), metric,
                (r.get("provenance") or {}).get("status")))
            print("   SQL: " + " ".join(str(r.get("sql")).split()))
            print("   rows: " + json.dumps(r.get("rows", [])[:4], ensure_ascii=False))
        for a in asks:
            print("   ask[%s]: %s options=%s" % (a.get("reason"), a.get("text"), a.get("options")))
    else:
        print("\n-- %s: %s" % (k, json.dumps(body, ensure_ascii=False)[:300]))
