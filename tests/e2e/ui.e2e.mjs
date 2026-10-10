// UI end-to-end — real Keycloak login, real gateway, real agent turn.
//
//   npm run e2e:ui               (or: node tests/e2e/ui.e2e.mjs)
//   E2E_KONG=… to target another gateway (default: the dev cluster's Kong NodePort)
//   E2E_STRICT=1 … to make the evidence assertions hard failures
//   E2E_HEADED=1 … to watch it
//
// Starts vite on :3000 with VITE_API_URL pointed at Kong if nothing is listening,
// logs in through the Keycloak form as E2E_USER, opens Phân tích › Hỏi đáp, asks the
// canonical metric question and checks what the answer block shows.
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', '..');
const require = createRequire(resolve(ROOT, 'package.json'));
const { chromium } = require('playwright');

const APP = process.env.E2E_APP ?? 'http://localhost:3000';
const KONG = process.env.E2E_KONG ?? 'http://10.10.55.101:30100';
const USER = process.env.E2E_USER ?? 'helios-engineer';
const PASS = process.env.E2E_PASS;
if (!PASS) {
  // Passwords come from the environment only (CLAUDE.md §6; tests/README.md, e2e).
  console.error('E2E_PASS is not set — export the helios-engineer password');
  process.exit(2);
}
const STRICT = process.env.E2E_STRICT === '1';
const HEADED = process.env.E2E_HEADED === '1';
const QUESTION = process.env.E2E_QUESTION ?? 'Doanh thu thuần tháng này theo vùng';
const AGENT_NAME = process.env.E2E_AGENT_NAME ?? 'e2e-sql-agent';
const TURN_TIMEOUT = Number(process.env.E2E_TURN_TIMEOUT ?? 240) * 1000;
const OUT = resolve(HERE, 'out');
mkdirSync(OUT, { recursive: true });

const results = { ok: true, checks: [] };
const check = (name, pass, detail, hard = true) => {
  const status = pass ? 'PASS' : hard || STRICT ? 'FAIL' : 'WARN';
  results.checks.push({ name, status, detail });
  console.log(`${status === 'PASS' ? '✔' : status === 'WARN' ? '⚠' : '✖'} ${name}${detail ? ` — ${detail}` : ''}`);
  if (status === 'FAIL') results.ok = false;
};

async function isUp(url) {
  try { const r = await fetch(url, { signal: AbortSignal.timeout(2000) }); return r.ok || r.status < 500; } catch { return false; }
}

let vite = null;
if (!(await isUp(APP))) {
  console.log(`▶ starting vite at ${APP} with VITE_API_URL=${KONG}`);
  vite = spawn('npx', ['vite', '--port=3000', '--host=0.0.0.0', '--strictPort'], {
    cwd: resolve(ROOT, 'frontend'), env: { ...process.env, VITE_API_URL: KONG }, stdio: 'ignore',
  });
  for (let i = 0; i < 60 && !(await isUp(APP)); i++) await new Promise(r => setTimeout(r, 1000));
  if (!(await isUp(APP))) { console.error('vite did not come up'); process.exit(2); }
}

const browser = await chromium.launch({ headless: !HEADED });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'vi-VN' });
const page = await ctx.newPage();
const frames = [];
page.on('response', async r => {
  if (r.url().includes('/api/orchestrate/stream')) {
    try { frames.push(await r.text()); } catch { /* streamed */ }
  }
});

try {
  // 1. login through Keycloak (login-required → redirect to :8080)
  await page.goto(`${APP}/analysis/ask`);
  await page.waitForURL(/realms\/aeroflow/, { timeout: 30000 });
  await page.fill('#username', USER);
  await page.fill('#password', PASS);
  await page.click('#kc-login');
  await page.waitForURL(u => u.origin === new URL(APP).origin, { timeout: 30000 });
  check('keycloak login redirects back to the app', true);

  // 2. the Ask screen
  await page.goto(`${APP}/analysis/ask`);
  await page.waitForSelector('textarea[aria-label="Câu hỏi"]', { timeout: 30000 });
  check('Hỏi đáp screen renders', true);
  // agents load asynchronously after the screen renders — give the request a moment
  await page.waitForFunction(() => document.querySelectorAll('aside .subnav-item').length > 1, null, { timeout: 15000 }).catch(() => {});
  const agentRows = await page.locator('aside .subnav-item').count();
  check('agent scopes loaded from /api/catalog/agents through Kong', agentRows > 1, `${agentRows - 1} agent(s)`);

  // 3. pick the seeded agent as scope (the "Tự động" default goes through the
  //    multi-agent ToT planner — a different, heavier path than this test covers)
  const scope = page.locator('aside .subnav-item', { hasText: AGENT_NAME }).first();
  if (await scope.count()) { await scope.click(); check(`scope "${AGENT_NAME}" selected`, true); }
  else check(`scope "${AGENT_NAME}" selected`, false, 'not in the agent list', false);

  // 4. ask
  await page.fill('textarea[aria-label="Câu hỏi"]', QUESTION);
  await page.keyboard.press('Enter');
  const t0 = Date.now();
  await page.waitForSelector('[data-testid="answer-block"]', { timeout: 30000 });
  // done = no typing dots and either text or a clarification card
  await page.waitForFunction(() => {
    const block = document.querySelector('[data-testid="answer-block"]');
    if (!block) return false;
    if (block.querySelector('[data-testid="typing"]')) return false;
    return block.textContent.trim().length > 0 || !!block.querySelector('[data-testid="clarification-card"]');
  }, null, { timeout: TURN_TIMEOUT });
  // give the trailing frames (evidence/chart/suggestions) a moment to land
  await page.waitForTimeout(1500);
  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
  await page.screenshot({ path: resolve(OUT, 'ui-answer.png'), fullPage: true });

  const text = (await page.locator('[data-testid="answer-block"]').first().innerText()).trim();
  check(`agent answered in ${elapsed}s`, text.length > 0 && !/không kết nối được backend/i.test(text), text.slice(0, 120).replace(/\n/g, ' '));

  const hasGrid = await page.locator('[data-testid="data-grid"]').count();
  const hasStrip = await page.locator('[data-testid="evidence-strip"]').count();
  const hasCard = await page.locator('[data-testid="clarification-card"]').count();
  const panelText = await page.locator('aside:has-text("Con số này ở đâu ra")').innerText().catch(() => '');
  const hasPanelSql = /SELECT/i.test(panelText);

  check('evidence reached the UI (strip, panel SQL, or a clarification card)', hasStrip + hasCard > 0 || hasPanelSql,
        `strip=${hasStrip} panelSQL=${hasPanelSql} clarification=${hasCard}`);
  check('data table rendered (metric question ran SQL)', hasGrid > 0, hasCard ? 'system asked a clarification instead' : `grid=${hasGrid}`, false);
  check('approved-metric badge shown', /Chỉ số đã duyệt|đã duyệt/.test(panelText + text), '', false);

  if (hasCard) {
    // answer the clarification with the first option and expect a run
    await page.locator('[data-testid="clarification-card"] .ab-option').first().click();
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="answer-block"]').length >= 2, null, { timeout: TURN_TIMEOUT });
    await page.waitForFunction(() => !document.querySelector('[data-testid="typing"]'), null, { timeout: TURN_TIMEOUT });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: resolve(OUT, 'ui-after-clarification.png'), fullPage: true });
    const grid2 = await page.locator('[data-testid="data-grid"]').count();
    check('clicking a clarification option produced a query', grid2 > 0, `grid=${grid2}`, false);
  }
} catch (err) {
  await page.screenshot({ path: resolve(OUT, 'ui-failure.png'), fullPage: true }).catch(() => {});
  check('ui flow completed', false, String(err).split('\n')[0]);
} finally {
  writeFileSync(resolve(OUT, 'ui-report.json'), JSON.stringify({ ...results, question: QUESTION, frames }, null, 2));
  await browser.close();
  if (vite) vite.kill();
}

console.log(`\n${results.ok ? 'UI E2E PASSED' : 'UI E2E FAILED'} — report: ${resolve(OUT, 'ui-report.json')}`);
process.exit(results.ok ? 0 : 1);
