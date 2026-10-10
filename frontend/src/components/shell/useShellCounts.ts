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

export function useShellCounts(): ShellCounts {
  const [counts, setCounts] = useState<ShellCounts>({});
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const [inbox, running, agents] = await Promise.allSettled([
        listInbox({ status: 'open' }),
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
    void load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);
  return counts;
}
