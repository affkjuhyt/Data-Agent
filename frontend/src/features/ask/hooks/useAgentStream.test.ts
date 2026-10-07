import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { openOrchestrateStream } from '../api';
import { useAgentStream } from './useAgentStream';

vi.mock('../api', () => ({ openOrchestrateStream: vi.fn(), openResumeStream: vi.fn() }));

const enc = new TextEncoder();

/** A response whose body sends `frames`, then ends — or breaks, like a dropped connection. */
const sse = (frames: string[], end: 'close' | 'drop' = 'close') =>
  new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        for (const f of frames) c.enqueue(enc.encode(`data: ${f}\n\n`));
        if (end === 'close') c.close();
        else setTimeout(() => c.error(new TypeError('network error')), 0);
      },
    }),
  );

beforeEach(() => vi.mocked(openOrchestrateStream).mockReset());

describe('useAgentStream', () => {
  it('folds old and agent-run frames into the assistant message (AC-5)', async () => {
    vi.mocked(openOrchestrateStream).mockResolvedValue(
      sse([
        '{"__session_id":"s1"}',
        '{"__run":{"run_id":"r1","status":"started"}}',
        '"Giá Brent "',
        '{"__unknown_frame":{"x":1}}',
        '{"__block":{"block":"ValueCard","props":{"value":"82.1"}}}',
        '"tăng"',
        '{"__suggestions":["Vì sao?"]}',
        '{"__run":{"run_id":"r1","status":"finished"}}',
        '[DONE]',
      ]),
    );
    const { result } = renderHook(() => useAgentStream({ userId: 'u1' }));

    await act(async () => {
      await result.current.send('Giá Brent hôm nay?');
    });

    await waitFor(() => expect(result.current.sending).toBe(false));
    const [question, answer] = result.current.messages;
    expect(question).toMatchObject({ role: 'user', content: 'Giá Brent hôm nay?' });
    expect(answer).toMatchObject({
      role: 'assistant',
      content: 'Giá Brent tăng',
      status: 'done',
      sessionId: 's1',
      suggestions: ['Vì sao?'],
      run: { run_id: 'r1', status: 'finished' },
      blocks: [{ block: 'ValueCard', props: { value: '82.1' } }],
    });
  });

  it('a connection dropped mid-answer keeps what arrived and ends in error', async () => {
    vi.mocked(openOrchestrateStream).mockResolvedValue(sse(['"Giá Brent "'], 'drop'));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { result } = renderHook(() => useAgentStream({ userId: 'u1' }));

    await act(async () => {
      await result.current.send('Hỏi');
    });

    expect(result.current.messages[1]).toMatchObject({ status: 'error', content: 'Giá Brent ' });
    warn.mockRestore();
  });
});
