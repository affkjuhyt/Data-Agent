import React, { useState } from 'react';
import {
  Bot, User, Copy, Check, Pencil, RefreshCw, AlertTriangle, ChevronDown, ChevronUp,
  ThumbsUp, ThumbsDown,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';
import { ChatMessage, PlanStep } from './types';
import { useChatContext } from './ChatContext';

/* ─── Markdown renderer (warm light theme) ───────────────────────── */

function renderInline(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**'))
      return <strong key={i} className="font-semibold text-[#111111]">{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`'))
      return (
        <code key={i} className="font-mono text-[0.84em] bg-[#F3E2A7] text-[#8A5A00] px-1 py-0.5 rounded">
          {part.slice(1, -1)}
        </code>
      );
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link)
      return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer" className="text-[#B88719] underline">{link[1]}</a>;
    return <span key={i}>{part}</span>;
  });
}

function MarkdownContent({ text }: { text: string }) {
  const lines = text.split('\n');
  const nodes: React.ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith('```')) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) { codeLines.push(lines[i]); i++; }
      i++;
      nodes.push(
        <pre key={`cb${i}`} className="bg-[#111111] text-[#EBD07C] rounded-lg px-3.5 py-3 my-2 text-[12px] font-mono overflow-x-auto whitespace-pre leading-relaxed">
          {codeLines.join('\n')}
        </pre>,
      );
      continue;
    }
    const hm = line.match(/^(#{1,4})\s+(.+)/);
    if (hm) {
      const lvl = hm[1].length;
      const sz  = ['text-[17px]', 'text-[15px]', 'text-[13.5px]', 'text-[13px]'][lvl - 1];
      nodes.push(
        <p key={`h${i}`} className={cn(sz, 'font-bold text-[#111111]', lvl <= 2 ? 'mt-3 mb-1' : 'mt-2 mb-0.5')}>
          {renderInline(hm[2])}
        </p>,
      );
      i++; continue;
    }
    if (/^[-*]\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i])) { items.push(lines[i].replace(/^[-*]\s/, '')); i++; }
      nodes.push(
        <ul key={`ul${i}`} className="my-1 pl-4 space-y-0.5">
          {items.map((it, j) => (
            <li key={j} className="text-[13px] text-[#2A2A2A] list-disc">{renderInline(it)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) { items.push(lines[i].replace(/^\d+\.\s/, '')); i++; }
      nodes.push(
        <ol key={`ol${i}`} className="my-1 pl-5 space-y-0.5">
          {items.map((it, j) => (
            <li key={j} className="text-[13px] text-[#2A2A2A] list-decimal">{renderInline(it)}</li>
          ))}
        </ol>,
      );
      continue;
    }
    if (!line.trim()) { i++; continue; }
    nodes.push(
      <p key={`p${i}`} className="text-[13px] text-[#2A2A2A] leading-relaxed my-0.5">
        {renderInline(line)}
      </p>,
    );
    i++;
  }
  return <>{nodes}</>;
}

/* ─── Plan Approval Card ─────────────────────────────────────────── */

function PlanApprovalCard({ msg }: { msg: ChatMessage }) {
  const { submitApproval } = useChatContext();
  const approval = msg.pendingApproval!;
  const [steps, setSteps]   = useState<PlanStep[]>(approval.payload?.steps ?? []);
  const [note, setNote]     = useState('');
  const [loading, setLoading] = useState(false);
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editDraft, setEditDraft]   = useState('');

  const updateStep = (idx: number, patch: Partial<PlanStep>) =>
    setSteps(prev => prev.map((s, i) => i === idx ? { ...s, ...patch } : s));

  const handleApprove = async (approved: boolean) => {
    setLoading(true);
    await submitApproval(approval.convId, approval.placeholderId, approved, note || undefined);
    setLoading(false);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="mt-2 rounded-xl border border-[#B88719] bg-[#FFFCF0] overflow-hidden"
    >
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[#D9C07E] bg-[#FFF9E8]">
        <div className="w-2 h-2 rounded-full bg-[#B88719] animate-pulse" />
        <span className="text-[11px] font-bold text-[#8A5A00] uppercase tracking-wider">
          Cần phê duyệt kế hoạch
        </span>
      </div>

      <div className="px-4 py-3 space-y-2">
        {approval.payload?.title && (
          <p className="text-[13.5px] font-semibold text-[#111111]">{approval.payload.title}</p>
        )}
        {approval.payload?.description && steps.length === 0 && (
          <p className="text-[12.5px] text-[#3A3A3A] leading-relaxed bg-[#FFF3D0] rounded-lg px-3 py-2 font-mono border border-[#D9C07E]">
            {approval.payload.description}
          </p>
        )}

        {/* Step list */}
        {steps.length > 0 && (
          <div className="space-y-1.5">
            {steps.map((step, idx) => (
              <div
                key={step.id}
                className={cn(
                  'rounded-lg border px-3 py-2 transition-colors',
                  step.approved === true  ? 'bg-green-50  border-green-200' :
                  step.approved === false ? 'bg-red-50    border-red-200'   :
                  'bg-white border-[#D6C79F]',
                )}
              >
                <div className="flex items-start gap-2">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[#F3E2A7] border border-[#B88719] flex items-center justify-center text-[10px] font-bold text-[#8A5A00] mt-0.5">
                    {idx + 1}
                  </span>
                  <div className="flex-1 min-w-0">
                    {editingIdx === idx ? (
                      <div className="flex items-center gap-1">
                        <input
                          autoFocus
                          value={editDraft}
                          onChange={e => setEditDraft(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') { updateStep(idx, { editedContent: editDraft, approved: null }); setEditingIdx(null); }
                            if (e.key === 'Escape') setEditingIdx(null);
                          }}
                          className="flex-1 text-[12.5px] border border-[#B88719] rounded-md px-2 py-0.5 outline-none"
                        />
                        <button onClick={() => { updateStep(idx, { editedContent: editDraft, approved: null }); setEditingIdx(null); }}>
                          <Check className="w-3.5 h-3.5 text-[#B88719]" />
                        </button>
                      </div>
                    ) : (
                      <p className="text-[12.5px] text-[#111111] leading-snug">
                        {step.editedContent ?? step.label}
                        {step.editedContent && (
                          <span className="ml-1.5 text-[10px] text-[#B88719] font-semibold">(đã sửa)</span>
                        )}
                      </p>
                    )}
                    {step.description && (
                      <p className="text-[11px] text-[#5A5A5A] mt-0.5 leading-relaxed">{step.description}</p>
                    )}
                  </div>
                  {editingIdx !== idx && (
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        onClick={() => { setEditDraft(step.editedContent ?? step.label); setEditingIdx(idx); }}
                        className="p-1 rounded text-[#777777] hover:text-[#B88719] hover:bg-[#F3E2A7] transition-colors"
                        title="Sửa bước này"
                      >
                        <Pencil className="w-3 h-3" />
                      </button>
                      <button
                        onClick={() => updateStep(idx, { approved: step.approved !== true ? true : null })}
                        className={cn('p-1 rounded transition-colors', step.approved === true ? 'text-green-600 bg-green-100' : 'text-[#777777] hover:text-green-600 hover:bg-green-50')}
                        title="Chấp nhận"
                      >
                        <ThumbsUp className="w-3 h-3" />
                      </button>
                      <button
                        onClick={() => updateStep(idx, { approved: step.approved !== false ? false : null })}
                        className={cn('p-1 rounded transition-colors', step.approved === false ? 'text-red-500 bg-red-100' : 'text-[#777777] hover:text-red-500 hover:bg-red-50')}
                        title="Từ chối"
                      >
                        <ThumbsDown className="w-3 h-3" />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Note input */}
        <textarea
          value={note}
          onChange={e => setNote(e.target.value)}
          placeholder="Ghi chú cho agent (tuỳ chọn)…"
          rows={2}
          className="w-full text-[12px] resize-none bg-white border border-[#D6C79F] rounded-lg px-2.5 py-2 outline-none focus:border-[#B88719] text-[#111111] placeholder:text-[#AAAAAA]"
        />

        {/* Action buttons */}
        <div className="flex gap-2">
          <button
            onClick={() => handleApprove(false)}
            disabled={loading}
            className="flex-1 py-2 rounded-lg border border-red-200 bg-red-50 text-red-600 text-[12.5px] font-semibold hover:bg-red-100 transition-colors disabled:opacity-50"
          >
            Từ chối
          </button>
          <button
            onClick={() => handleApprove(true)}
            disabled={loading}
            className="flex-1 py-2 rounded-lg border border-green-200 bg-green-50 text-green-700 text-[12.5px] font-semibold hover:bg-green-100 transition-colors disabled:opacity-50"
          >
            {loading ? 'Đang xử lý…' : 'Phê duyệt ✓'}
          </button>
        </div>
      </div>
    </motion.div>
  );
}

/* ─── Critique Loop Banner ───────────────────────────────────────── */

function CritiqueLoopBanner({ msg }: { msg: ChatMessage }) {
  const { submitCritiqueFeedback } = useChatContext();
  const [expanded, setExpanded]   = useState(false);
  const [feedback, setFeedback]   = useState('');

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="mt-2 rounded-xl border border-orange-200 bg-orange-50 overflow-hidden"
    >
      <div className="flex items-center gap-2.5 px-3.5 py-2.5">
        <AlertTriangle className="w-4 h-4 text-orange-500 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-[12.5px] font-semibold text-orange-800">
            Critique loop vượt giới hạn ({msg.critiqueRetries} lần retry)
          </p>
          <p className="text-[11px] text-orange-600 mt-0.5">
            Agent không hội tụ được sau nhiều lần tự phê duyệt. Cần hướng dẫn thủ công.
          </p>
        </div>
        <button
          onClick={() => setExpanded(v => !v)}
          className="shrink-0 p-1 rounded text-orange-500 hover:bg-orange-100 transition-colors"
        >
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0 }}
            animate={{ height: 'auto' }}
            exit={{ height: 0 }}
            className="overflow-hidden"
          >
            <div className="px-3.5 pb-3 border-t border-orange-200 pt-2.5 space-y-2">
              <p className="text-[11.5px] text-orange-700">
                Nhập hướng dẫn cụ thể để giúp agent vượt qua critique loop:
              </p>
              <textarea
                value={feedback}
                onChange={e => setFeedback(e.target.value)}
                placeholder="Ví dụ: Hãy tập trung vào X và bỏ qua yêu cầu Y. Câu trả lời không cần quá chi tiết…"
                rows={3}
                className="w-full text-[12px] resize-none bg-white border border-orange-200 rounded-lg px-2.5 py-2 outline-none focus:border-orange-400 text-[#111111] placeholder:text-[#AAAAAA]"
              />
              <button
                onClick={() => { if (feedback.trim()) submitCritiqueFeedback(msg.id, feedback.trim()); }}
                disabled={!feedback.trim()}
                className="w-full py-1.5 rounded-lg bg-orange-500 hover:bg-orange-600 text-white text-[12px] font-semibold transition-colors disabled:opacity-40"
              >
                Gửi hướng dẫn
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/* ─── Streaming dots ─────────────────────────────────────────────── */
function StreamingDots() {
  return (
    <div className="flex items-center gap-1 px-1">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-[#B88719] inline-block"
          style={{ animation: 'pulse 1.2s ease-in-out infinite', animationDelay: `${i * 0.2}s` }}
        />
      ))}
    </div>
  );
}

/* ─── MessageBubble ──────────────────────────────────────────────── */
export default function MessageBubble({ msg }: { msg: ChatMessage }) {
  const { editResend, regenerate, isStreaming } = useChatContext();
  const [copied, setCopied]     = useState(false);
  const [editing, setEditing]   = useState(false);
  const [editDraft, setEditDraft] = useState(msg.content);
  const isUser = msg.role === 'user';
  const showActions = !msg.isStreaming && !msg.pendingApproval;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(msg.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleEditSubmit = () => {
    if (editDraft.trim() && editDraft !== msg.content) editResend(msg.id, editDraft.trim());
    setEditing(false);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className={cn('flex gap-2.5', isUser ? 'flex-row-reverse' : 'flex-row')}
    >
      {/* Avatar */}
      <div className={cn(
        'w-7 h-7 rounded-full shrink-0 mt-0.5 flex items-center justify-center',
        isUser ? 'bg-[#F3E2A7] border border-[#B88719]' : 'bg-[#111111]',
      )}>
        {isUser
          ? <User className="w-3.5 h-3.5 text-[#B88719]" />
          : <Bot  className="w-3.5 h-3.5 text-[#D9B86C]" />
        }
      </div>

      {/* Bubble content */}
      <div className={cn('max-w-[76%] flex flex-col', isUser ? 'items-end' : 'items-start')}>
        <div
          className={cn(
            'rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed',
            isUser
              ? 'bg-[#F3E2A7] border border-[#D9B86C] rounded-tr-sm text-[#111111]'
              : 'bg-white border border-[#D6C79F] rounded-tl-sm text-[#2A2A2A]',
          )}
        >
          {/* User: editable or plain text */}
          {isUser ? (
            editing ? (
              <div className="flex flex-col gap-1.5">
                <textarea
                  autoFocus
                  value={editDraft}
                  onChange={e => setEditDraft(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleEditSubmit(); }
                    if (e.key === 'Escape') setEditing(false);
                  }}
                  rows={Math.min(6, msg.content.split('\n').length + 1)}
                  className="text-[13px] bg-white border border-[#B88719] rounded-lg px-2 py-1.5 resize-none outline-none w-full min-w-[200px]"
                />
                <div className="flex gap-1.5 justify-end">
                  <button onClick={() => setEditing(false)} className="text-[11px] px-2 py-0.5 rounded border border-[#D6C79F] text-[#5A5A5A] hover:bg-[#F8EBC4]">
                    Hủy
                  </button>
                  <button onClick={handleEditSubmit} className="text-[11px] px-2 py-0.5 rounded bg-[#B88719] text-white hover:bg-[#8A5A00]">
                    Gửi lại
                  </button>
                </div>
              </div>
            ) : (
              <p className="whitespace-pre-wrap">{msg.content}</p>
            )
          ) : (
            /* Assistant: markdown or streaming dots */
            msg.isStreaming && !msg.content
              ? <StreamingDots />
              : <MarkdownContent text={msg.content} />
          )}

          {/* Timestamp */}
          {!editing && (
            <p className="text-[10px] text-[#AAAAAA] mt-1.5">{msg.timestamp}</p>
          )}
        </div>

        {/* HITL cards (below bubble) */}
        {msg.pendingApproval && <PlanApprovalCard msg={msg} />}
        {msg.critiqueRetries !== undefined && msg.critiqueRetries >= 2 && (
          <CritiqueLoopBanner msg={msg} />
        )}

        {/* Suggestion chips */}
        {!isUser && msg.suggestions && msg.suggestions.length > 0 && !isStreaming && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {msg.suggestions.map((s, i) => (
              <button
                key={i}
                className="text-[11.5px] px-2.5 py-1 rounded-full bg-[#F8EBC4] border border-[#D6C79F] text-[#5A5A5A] hover:bg-[#F3E2A7] hover:border-[#B88719] hover:text-[#111111] transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {/* Message actions */}
        {showActions && (
          <div className={cn('flex items-center gap-0.5 mt-1', isUser ? 'flex-row-reverse' : 'flex-row')}>
            <button
              onClick={handleCopy}
              title="Copy"
              className="p-1 rounded text-[#AAAAAA] hover:text-[#111111] hover:bg-[#F3E2A7] transition-colors"
            >
              {copied ? <Check className="w-3 h-3 text-green-600" /> : <Copy className="w-3 h-3" />}
            </button>
            {isUser && !isStreaming && (
              <button
                onClick={() => { setEditDraft(msg.content); setEditing(true); }}
                title="Sửa & gửi lại"
                className="p-1 rounded text-[#AAAAAA] hover:text-[#111111] hover:bg-[#F3E2A7] transition-colors"
              >
                <Pencil className="w-3 h-3" />
              </button>
            )}
            {!isUser && !isStreaming && (
              <button
                onClick={regenerate}
                title="Tạo lại"
                className="p-1 rounded text-[#AAAAAA] hover:text-[#111111] hover:bg-[#F3E2A7] transition-colors"
              >
                <RefreshCw className="w-3 h-3" />
              </button>
            )}
          </div>
        )}
      </div>
    </motion.div>
  );
}
