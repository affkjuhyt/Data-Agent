export type ChatCommandName = 'chart' | 'sql' | 'data' | 'report' | 'status' | 'help' | 'new';

export interface ChatCommand {
  name: ChatCommandName;
  label: string;
  description: string;
  argumentHint?: string;
}

export const CHAT_COMMANDS: readonly ChatCommand[] = [
  { name: 'chart', label: '/chart', description: 'Ưu tiên tạo biểu đồ cho câu hỏi', argumentHint: 'Mô tả biểu đồ hoặc phân tích cần xem…' },
  { name: 'sql', label: '/sql', description: 'Hiển thị SQL và kết quả đã chạy', argumentHint: 'Bạn muốn kiểm tra câu SQL nào…' },
  { name: 'data', label: '/data', description: 'Khám phá bảng và dữ liệu trong Data Space', argumentHint: 'Bạn muốn khám phá dữ liệu nào…' },
  { name: 'report', label: '/report', description: 'Chuẩn bị nội dung báo cáo từ câu hỏi', argumentHint: 'Báo cáo cần phân tích nội dung gì…' },
  { name: 'status', label: '/status', description: 'Xem trạng thái turn và queue hiện tại' },
  { name: 'help', label: '/help', description: 'Xem các lệnh chat có thể dùng' },
  { name: 'new', label: '/new', description: 'Bắt đầu hội thoại mới' },
];

export interface ParsedChatInput {
  command: ChatCommand | null;
  argument: string;
}

export function parseChatInput(input: string): ParsedChatInput {
  const trimmed = input.trim();
  if (!trimmed.startsWith('/')) return { command: null, argument: trimmed };
  const match = /^\/([a-z]+)(?:\s+([\s\S]*))?$/i.exec(trimmed);
  if (!match) return { command: null, argument: trimmed };
  const command = CHAT_COMMANDS.find((item) => item.name === match[1].toLowerCase()) ?? null;
  return { command, argument: match[2]?.trim() ?? '' };
}

export function commandPrompt(command: ChatCommandName, argument: string): string {
  const subject = argument.trim();
  const prompts: Record<Exclude<ChatCommandName, 'status' | 'help' | 'new'>, string> = {
    chart: `Phân tích dữ liệu và ưu tiên tạo biểu đồ phù hợp cho: ${subject}`,
    sql: `Phân tích câu hỏi sau, đồng thời hiển thị SQL đã chạy và giải thích kết quả: ${subject}`,
    data: `Khám phá dữ liệu trong Data Space cho yêu cầu sau, gồm các chiều và kết quả phù hợp: ${subject}`,
    report: `Chuẩn bị nội dung báo cáo phân tích cho yêu cầu sau, gồm kết quả, biểu đồ và insight: ${subject}`,
  };
  return prompts[command as Exclude<ChatCommandName, 'status' | 'help' | 'new'>] ?? subject;
}

export function commandHelp(): string {
  return CHAT_COMMANDS
    .map((command) => `${command.label} — ${command.description}`)
    .join('\n');
}
