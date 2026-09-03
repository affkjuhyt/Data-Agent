// E2E seed — the agent's canvas (flow nodes + edges) in Mongo. Mirrors what
// flow_builder writes when a person drags nodes; workflow_runtime reads it in
// config_loader.load_agent_config → graph.build_graph.
//
//   docker exec -i dataagent-mongo mongosh --quiet dataagent < tests/e2e/seed-flow.js
//
// Canvas:  planner → db_connector → rrf_ranking → reranker → { visualization, responder → judge }
// The db_connector node carries the demo warehouse connection; its password is
// NOT here — only secretId, resolved through secrets_vault + OpenBao.
const WV = "0000000e-2e00-4000-8000-000000000004";

const dbConnections = JSON.stringify([
  {
    name: "dw",
    type: "postgres",
    host: "postgres",
    port: 5432,
    database: "sandbox_dev",
    user: "sandbox_reader",
    secretId: "0000000e-2e00-4000-8000-000000000005",
    note: "Kho demo E2E: fact_sales(region, product, amount, returned_amount, status, order_date)",
  },
]);

const nodes = [
  { id: "n_planner",   type: "planner",       data: { nodeType: "planner" } },
  { id: "n_db",        type: "db_connector",  data: { nodeType: "db_connector", dbConnections } },
  { id: "n_rrf",       type: "rrf_ranking",   data: { nodeType: "rrf_ranking", top_n: 20 } },
  { id: "n_rerank",    type: "reranker",      data: { nodeType: "reranker", top_n: 5 } },
  { id: "n_viz",       type: "visualization", data: { nodeType: "visualization" } },
  { id: "n_responder", type: "responder",     data: { nodeType: "responder", temperature: 0.1, max_tokens: 700 } },
  // max_iterations 1: a small local model fails the judge often; one retry is
  // enough to exercise the loop without tripling the turn time.
  { id: "n_judge",     type: "judge",         data: { nodeType: "judge", max_iterations: 1 } },
].map(n => ({ ...n, workflow_version_id: WV, position: { x: 0, y: 0 } }));

const edges = [
  ["n_planner", "n_db"],
  ["n_db", "n_rrf"],
  ["n_rrf", "n_rerank"],
  ["n_rerank", "n_viz"],
  ["n_rerank", "n_responder"],
  ["n_responder", "n_judge"],
].map(([source, target], i) => ({ id: `e${i + 1}`, source, target, workflow_version_id: WV }));

db.flow_nodes.deleteMany({ workflow_version_id: WV });
db.flow_edges.deleteMany({ workflow_version_id: WV });
db.flow_nodes.insertMany(nodes);
db.flow_edges.insertMany(edges);

print(`seeded ${db.flow_nodes.countDocuments({ workflow_version_id: WV })} nodes, ${db.flow_edges.countDocuments({ workflow_version_id: WV })} edges for workflow version ${WV}`);
