import { listAgents, listInbox, listRuns } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';

/*
 * The live numbers of the frame: the Cần bạn badge, the "N agent đang chạy" pill and the
 * counts of the Agents rail. The spec wants them pushed by events (hitl.*, run.*); until the
 * event stream reaches the browser they are polled, through TanStack Query: paused while the tab
 * is hidden, one request shared with any screen reading the same list. undefined = unknown: the
 * badge or pill is hidden rather than showing a made-up 0.
 */
const POLL_MS = 30_000;

interface ShellCounts {
  needsYou?: number;
  runningAgents?: number;
  agents?: number;
}

/** `scope` is the open Data Space: the counts are its own, so another space's are dropped and
 *  read again at once rather than shown until the next poll. */
export function useShellCounts(enabled = true, scope = ''): ShellCounts {
  const poll = { refetchInterval: POLL_MS, keepPrevious: false };
  const inbox = useV1(['shellCounts', 'inbox', scope], enabled ? () => listInbox() : null, poll);
  const running = useV1(
    ['shellCounts', 'running', scope],
    enabled ? () => listRuns({ status: 'running' }) : null,
    poll,
  );
  const agents = useV1(['shellCounts', 'agents', scope], enabled ? () => listAgents() : null, poll);
  if (!enabled) return {};
  return {
    needsYou: inbox.error ? undefined : inbox.data?.items.length,
    runningAgents:
      running.error || !running.data
        ? undefined
        : new Set(running.data.items.map((r) => r.agent_id)).size,
    agents: agents.error ? undefined : agents.data?.items.length,
  };
}
