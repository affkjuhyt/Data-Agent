import { AsyncRegion } from '../../design-system/AsyncRegion';
import { getDashboard, listDashboards } from '../../lib/v1/dashboards';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "Dashboard của bạn" of "v3.1 · Trang chủ" (2132:15338): the viewer's dashboards, each with the
 * first line the agent wrote about it this period. The list carries no summary, so the first three
 * dashboards are read for theirs.
 */
const SHOWN = 3;

export function YourDashboards({ onOpen }: { onOpen: (id: string) => void }) {
  const items = useV1(async () => {
    const page = await listDashboards();
    return Promise.all(page.items.slice(0, SHOWN).map((d) => getDashboard(d.id)));
  }, []);
  return (
    <section
      aria-labelledby="your-dashboards"
      className="flex flex-col gap-da-md rounded-lg border border-border bg-card p-da-xl"
    >
      <h2 id="your-dashboards" className="text-foreground da-heading-3">
        Dashboard của bạn
      </h2>
      <AsyncRegion state={items} what="dashboard">
        {(dashboards) =>
          dashboards.length === 0 ? (
            <p className="text-muted-foreground da-body-small">Bạn chưa có dashboard nào.</p>
          ) : (
            <ul className="flex flex-col gap-da-md">
              {dashboards.map((d) => (
                <li key={d.id} className="flex flex-col gap-[2px]">
                  <button
                    type="button"
                    onClick={() => onOpen(d.id)}
                    className="self-start text-left text-primary hover:underline da-body-small"
                  >
                    {d.title}
                  </button>
                  {d.summary?.lines[0] && (
                    <span className="text-muted-foreground da-caption">
                      AI: {d.summary.lines[0]}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )
        }
      </AsyncRegion>
    </section>
  );
}
