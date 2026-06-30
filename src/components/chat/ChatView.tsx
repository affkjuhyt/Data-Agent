import React, { useState, useRef, useEffect } from 'react';
import { MessageSquare, Bot, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { cn } from '../../lib/utils';
import { ChatProvider, useChatContext } from './ChatContext';
import ConversationSidebar from './ConversationSidebar';
import MessageBubble from './MessageBubble';
import ChatInput from './ChatInput';

/* ─── Empty state ────────────────────────────────────────────────── */
function EmptyState() {
  const { agents, selectedAgentId, createConversation } = useChatContext();
  const agent = agents.find(a => a.id === selectedAgentId);
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8 opacity-80">
      <div className="w-14 h-14 rounded-2xl bg-[#F3E2A7] border border-[#B88719] flex items-center justify-center">
        <Bot className="w-7 h-7 text-[#B88719]" />
      </div>
      <div className="text-center">
        <p className="text-[15px] font-semibold text-[#111111]">
          {agent ? `Chat với ${agent.name}` : 'Chọn agent để bắt đầu'}
        </p>
        <p className="text-[13px] text-[#5A5A5A] mt-1">
          {agent
            ? 'Nhập câu hỏi bên dưới hoặc tạo conversation mới.'
            : 'Chọn agent từ sidebar bên trái để bắt đầu hội thoại.'}
        </p>
      </div>
      <button
        onClick={createConversation}
        className="px-4 py-2 rounded-xl bg-[#B88719] hover:bg-[#8A5A00] text-white text-[13px] font-semibold transition-colors"
      >
        + Tạo conversation mới
      </button>
    </div>
  );
}

/* ─── Chat area (messages + input) ──────────────────────────────── */
function ChatArea() {
  const { messages, isStreaming, sendMessage, activeConvId, conversations } = useChatContext();
  const [inputText, setInputText] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  /* Scroll to bottom on new messages */
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, messages[messages.length - 1]?.content?.length]);

  const handleSend = () => {
    if (!inputText.trim()) return;
    sendMessage(inputText.trim());
    setInputText('');
  };

  const activeConv = conversations.find(c => c.id === activeConvId);

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0">
      {/* Conversation header */}
      {activeConv && (
        <div className="flex items-center gap-2.5 px-5 py-3 border-b border-[#D6C79F] bg-white shrink-0">
          <MessageSquare className="w-4 h-4 text-[#B88719] shrink-0" />
          <div className="min-w-0">
            <p className="text-[13.5px] font-semibold text-[#111111] truncate">{activeConv.title}</p>
            <p className="text-[11px] text-[#5A5A5A]">{activeConv.agentName}</p>
          </div>
        </div>
      )}

      {/* Message list */}
      <div className="flex-1 overflow-y-auto custom-scrollbar px-5 py-4 space-y-4">
        {messages.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            <AnimatePresence initial={false}>
              {messages.map(msg => (
                <MessageBubble key={msg.id} msg={msg} />
              ))}
            </AnimatePresence>
            <div ref={bottomRef} />
          </>
        )}
      </div>

      {/* Input */}
      <ChatInput value={inputText} onChange={setInputText} onSend={handleSend} />
    </div>
  );
}

/* ─── Inner view (consumes context) ─────────────────────────────── */
function ChatInner() {
  const [sidebarOpen, setSidebarOpen] = useState(true);

  return (
    <div className="flex h-[calc(100vh-200px)] min-h-[520px] rounded-2xl border border-[#D6C79F] bg-[#FCFBF7] overflow-hidden shadow-sm">
      {/* Sidebar toggle button (mobile / collapsed state) */}
      <button
        onClick={() => setSidebarOpen(v => !v)}
        title={sidebarOpen ? 'Ẩn sidebar' : 'Mở sidebar'}
        className="absolute z-20 top-3 left-3 hidden sm:flex items-center justify-center w-7 h-7 rounded-lg bg-white border border-[#D6C79F] text-[#5A5A5A] hover:bg-[#F8EBC4] hover:text-[#111111] transition-colors shadow-sm"
        style={{ position: 'absolute' }}
      >
        {sidebarOpen
          ? <PanelLeftClose className="w-3.5 h-3.5" />
          : <PanelLeftOpen  className="w-3.5 h-3.5" />
        }
      </button>

      {/* Sidebar */}
      <AnimatePresence initial={false}>
        {sidebarOpen && (
          <motion.div
            key="sidebar"
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 260, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden shrink-0"
          >
            <ConversationSidebar />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main chat */}
      <ChatArea />
    </div>
  );
}

/* ─── ChatView (exported, wraps provider) ────────────────────────── */
export default function ChatView() {
  return (
    <div className="relative">
      <ChatProvider>
        <ChatInner />
      </ChatProvider>
    </div>
  );
}
