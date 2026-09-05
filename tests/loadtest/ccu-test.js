// KB high-CCU load test — k6.
//
// Measures what the M3 laptop can actually serve concurrently: the USER-FACING
// paths (retrieval + CRUD) with the LLM BYPASSED. It deliberately does NOT push
// load through the local LLM (one GPU = one queue; see docs/M3-CAPACITY.md §4).
//
// Retrieval bypasses the canonicalize-LLM by sending front-layer `entities`, so
// each request exercises embed → Qdrant → rerank → gold-fetch (the real hot path)
// without serializing on ollama.
//
// Run (install: `brew install k6`):
//   k6 run tests/loadtest/ccu-test.js
//   BASE_URL=http://localhost:8220 VUS=50 DURATION=1m TENANT=... KB=... \
//     k6 run tests/loadtest/ccu-test.js
//
// Auth: hits kb-backend directly on :8050 and injects the X-Kong-* identity Kong
// would add (the :8050 port exists for exactly this). To go through the real
// gateway instead, set BASE_URL=http://localhost:8100 and pass a Bearer token.

import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE   = __ENV.BASE_URL || 'http://localhost:8220';
const TENANT = __ENV.TENANT   || 'a0000000-0000-0000-0000-000000000001';
const KB     = __ENV.KB       || '00000000-0000-0003-0000-000000000001';
const VUS    = parseInt(__ENV.VUS || '30');
const DURATION = __ENV.DURATION || '45s';
const TOKEN  = __ENV.TOKEN || ''; // set when BASE_URL is the Kong gateway (:8000)

// Identity headers: a real Bearer via Kong, or simulated Kong-verified headers direct.
const headers = TOKEN
  ? { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' }
  : {
      'X-Kong-Verified': 'true',
      'X-User-Id': '11111111-1111-1111-1111-111111111111',
      'X-User-Roles': 'platform-admin,tenant-admin,kb-admin,engineer',
      'X-Tenant-Id': TENANT,
      'Content-Type': 'application/json',
    };

const retrievalLatency = new Trend('retrieval_latency', true);
const crudLatency = new Trend('crud_latency', true);
const errors = new Rate('errors');

// A few Vietnamese queries + front-layer entities (bypass the LLM canonicalize step).
const QUERIES = [
  { query: 'điều kiện bảo vệ dữ liệu cá nhân', entities: ['dữ liệu cá nhân'] },
  { query: 'nghị định quy định gì', entities: ['nghị định'] },
  { query: 'trách nhiệm của tổ chức', entities: ['tổ chức'] },
  { query: 'quyền của chủ thể dữ liệu', entities: ['chủ thể dữ liệu'] },
];

export const options = {
  scenarios: {
    // 70% of load = retrieval (the expensive interactive path)
    retrieval: {
      executor: 'ramping-vus',
      exec: 'retrieval',
      startVUs: 1,
      stages: [
        { duration: '10s', target: Math.ceil(VUS * 0.7) },
        { duration: DURATION, target: Math.ceil(VUS * 0.7) },
        { duration: '5s', target: 0 },
      ],
    },
    // 30% = cheap CRUD reads (list KBs)
    crud: {
      executor: 'ramping-vus',
      exec: 'crud',
      startVUs: 1,
      stages: [
        { duration: '10s', target: Math.ceil(VUS * 0.3) },
        { duration: DURATION, target: Math.ceil(VUS * 0.3) },
        { duration: '5s', target: 0 },
      ],
    },
  },
  thresholds: {
    // CCU is "healthy" only if the user-facing tier stays snappy under load:
    'retrieval_latency': ['p(95)<1500'],  // 95% of retrievals < 1.5s
    'crud_latency': ['p(95)<300'],        // 95% of CRUD reads  < 300ms
    'errors': ['rate<0.01'],              // < 1% errors
  },
};

export function retrieval() {
  const q = QUERIES[Math.floor(Math.random() * QUERIES.length)];
  const body = JSON.stringify({ query: q.query, knowledge_base: KB, entities: q.entities });
  const res = http.post(`${BASE}/api/pipeline/light-rag/retrieve`, body, { headers, timeout: '20s' });
  retrievalLatency.add(res.timings.duration);
  const ok = check(res, {
    'retrieval 200': (r) => r.status === 200,
    'has results array': (r) => { try { return Array.isArray(r.json('results')); } catch (e) { return false; } },
  });
  errors.add(!ok);
}

export function crud() {
  const res = http.get(`${BASE}/api/knowledge/knowledge-bases`, { headers, timeout: '10s' });
  crudLatency.add(res.timings.duration);
  const ok = check(res, { 'crud 200': (r) => r.status === 200 });
  errors.add(!ok);
}
