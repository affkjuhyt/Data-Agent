import { describe, expect, it } from 'vitest';
import { commandHelp, commandPrompt, parseChatInput } from './chatCommands';

describe('chat commands', () => {
  it('parses a command and preserves its free-form argument', () => {
    expect(parseChatInput('/chart doanh thu theo tháng')).toMatchObject({
      command: { name: 'chart' },
      argument: 'doanh thu theo tháng',
    });
  });

  it('leaves normal questions untouched', () => {
    expect(parseChatInput('Doanh thu theo vùng')).toEqual({
      command: null,
      argument: 'Doanh thu theo vùng',
    });
  });

  it('turns supported modes into explicit agent prompts', () => {
    expect(commandPrompt('sql', 'doanh thu 2026')).toContain('hiển thị SQL đã chạy');
    expect(commandPrompt('report', 'doanh thu 2026')).toContain('nội dung báo cáo');
  });

  it('provides a discoverable help message', () => {
    expect(commandHelp()).toContain('/chart');
    expect(commandHelp()).toContain('/new');
  });
});
