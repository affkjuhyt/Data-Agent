import { useEffect, useState } from 'react';
import { listAgents, listInbox, listRuns } from '../../lib/v1/api';

/*
 * The live numbers of the frame: the Cần bạn badge, the "N agent đang chạy" pill and the
 * counts of the Agents rail. The spec wants them pushed by events (hitl.*, run.*); until the
 * event stream reaches the browser they are polled. undefined = unknown: the badge or pill is
 * hidden rather than showing a made-up 0.
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
  const [counts, setCounts] = useState<ShellCounts>({});
  useEffect(() => {
    let cancelled = false;
    setCounts({});
    const load = async () => {
      const [inbox, running, agents] = await Promise.allSettled([
        listInbox(),
        listRuns({ status: 'running' }),
        listAgents(),
      ]);
      if (cancelled) return;
      setCounts({
        needsYou: inbox.status === 'fulfilled' ? inbox.value.items.length : undefined,
        runningAgents:
          running.status === 'fulfilled'
            ? new Set(running.value.items.map((r) => r.agent_id)).size
            : undefined,
        agents: agents.status === 'fulfilled' ? agents.value.items.length : undefined,
      });
    };
    if (enabled) void load();
    else setCounts({});
    if (!enabled) return () => {
      cancelled = true;
    };
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [enabled, scope]);
  return counts;
}
