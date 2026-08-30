// Agent end-to-end load test — k6. Drives ONE full agent turn per iteration
// through the orchestrator SSE entry point (planner → responder → judge → …),
// so latency = the WHOLE turn (multiple LLM calls, the dominant cost).
//
// The turn is synchronous over a streaming HTTP connection: k6's http.post reads
// the entire SSE body (all `data: …` frames) before returning, so
// res.timings.duration IS the end-to-end turn latency. Done = body contains `[DONE]`.
//
// Agent turns are LONG (several LLM calls); on one GPU the LLM is the ceiling, so
// model MODEST concurrency (VUS) — this is a chat agent, not a 10k-CCU search tier.
//
//   VUS=5 DURATION=2m THINK=5 k6 run tests/loadtest/agent-e2e.js
//   AGENT_ID=... TENANT=... BASE_URL=http://localhost:8001 VUS=10 k6 run tests/loadtest/agent-e2e.js
//
// Env: BASE_URL (workflow_runtime, which serves /api/orchestrate; default :8001), VUS, DURATION, THINK (s between a
//      user's turns), AGENT_ID (single-agent → predictable path), TENANT, MULTI
//      (set 1 to OMIT agent_id → multi-agent ToT planner, much heavier).

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

const BASE    = __ENV.BASE_URL || 'http://localhost:8001';
const TENANT  = __ENV.TENANT   || 'a0000000-0000-0000-0000-000000000001';
const AGENT   = __ENV.AGENT_ID || '00000000-0000-0000-0000-000000000030';
const VUS     = parseInt(__ENV.VUS || '5');
const HOLD    = __ENV.DURATION || '2m';
const THINK   = parseInt(__ENV.THINK || '5');
const MULTI   = __ENV.MULTI === '1';   // omit agent_id → multi-agent (ToT) path

const turnMs   = new Trend('agent_turn_ms', true);   // full end-to-end turn latency
const ttfbMs   = new Trend('agent_ttfb_ms', true);   // time to first byte (first token)
const errors   = new Rate('agent_errors');
const incomplete = new Counter('agent_incomplete');  // no [DONE] terminator
const answerBytes = new Trend('agent_answer_bytes');

const QUERIES = [
  'Tóm tắt các quy định chính về bảo vệ dữ liệu cá nhân',
  'Trách nhiệm của tổ chức khi xử lý dữ liệu là gì',
  'Quyền của chủ thể dữ liệu gồm những gì',
  'Điều kiện chuyển dữ liệu ra nước ngoài',
  'Khi nào phải thông báo vi phạm dữ liệu',
];

const headers = {
  'Content-Type': 'application/json',
  'X-Tenant-Id': TENANT,
  'X-User-ID': '11111111-1111-1111-1111-111111111111',
  'X-User-Roles': 'platform-admin,tenant-admin,engineer',
  'Accept': 'text/event-stream',
};

export const options = {
  scenarios: {
    agent: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '20s', target: VUS },
        { duration: HOLD,  target: VUS },
        { duration: '10s', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    agent_errors: ['rate<0.05'],
    agent_turn_ms: ['p(95)<60000'],   // a turn should finish inside 60s
  },
};

export default function () {
  const q = QUERIES[randomIntBetween(0, QUERIES.length - 1)];
  const body = { query: q, user_id: `k6-${__VU}` };
  if (!MULTI) body.agent_id = AGENT;      // single-agent = predictable one-agent path

  const res = http.post(`${BASE}/api/orchestrate/stream`, JSON.stringify(body), {
    headers,
    timeout: '120s',
  });

  turnMs.add(res.timings.duration);
  if (res.timings.waiting) ttfbMs.add(res.timings.waiting);
  answerBytes.add((res.body || '').length);

  const done = typeof res.body === 'string' && res.body.includes('[DONE]');
  const errored = typeof res.body === 'string' && (res.body.includes('[ERROR]') || res.body.includes('__error'));
  const ok = check(res, {
    'status 200': (r) => r.status === 200,
    'stream terminated ([DONE])': () => done,
    'no [ERROR] frame': () => !errored,
  });
  if (!done) incomplete.add(1);
  errors.add(!ok);

  sleep(randomIntBetween(THINK * 0.5, THINK * 1.5));   // jittered think-time
}
