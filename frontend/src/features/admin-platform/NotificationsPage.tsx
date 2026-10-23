import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import {
  getNotificationPreferences,
  saveNotificationPreferences,
  sendTestNotification,
  type NotificationChannel,
  type NotificationPreferences,
} from '../../lib/v1/adminPlatform';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P08 · Cài đặt · Thông báo" (2272:16341, UC-08-07): which events reach the user on which
 * channel. A locked cell (high-risk work always reaches the app) is drawn disabled and stays on.
 * Choices apply on "Lưu"; "Gửi thử" sends a sample to every channel that is on.
 */
const CHANNELS: { key: NotificationChannel; label: string }[] = [
  { key: 'in_app', label: 'Trong ứng dụng' },
  { key: 'teams', label: 'Teams' },
  { key: 'zalo', label: 'Zalo' },
  { key: 'email', label: 'Email' },
  { key: 'digest', label: 'Bản tin ngày' },
];

type NotificationEvent = NotificationPreferences['events'][number];

const channelOn = (event: NotificationEvent, key: NotificationChannel) =>
  event.locked.includes(key) || event.channels[key];

function EventsTable({
  events,
  onToggle,
}: {
  events: NotificationEvent[];
  onToggle: (eventKey: string, channel: NotificationChannel, on: boolean) => void;
}) {
  const view = useTableView(events, {
    search: (event) => `${event.label} ${event.description}`,
    filters: [
      {
        id: 'in_app',
        label: 'Trong ứng dụng',
        value: (event) => (channelOn(event, 'in_app') ? 'Bật' : 'Tắt'),
      },
    ],
    sorts: [
      { id: 'event', value: (event) => event.label },
      ...CHANNELS.map((c) => ({
        id: c.key,
        value: (event: NotificationEvent) => (channelOn(event, c.key) ? 1 : 0),
      })),
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-lg">
      <TableToolbar view={view} placeholder="Tìm loại sự kiện…" searchLabel="Tìm loại sự kiện" />
      <div className="overflow-hidden rounded-md border border-border">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-border bg-background text-muted-foreground da-caption">
              <SortableTh
                view={view}
                column="event"
                className="py-[10px] pl-da-xl text-left font-normal"
              >
                Loại sự kiện
              </SortableTh>
              {CHANNELS.map((c) => (
                <SortableTh
                  key={c.key}
                  view={view}
                  column={c.key}
                  className="w-[120px] py-[10px] text-center font-normal"
                >
                  {c.label}
                </SortableTh>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.rows.map((event) => (
              <tr key={event.key} className="border-b border-border last:border-b-0">
                <td className="py-[10px] pl-da-xl">
                  <div className="flex flex-col gap-[2px]">
                    <span className="text-foreground da-body-strong">{event.label}</span>
                    <span className="text-muted-foreground da-caption">{event.description}</span>
                  </div>
                </td>
                {CHANNELS.map((c) => {
                  const locked = event.locked.includes(c.key);
                  return (
                    <td key={c.key} className="text-center">
                      <Checkbox
                        checked={locked || event.channels[c.key]}
                        disabled={locked}
                        onChange={(on) => onToggle(event.key, c.key, on)}
                      >
                        <span className="sr-only">
                          {event.label} · {c.label}
                          {locked ? ' (luôn bật)' : ''}
                        </span>
                      </Checkbox>
                    </td>
                  );
                })}
              </tr>
            ))}
            {view.total === 0 && tableEmptyRow(1 + CHANNELS.length)}
          </tbody>
        </table>
      </div>
      <TablePager view={view} />
    </div>
  );
}

export default function NotificationsPage() {
  const toast = useToast();
  const { navigate } = useAppState();
  const saved = useV1(['getNotificationPreferences'], getNotificationPreferences);
  const [draft, setDraft] = useState<NotificationPreferences>();
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (saved.data) setDraft(saved.data);
  }, [saved.data]);

  const dirty = Boolean(
    draft && saved.data && JSON.stringify(draft) !== JSON.stringify(saved.data),
  );

  const toggle = (eventKey: string, channel: NotificationChannel, on: boolean) =>
    setDraft(
      (current) =>
        current && {
          ...current,
          events: current.events.map((event) =>
            event.key === eventKey
              ? { ...event, channels: { ...event.channels, [channel]: on } }
              : event,
          ),
        },
    );

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await saveNotificationPreferences(draft);
      toast({ tone: 'success', message: 'Đã lưu cách nhận thông báo.' });
      saved.reload();
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    try {
      const result = await sendTestNotification();
      const names = CHANNELS.filter((c) => result.channels.includes(c.key)).map((c) => c.label);
      toast({ tone: 'info', message: `Đã gửi thông báo thử tới ${names.join(', ')}.` });
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Cài đặt', 'Thông báo']}
        title="Thông báo"
        description="Chọn bạn nhận gì, qua kênh nào. Việc rủi ro cao luôn được báo trong ứng dụng, không tắt được."
        actions={
          <>
            <Button kind="ghost" onClick={test} loading={testing}>
              Gửi thử
            </Button>
            <Button kind="primary" onClick={save} loading={saving} disabled={!dirty}>
              Lưu
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-2xl px-da-3xl pt-[20px] pb-da-3xl">
        <AsyncRegion state={saved} what="cài đặt thông báo" skeletonRows={6}>
          {() =>
            draft && (
              <>
                <EventsTable events={draft.events} onToggle={toggle} />
                <div className="flex gap-da-2xl max-[1023px]:flex-col">
                  <section className="flex min-w-0 flex-1 flex-col gap-da-md">
                    <h2 className="text-foreground da-heading-3">Giờ yên lặng</h2>
                    <p className="text-foreground da-body-small">{draft.quiet_hours_text}</p>
                  </section>
                  <section className="flex min-w-0 flex-1 flex-col gap-da-md">
                    <h2 className="text-foreground da-heading-3">Gộp thông báo</h2>
                    <p className="text-foreground da-body-small">{draft.grouping_text}</p>
                  </section>
                </div>
                <p className="text-muted-foreground da-caption">
                  Ô khoá: việc rủi ro cao luôn báo trong ứng dụng (
                  <button
                    type="button"
                    onClick={() => navigate('account', 'CHANNEL_RULES')}
                    className="text-primary hover:underline"
                  >
                    quy tắc kênh
                  </button>
                  ).
                </p>
              </>
            )
          }
        </AsyncRegion>
      </div>
    </div>
  );
}
