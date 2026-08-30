/**
 * CLI cheat-sheet — documentation for calling the platform from a terminal.
 * Every command here targets a real gateway endpoint; the API key comes from
 * Settings › API (useAPIKeys).
 */
import React, { useState } from 'react';
import { Terminal, Copy, Check, KeyRound, Code } from 'lucide-react';
import { useAppState } from '../../../AppStateContext';
import { API_URL } from '../../../lib/config';
import { Button, Card, SectionTitle } from '../../ui';

const COMMANDS = (base: string) => [
  { id: 'agents',   cmd: `curl -H "Authorization: Bearer $API_KEY" ${base}/api/agents`,                              desc: 'Liệt kê agent trong tenant.' },
  { id: 'catalog',  cmd: `curl -H "Authorization: Bearer $API_KEY" ${base}/api/catalog/agents`,                      desc: 'Catalog agent đã publish (dùng cho routing).' },
  { id: 'convs',    cmd: `curl -H "Authorization: Bearer $API_KEY" "${base}/api/conversations?limit=20"`,           desc: 'Hội thoại gần nhất.' },
  { id: 'messages', cmd: `curl -H "Authorization: Bearer $API_KEY" ${base}/api/conversations/<conversation-id>/messages`, desc: 'Transcript và tool traces của một hội thoại.' },
  { id: 'traces',   cmd: `curl -H "Authorization: Bearer $API_KEY" "${base}/api/traces?status=error&limit=50"`,     desc: 'Traces lỗi gần nhất.' },
  { id: 'metrics',  cmd: `curl -H "Authorization: Bearer $API_KEY" "${base}/api/platform/metrics?days=7"`,          desc: 'Số liệu nền tảng 7 ngày.' },
  { id: 'services', cmd: `curl -H "Authorization: Bearer $API_KEY" ${base}/api/platform/services`,                   desc: 'Sức khoẻ các service.' },
];

export const CLIScreen = () => {
  const { navigate } = useAppState();
  const [copied, setCopied] = useState<string | null>(null);
  const commands = COMMANDS(API_URL);

  const copy = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(id);
    } catch {
      setCopied(null);
    }
  };

  return (
    <div className="space-y-5">
      <SectionTitle
        title="CLI"
        subtitle="Gọi platform qua Kong gateway bằng API key. Thay <…> bằng giá trị thật."
        actions={<Button variant="primary" icon={KeyRound} onClick={() => navigate('settings', 'API')}>Tạo API key</Button>}
      />

      <Card title="Thiết lập" subtitle="Đặt API key vào biến môi trường trước khi chạy các lệnh bên dưới" padding="sm">
        <div className="flex items-center gap-3 p-3 bg-[#111111] rounded-xl">
          <Terminal className="w-4 h-4 text-[#D9B86C] shrink-0" />
          <code className="text-[12px] font-mono text-[#F3E2A7] flex-1 break-all">export API_KEY=&lt;api-key-từ-Settings&gt;</code>
          <Button size="sm" variant="ghost" icon={copied === 'export' ? Check : Copy} onClick={() => void copy('export', 'export API_KEY=')}>
            {copied === 'export' ? 'Đã copy' : 'Copy'}
          </Button>
        </div>
      </Card>

      <Card title="Lệnh thường dùng" subtitle={`Base URL: ${API_URL}`} padding="none">
        <ul className="divide-y divide-[#BFA66A]">
          {commands.map((c, i) => (
            <li key={c.id} className="flex items-center gap-3 px-4 py-3">
              <div className="w-7 h-7 rounded-lg bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center text-[#8A5A00] font-mono text-[11px] shrink-0">{i + 1}</div>
              <div className="flex-1 min-w-0">
                <code className="block text-[12px] font-mono text-[#111111] break-all">{c.cmd}</code>
                <div className="text-[11px] text-[#5F5F5F] mt-0.5">{c.desc}</div>
              </div>
              <Button size="sm" variant="ghost" icon={copied === c.id ? Check : Copy} onClick={() => void copy(c.id, c.cmd)} aria-label={`Copy lệnh ${i + 1}`}>
                {copied === c.id ? 'Đã copy' : 'Copy'}
              </Button>
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Ghi chú" padding="sm">
        <div className="flex items-start gap-2 text-[12px] text-[#3F3F3F]">
          <Code className="w-4 h-4 text-[#8A5A00] shrink-0 mt-0.5" />
          <p>Mọi request đi qua gateway; Kong xác thực key và gắn tenant. Response bọc trong <code className="font-mono">{'{ code, data, error }'}</code>.</p>
        </div>
      </Card>
    </div>
  );
};
