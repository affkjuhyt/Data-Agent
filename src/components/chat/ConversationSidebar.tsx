import React, { useState, useRef, useEffect } from 'react';
import {
  Plus, Search, MessageSquare, Trash2, Pencil, Check, X, ChevronDown, Bot,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';
import { useChatContext } from './ChatContext';
import { Conversation } from './types';

/* ─── Relative time helper ───────────────────────────────────────── */
function relativeTime(ms: number): string {
  const diff = Date.now() - ms;
  const m = Math.floor(diff / 60000);
  if (m < 1)  return 'Vừa xong';
  if (m < 60) return `${m}p trước`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h trước`;
  return `${Math.floor(h / 24)}d trước`;
}

/* ─── ConversationItem ───────────────────────────────────────────── */
function ConversationItem({ conv, isActive }: { conv: Conversation; isActive: boolean }) {
  const { selectConversation, deleteConversation, renameConversation } = useChatContext();
  const [editing, setEditing]   = useState(false);
  const [draft, setDraft]       = useState(conv.title);
  const [menuOpen, setMenuOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) { setDraft(conv.title); inputRef.current?.focus(); }
  }, [editing, conv.title]);

  const confirmRename = () => {
    if (draft.trim()) renameConversation(conv.id, draft.trim());
    setEditing(false);
  };

  return (
    <div
      className={cn(
        'group relative flex items-start gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition-all border',
        isActive
          ? 'bg-[#F3E2A7] border-[#B88719]'
          : 'border-transparent hover:bg-[#F8EBC4] hover:border-[#D6C79F]',
      )}
      onClick={() => !editing && selectConversation(conv.id)}
    >
      <MessageSquare
        className={cn('w-3.5 h-3.5 mt-0.5 shrink-0', isActive ? 'text-[#B88719]' : 'text-[#777777]')}
      />

      <div className="flex-1 min-w-0">
        {editing ? (
          <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
            <input
              ref={inputRef}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') confirmRename();
                if (e.key === 'Escape') setEditing(false);
              }}
              className="flex-1 text-[12px] bg-white border border-[#B88719] rounded-md px-1.5 py-0.5 outline-none text-[#111111]"
            />
            <button onClick={confirmRename} className="text-[#B88719] hover:text-[#8A5A00]">
              <Check className="w-3 h-3" />
            </button>
            <button onClick={() => setEditing(false)} className="text-[#777777] hover:text-[#111111]">
              <X className="w-3 h-3" />
            </button>
          </div>
        ) : (
          <p className={cn('text-[12.5px] font-medium truncate leading-tight', isActive ? 'text-[#111111]' : 'text-[#2A2A2A]')}>
            {conv.title}
          </p>
        )}
        <div className="flex items-center gap-1.5 mt-0.5">
          <span className="text-[10px] text-[#777777]">{conv.agentName}</span>
          <span className="text-[#B8B8B8]">·</span>
          <span className="text-[10px] text-[#777777]">{relativeTime(conv.createdAt)}</span>
        </div>
        {conv.lastMessage && !editing && (
          <p className="text-[11px] text-[#5A5A5A] truncate mt-0.5 leading-tight">{conv.lastMessage}</p>
        )}
      </div>

      {/* Action buttons (visible on hover or when active) */}
      {!editing && (
        <div
          className={cn(
            'absolute right-2 top-2 flex items-center gap-0.5 transition-opacity',
            isActive ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
          )}
          onClick={e => e.stopPropagation()}
        >
          <button
            onClick={() => setEditing(true)}
            title="Đổi tên"
            className="p-1 rounded-md text-[#5A5A5A] hover:text-[#111111] hover:bg-[#F3E2A7] transition-colors"
          >
            <Pencil className="w-3 h-3" />
          </button>
          <button
            onClick={() => deleteConversation(conv.id)}
            title="Xóa"
            className="p-1 rounded-md text-[#5A5A5A] hover:text-red-600 hover:bg-red-50 transition-colors"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}

/* ─── ConversationSidebar ────────────────────────────────────────── */
export default function ConversationSidebar() {
  const {
    conversations, activeConvId, createConversation,
    agents, agentsLoading, selectedAgentId, setSelectedAgentId,
  } = useChatContext();

  const [query, setQuery]         = useState('');
  const [agentOpen, setAgentOpen] = useState(false);
  const agentRef = useRef<HTMLDivElement>(null);

  const filtered = query.trim()
    ? conversations.filter(c =>
        c.title.toLowerCase().includes(query.toLowerCase()) ||
        c.lastMessage?.toLowerCase().includes(query.toLowerCase()),
      )
    : conversations;

  const selectedAgent = agents.find(a => a.id === selectedAgentId);

  /* Close agent dropdown on outside click */
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (agentRef.current && !agentRef.current.contains(e.target as Node))
        setAgentOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  return (
    <div className="flex flex-col h-full bg-[#FAF7EC] border-r border-[#D6C79F] w-[260px] shrink-0">

      {/* Header */}
      <div className="flex items-center justify-between px-3 pt-4 pb-3 shrink-0">
        <span className="text-[11px] font-bold text-[#5A5A5A] uppercase tracking-widest">Conversations</span>
        <button
          onClick={createConversation}
          title="Tạo conversation mới"
          className="flex items-center gap-1 px-2 py-1 rounded-lg bg-[#B88719] hover:bg-[#8A5A00] text-white text-[11px] font-semibold transition-colors"
        >
          <Plus className="w-3 h-3" />
          New
        </button>
      </div>

      {/* Agent selector */}
      <div className="px-3 pb-2 shrink-0" ref={agentRef}>
        <button
          onClick={() => setAgentOpen(v => !v)}
          disabled={agentsLoading || agents.length === 0}
          className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg border border-[#BFA66A] bg-white hover:bg-[#FFF9E8] transition-colors text-left"
        >
          <Bot className="w-3.5 h-3.5 text-[#B88719] shrink-0" />
          <span className="flex-1 text-[12px] font-medium text-[#111111] truncate">
            {agentsLoading ? 'Loading agents…' : (selectedAgent?.name ?? 'Select agent…')}
          </span>
          <ChevronDown
            className={cn('w-3 h-3 text-[#777777] transition-transform shrink-0', agentOpen && 'rotate-180')}
          />
        </button>
        <AnimatePresence>
          {agentOpen && (
            <motion.div
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.1 }}
              className="absolute z-50 mt-1 w-[228px] bg-white border border-[#BFA66A] rounded-xl shadow-lg overflow-hidden"
            >
              {agents.map(a => (
                <button
                  key={a.id}
                  onClick={() => { setSelectedAgentId(a.id); setAgentOpen(false); }}
                  className={cn(
                    'w-full flex flex-col px-3 py-2.5 text-left transition-colors',
                    selectedAgentId === a.id ? 'bg-[#F3E2A7]' : 'hover:bg-[#F8EBC4]',
                  )}
                >
                  <span className={cn('text-[12.5px] font-semibold', selectedAgentId === a.id ? 'text-[#111111]' : 'text-[#2A2A2A]')}>
                    {a.name}
                  </span>
                  {a.description && (
                    <span className="text-[11px] text-[#777777] truncate mt-0.5">{a.description}</span>
                  )}
                </button>
              ))}
              {agents.length === 0 && (
                <p className="px-3 py-3 text-[12px] text-[#5A5A5A]">No agents available</p>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Search */}
      <div className="px-3 pb-2 shrink-0">
        <div className="flex items-center gap-2 px-2.5 py-2 rounded-lg bg-white border border-[#D6C79F]">
          <Search className="w-3 h-3 text-[#777777] shrink-0" />
          <input
            type="text"
            placeholder="Tìm conversation…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            className="flex-1 bg-transparent text-[12px] text-[#111111] placeholder:text-[#AAAAAA] outline-none"
          />
          {query && (
            <button onClick={() => setQuery('')} className="text-[#AAAAAA] hover:text-[#111111]">
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5 custom-scrollbar">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-2 pt-8 opacity-50">
            <MessageSquare className="w-6 h-6 text-[#B8B8B8]" />
            <p className="text-[11px] text-[#777777] text-center">
              {query ? 'Không tìm thấy' : 'Chưa có conversation'}
            </p>
          </div>
        ) : (
          filtered.map(c => (
            <ConversationItem
              key={c.id}
              conv={c}
              isActive={c.id === activeConvId}
            />
          ))
        )}
      </div>
    </div>
  );
}
