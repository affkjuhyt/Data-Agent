import React, { useRef, useEffect } from 'react';
import { Send, Square, Bot } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useChatContext } from './ChatContext';

interface ChatInputProps {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
}

export default function ChatInput({ value, onChange, onSend }: ChatInputProps) {
  const { isStreaming, stopStreaming, selectedAgentId, agents } = useChatContext();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  /* Auto-resize */
  const adjustHeight = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 140) + 'px';
  };

  useEffect(() => { adjustHeight(); }, [value]);

  const canSend = value.trim().length > 0 && !isStreaming && !!selectedAgentId;
  const selectedAgent = agents.find(a => a.id === selectedAgentId);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (canSend) onSend();
    }
  };

  return (
    <div className="px-4 py-3 border-t border-[#D6C79F] bg-[#FCFBF7] shrink-0">
      {/* Agent badge */}
      {selectedAgent && (
        <div className="flex items-center gap-1.5 mb-2">
          <Bot className="w-3 h-3 text-[#B88719]" />
          <span className="text-[11px] text-[#5A5A5A]">
            Đang chat với <span className="font-semibold text-[#B88719]">{selectedAgent.name}</span>
          </span>
        </div>
      )}
      {!selectedAgentId && (
        <p className="text-[11px] text-orange-500 mb-2">Chọn agent từ sidebar để bắt đầu chat.</p>
      )}

      <div
        className={cn(
          'flex items-end gap-2 rounded-xl border bg-white px-3 py-2 transition-colors',
          isStreaming
            ? 'border-[#D9B86C]'
            : value.trim()
              ? 'border-[#B88719]'
              : 'border-[#D6C79F]',
        )}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={e => { onChange(e.target.value); adjustHeight(); }}
          onKeyDown={handleKeyDown}
          placeholder={
            !selectedAgentId
              ? 'Chọn agent trước…'
              : isStreaming
                ? 'Agent đang xử lý…'
                : 'Nhập tin nhắn… (Enter gửi, Shift+Enter xuống dòng)'
          }
          disabled={!selectedAgentId}
          rows={1}
          className="flex-1 resize-none bg-transparent text-[13.5px] text-[#111111] placeholder:text-[#AAAAAA] outline-none leading-relaxed max-h-[140px] overflow-y-auto disabled:cursor-not-allowed"
        />

        {isStreaming ? (
          <button
            onClick={stopStreaming}
            title="Dừng lại"
            className="shrink-0 w-8 h-8 rounded-lg bg-[#111111] hover:bg-[#333333] flex items-center justify-center transition-colors"
          >
            <Square className="w-3.5 h-3.5 text-white fill-white" />
          </button>
        ) : (
          <button
            onClick={onSend}
            disabled={!canSend}
            title="Gửi (Enter)"
            className={cn(
              'shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-colors',
              canSend
                ? 'bg-[#B88719] hover:bg-[#8A5A00] text-white'
                : 'bg-[#F3E2A7] text-[#C8A84B] cursor-not-allowed',
            )}
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      <p className="text-[10px] text-[#AAAAAA] mt-1.5 text-center">
        AI có thể mắc lỗi. Kiểm tra thông tin quan trọng trước khi sử dụng.
      </p>
    </div>
  );
}
