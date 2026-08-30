/**
 * Release API client — calls release_worker via Kong gateway.
 * Kong injects X-User-Id / X-User-Roles from JWT → backend reads those headers.
 */

const API_BASE = '/api/release';

/** Raw shapes returned by the backend */
export interface PipelineRaw {
  id: string;
  pipeline_name: string | null;
  triggered_by: string;
  trigger_type: string;
  commit_sha: string | null;
  branch: string | null;
  package_version: string;
  target_env: string;
  status: string;
  risk_score: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

interface HistoryRaw {
  pipeline_id: string;
  package_id: string | null;
  environment: string;
  status: string;
  triggered_by: string;
  deployed_at: string;
  duration_ms: number | null;
}

interface RollbackTargetRaw {
  pipeline_id: string;
  name: string;
  current_version: string;
  previous_version: string;
  environment: string;
}

interface PipelineStepRaw {
  step_name: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  output: Record<string, unknown> | null;
  error: string | null;
}

/** UI-facing shapes (mapped from Raw) */
export interface DeploymentUI {
  id: string;
  name: string;
  env: string;
  status: string;
  version: string;
  startedAt: string;
  duration: string;
  owner: string;
  riskScore: number;
  branch: string | null;
  commitSha: string | null;
  errorMessage: string | null;
  triggerType: string;
}

export interface HistoryUI {
  id: string;
  name: string;
  env: string;
  status: string;
  by: string;
  at: string;
  duration: string;
  version: string;
}

export interface RollbackTargetUI {
  id: string;
  name: string;
  current: string;
  previous: string;
  env: string;
}

export interface PipelineStepUI {
  stepName: string;
  status: string;
  duration: string;
  error: string | null;
}

export interface TriggerPipelineParams {
  packageVersion: string;
  pipelineName?: string;
  branch?: string;
  targetEnvironments: string[];
}

/* ─── Time helpers ───────────────────────────────────────────────────── */

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60)  return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60)  return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)  return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function formatDuration(ms: number | null): string {
  if (!ms) return '--';
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m === 0) return `${s}s`;
  return `${m}m ${rem}s`;
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString('sv-SE', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }).replace('T', ' ').slice(0, 16);
}

/* ─── Status mapping: backend → UI DEPLOY_STATUS key ────────────────── */
const STATUS_MAP: Record<string, string> = {
  PENDING:           'QUEUED',
  RUNNING:           'VALIDATING',
  BUILDING:          'BUILDING',
  SCANNING:          'VALIDATING',
  AWAITING_APPROVAL: 'WAITING_APPROVAL',
  SUCCESS:           'SUCCESS',
  FAILED:            'FAILED',
  ROLLED_BACK:       'ROLLED_BACK',
};

/* ─── Mappers ────────────────────────────────────────────────────────── */

function mapPipeline(p: PipelineRaw): DeploymentUI {
  const startedMs   = new Date(p.created_at).getTime();
  const completedMs = p.completed_at ? new Date(p.completed_at).getTime() : null;
  const durationMs  = completedMs ? completedMs - startedMs : null;

  return {
    id:           p.id,
    name:         p.pipeline_name ?? p.id,
    env:          (p.target_env ?? 'dev').toUpperCase(),
    status:       STATUS_MAP[p.status] ?? 'QUEUED',
    version:      p.package_version,
    startedAt:    relativeTime(p.created_at),
    duration:     durationMs ? formatDuration(durationMs) : (
                    ['BUILDING', 'SCANNING', 'RUNNING'].includes(p.status) ? 'running…' : '--'
                  ),
    owner:        p.triggered_by.length > 20
                    ? p.triggered_by.slice(0, 8) + '…'
                    : p.triggered_by,
    riskScore:    p.risk_score ?? 0,
    branch:       p.branch,
    commitSha:    p.commit_sha,
    errorMessage: p.error_message,
    triggerType:  p.trigger_type ?? 'MANUAL',
  };
}

function mapHistory(h: HistoryRaw, pipelines: PipelineRaw[]): HistoryUI {
  const pipe = pipelines.find(p => p.id === h.pipeline_id);
  const name = pipe?.pipeline_name ?? h.pipeline_id;
  const version = pipe?.package_version ?? h.package_id ?? '--';
  return {
    id:       h.pipeline_id,
    name:     `${name} ${version}`,
    env:      h.environment.toUpperCase(),
    status:   h.status,
    by:       h.triggered_by.length > 20
                ? h.triggered_by.slice(0, 8) + '…'
                : h.triggered_by,
    at:       formatTimestamp(h.deployed_at),
    duration: formatDuration(h.duration_ms),
    version,
  };
}

function mapRollbackTarget(r: RollbackTargetRaw): RollbackTargetUI {
  return {
    id:       r.pipeline_id,
    name:     r.name,
    current:  r.current_version,
    previous: r.previous_version,
    env:      r.environment.toUpperCase(),
  };
}

/* ─── API fetch helpers ──────────────────────────────────────────────── */

async function apiFetch<T>(path: string, token: string): Promise<T> {
  const res = await fetch(path, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export async function fetchPipelines(token: string): Promise<{
  deployments: DeploymentUI[];
  raw: PipelineRaw[];
}> {
  const data = await apiFetch<{ pipelines: PipelineRaw[] }>(
    `${API_BASE}/pipelines?limit=30`,
    token,
  );
  return {
    deployments: data.pipelines.map(mapPipeline),
    raw:         data.pipelines,
  };
}

export async function fetchHistory(token: string, pipelines: PipelineRaw[]): Promise<HistoryUI[]> {
  const data = await apiFetch<{ history: HistoryRaw[] }>(
    `${API_BASE}/history?limit=30`,
    token,
  );
  return data.history.map(h => mapHistory(h, pipelines));
}

export async function fetchRollbackTargets(token: string): Promise<RollbackTargetUI[]> {
  const data = await apiFetch<{ targets: RollbackTargetRaw[] }>(
    `${API_BASE}/rollback-targets`,
    token,
  );
  return data.targets.map(mapRollbackTarget);
}

export async function fetchPipelineDetail(
  token: string,
  pipelineId: string,
): Promise<PipelineStepUI[]> {
  const data = await apiFetch<{ pipeline: PipelineRaw; steps: PipelineStepRaw[] }>(
    `${API_BASE}/pipelines/${pipelineId}`,
    token,
  );
  return (data.steps ?? []).map(s => ({
    stepName: s.step_name,
    status:   s.status,
    duration: formatDuration(s.duration_ms),
    error:    s.error,
  }));
}

export async function triggerPipeline(
  token: string,
  params: TriggerPipelineParams,
): Promise<{ pipeline_id: string; status: string }> {
  const res = await fetch(`${API_BASE}/pipeline/trigger`, {
    method:  'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body:    JSON.stringify({
      package_version:     params.packageVersion,
      pipeline_name:       params.pipelineName || undefined,
      branch:              params.branch || undefined,
      target_environments: params.targetEnvironments,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({})) as { detail?: string };
    throw new Error(err.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<{ pipeline_id: string; status: string }>;
}

export async function triggerRollback(
  token: string,
  fromVersion: string,
  toVersion: string,
  environment: string,
  reason?: string,
): Promise<{ rollback_id: string }> {
  const res = await fetch(`${API_BASE}/rollback`, {
    method:  'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body:    JSON.stringify({ from_version: fromVersion, to_version: toVersion, environment, reason }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({})) as { detail?: string };
    throw new Error(err.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function approveDeployment(
  token: string,
  pipelineId: string,
  environment: string,
  decision: 'APPROVED' | 'REJECTED',
  comment?: string,
): Promise<void> {
  const res = await fetch(`${API_BASE}/pipeline/${pipelineId}/approve/${environment}`, {
    method:  'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body:    JSON.stringify({ decision, comment }),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
}
