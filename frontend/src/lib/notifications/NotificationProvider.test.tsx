import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { NotificationBell, RECONNECTING_MESSAGE } from '../../components/shared/NotificationBell';
import { fakeEventStream } from '../../test-utils/fakeEventStream';
import { queryKeys } from '../api/queryKeys';
import { NotificationProvider, toNotification } from './NotificationProvider';

const CASE_ID = '2e3f4a5b-6c7d-4e8f-9a0b-1c2d3e4f5a07';

/** One inbox event in the provisional shape: event = topic, id = event_id, data = EventEnvelope. */
const inboxEvent = (eventId: string, topic: string, payload: Record<string, unknown> = {}) => {
  const envelope = {
    envelope_version: 1,
    event_id: eventId,
    event_type: topic,
    idempotency_key: eventId,
    occurred_at: '2026-10-07T09:12:00Z',
    partition_key: CASE_ID,
    payload,
    producer: 'governance',
    schema_version: 1,
    tenant_id: 'a0000000-0000-0000-0000-000000000001',
    trace_id: null,
  };
  return `id: ${eventId}\nevent: ${topic}\ndata: ${JSON.stringify(envelope)}\n\n`;
};

/** Stands in for the Review Inbox: the list of cases assigned to this user. */
function ReviewList({ fetchCases }: { fetchCases: () => Promise<string[]> }) {
  const { data } = useQuery({ queryKey: queryKeys.reviewCases.all, queryFn: fetchCases });
  return (
    <ul>
      {data?.map((id) => (
        <li key={id}>{id}</li>
      ))}
    </ul>
  );
}

function setup() {
  const server = fakeEventStream();
  const assigned: string[] = [];
  const fetchCases = vi.fn(async () => [...assigned]);
  const fetchAlerts = vi.fn(async () => []);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  function Alerts() {
    useQuery({ queryKey: queryKeys.alerts.all, queryFn: fetchAlerts });
    return null;
  }
  const reload = vi.fn();
  window.addEventListener('beforeunload', reload);
  render(
    <QueryClientProvider client={client}>
      <NotificationProvider
        url="http://inbox.test/notifications/stream"
        stream={{ fetchImpl: server.fetchImpl, sleep: () => Promise.resolve() }}
      >
        <NotificationBell />
        <ReviewList fetchCases={fetchCases} />
        <Alerts />
      </NotificationProvider>
    </QueryClientProvider>,
  );
  return { server, assigned, fetchCases, fetchAlerts, reload };
}

describe('NotificationProvider', () => {
  it('a new case assigned to the researcher appears without a reload (AC-3)', async () => {
    const { server, assigned, fetchCases, fetchAlerts, reload } = setup();
    await waitFor(() => expect(server.connections).toHaveLength(1));
    await waitFor(() => expect(fetchCases).toHaveBeenCalledTimes(1));

    assigned.push(CASE_ID);
    server.connections[0].send(inboxEvent('e1', 'review.case.created', { case_id: CASE_ID }));

    // waitFor's default budget is 1 s, well inside the 5 s of AC-3: no timer, no reload.
    expect(await screen.findByText(CASE_ID)).toBeInTheDocument();
    expect(fetchCases).toHaveBeenCalledTimes(2);
    expect(fetchAlerts).toHaveBeenCalledTimes(1);
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Thông báo: 1 chưa đọc' })).toBeInTheDocument();
  });

  it('refreshes only what the event is about, and ignores other topics', async () => {
    const { server, fetchCases, fetchAlerts } = setup();
    await waitFor(() => expect(fetchCases).toHaveBeenCalledTimes(1));
    server.connections[0].send(inboxEvent('a1', 'alert.raised'));
    await waitFor(() => expect(fetchAlerts).toHaveBeenCalledTimes(2));
    server.connections[0].send(inboxEvent('x1', 'artifact.stored'));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Thông báo: 1 chưa đọc' })).toBeInTheDocument(),
    );
    expect(fetchCases).toHaveBeenCalledTimes(1);
  });

  it('after a drop: says so, reconnects from the last event, refreshes, and counts a replay once', async () => {
    const { server, fetchCases } = setup();
    await waitFor(() => expect(fetchCases).toHaveBeenCalledTimes(1));
    server.connections[0].send(inboxEvent('e1', 'review.case.created'));
    await waitFor(() => expect(fetchCases).toHaveBeenCalledTimes(2));

    server.connections[0].drop();
    await waitFor(() => expect(server.connections).toHaveLength(2));
    expect(server.connections[1].headers['Last-Event-ID']).toBe('e1');
    // Whatever was missed while down is fetched again even without a replay.
    await waitFor(() => expect(fetchCases).toHaveBeenCalledTimes(3));

    // The server replays e1 and sends what was missed.
    server.connections[1].send(
      inboxEvent('e1', 'review.case.created') + inboxEvent('e2', 'review.case.created'),
    );
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Thông báo: 2 chưa đọc' })).toBeInTheDocument(),
    );
  });

  it('shows the reconnecting state while the connection is down', async () => {
    const server = fakeEventStream();
    // The retry never fires: the tab stays disconnected.
    const sleep = () => new Promise<void>(() => {});
    render(
      <QueryClientProvider client={new QueryClient()}>
        <NotificationProvider
          url="http://inbox.test/s"
          stream={{ fetchImpl: server.fetchImpl, sleep }}
        >
          <NotificationBell />
        </NotificationProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(server.connections).toHaveLength(1));
    server.connections[0].drop();
    expect(await screen.findByRole('status')).toHaveTextContent(RECONNECTING_MESSAGE);
  });

  it('marks everything read on click', async () => {
    const { server } = setup();
    await waitFor(() => expect(server.connections).toHaveLength(1));
    server.connections[0].send(inboxEvent('e1', 'review.case.created'));
    fireEvent.click(await screen.findByRole('button', { name: 'Thông báo: 1 chưa đọc' }));
    expect(screen.getByRole('button', { name: 'Thông báo' })).toBeInTheDocument();
  });

  it('is off, bell hidden, without an endpoint or when the server has none', async () => {
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <NotificationProvider url="">
          <NotificationBell />
        </NotificationProvider>
      </QueryClientProvider>,
    );
    expect(container).toBeEmptyDOMElement();

    const server = fakeEventStream([404]);
    const second = render(
      <QueryClientProvider client={new QueryClient()}>
        <NotificationProvider url="http://inbox.test/s" stream={{ fetchImpl: server.fetchImpl }}>
          <NotificationBell />
        </NotificationProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(second.container).toBeEmptyDOMElement());
    expect(server.calls).toHaveLength(1);
  });
});

describe('toNotification', () => {
  it('takes the id from the envelope when the server sent no id field', () => {
    const [, , dataLine] = inboxEvent('e9', 'review.approved').split('\n');
    expect(toNotification({ event: 'review.approved', data: dataLine.slice(6) })).toEqual({
      id: 'e9',
      topic: 'review.approved',
    });
    expect(toNotification({ event: 'message', data: '{}' })).toBeNull();
  });
});
