#!/usr/bin/env bash
# Local dev Kong setup — routing only, no JWT auth required
set -e
KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8900}"

echo "Setting up workflow-runtime..."
curl -sf -X PUT "$KONG_ADMIN/services/workflow-runtime" \
  -H "Content-Type: application/json" \
  -d '{"name":"workflow-runtime","url":"http://workflow-runtime:8001"}'

curl -sf -X PUT "$KONG_ADMIN/services/workflow-runtime/routes/runtime-route" \
  -H "Content-Type: application/json" \
  -d '{"name":"runtime-route","paths":["/api/conversations","/api/traces","/api/nodes","/api/llm-providers","/api/kb-connections","/api/system-prompts"],"strip_path":false}'

echo ""
echo "Setting up flow-builder..."
curl -sf -X PUT "$KONG_ADMIN/services/flow-builder" \
  -H "Content-Type: application/json" \
  -d '{"name":"flow-builder","url":"http://flow-builder:8002"}'

curl -sf -X PUT "$KONG_ADMIN/services/flow-builder/routes/builder-route" \
  -H "Content-Type: application/json" \
  -d '{"name":"builder-route","paths":["/api/agents","/api/workflows","/api/workflow-versions"],"strip_path":false}'

echo ""
echo "Done. Test: curl http://localhost:8000/api/agents"
