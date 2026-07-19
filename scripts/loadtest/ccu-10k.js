// 10,000-CCU load test — k6. Models 10k concurrent USERS with think-time
// (Little's Law: 10k users × 1 req / 15 s ≈ 670 req/s — see KB-SCALE-10K.md §0),
// NOT 10k simultaneous requests. Drives retrieval + CRUD through the kb-gateway
// (so metrics land in Grafana) with the LLM bypassed (front-layer entities).
//
// A tunable share of queries are UNIQUE (exercise the embed path / cache miss);
// the rest repeat a hot set (hit the result + embed caches) — the realistic
// Zipfian search pattern that makes 10k CCU tractable on one laptop.
//
// Run (install: brew install k6). Start small, then scale:
//   VUS=1000  DURATION=1m THINK=15 k6 run scripts/loadtest/ccu-10k.js
//   VUS=10000 DURATION=3m THINK=15 UNIQUE_PCT=20 k6 run scripts/loadtest/ccu-10k.js
// Watch it live in Grafana: http://localhost:3000 → "KB — High CCU".
//
// Env: BASE_URL (default gateway :8010), VUS, DURATION (hold), THINK (s),
//      UNIQUE_PCT (0-100), TENANT, KB.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';
import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

const BASE   = __ENV.BASE_URL || 'http://localhost:8010';   // kb-gateway
const TENANT = __ENV.TENANT   || 'a0000000-0000-0000-0000-000000000001';
const KB     = __ENV.KB       || '00000000-0000-0003-0000-000000000001';
const VUS    = parseInt(__ENV.VUS || '10000');
const HOLD   = __ENV.DURATION || '3m';
const THINK  = parseInt(__ENV.THINK || '15');       // avg seconds between a user's requests
const UNIQUE = parseInt(__ENV.UNIQUE_PCT || '20');  // % of retrievals that are cold (unique)

const errors = new Rate('errors');
const retrieveL = new Trend('retrieve_ms', true);
const crudL = new Trend('crud_ms', true);

const HOT = [
  { q: 'điều kiện bảo vệ dữ liệu cá nhân', e: ['dữ liệu cá nhân'] },
  { q: 'nghị định quy định về xử lý dữ liệu', e: ['nghị định'] },
  { q: 'trách nhiệm của tổ chức xử lý', e: ['tổ chức'] },
  { q: 'quyền của chủ thể dữ liệu', e: ['chủ thể dữ liệu'] },
  { q: 'sự đồng ý của chủ thể', e: ['đồng ý'] },
];

const headers = {
  'X-Kong-Verified': 'true',
  'X-User-Id': '11111111-1111-1111-1111-111111111111',
  'X-User-Roles': 'platform-admin,tenant-admin,kb-admin,engineer',
  'X-Tenant-Id': TENANT,
  'Content-Type': 'application/json',
};

export const options = {
  scenarios: {
    ccu: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '60s', target: VUS },   // ramp to the full CCU
        { duration: HOLD,  target: VUS },    // hold
        { duration: '20s', target: 0 },      // drain
      ],
      gracefulRampDown: '20s',
    },
  },
  thresholds: {
    errors: ['rate<0.02'],
    retrieve_ms: ['p(95)<3000'],
    crud_ms: ['p(95)<800'],
  },
};

function retrieve() {
  const base = HOT[randomIntBetween(0, HOT.length - 1)];
  // UNIQUE% of retrievals get a unique suffix → cold (real embed); rest hit cache.
  const cold = randomIntBetween(1, 100) <= UNIQUE;
  const q = cold ? `${base.q} ${__VU}-${__ITER}-${Date.now()}` : base.q;
  const body = JSON.stringify({ query: q, knowledge_base: KB, entities: base.e });
  const res = http.post(`${BASE}/api/pipeline/light-rag/retrieve`, body, { headers, timeout: '30s' });
  retrieveL.add(res.timings.duration);
  errors.add(!check(res, { 'retrieve 200': (r) => r.status === 200 }));
}

function crud() {
  const res = http.get(`${BASE}/api/knowledge/knowledge-bases`, { headers, timeout: '15s' });
  crudL.add(res.timings.duration);
  errors.add(!check(res, { 'crud 200': (r) => r.status === 200 }));
}

export default function () {
  // One "user" iteration: 70% search, 30% browse, then think.
  if (randomIntBetween(1, 100) <= 70) retrieve();
  else crud();
  sleep(randomIntBetween(THINK * 0.5, THINK * 1.5)); // jittered think-time
}
