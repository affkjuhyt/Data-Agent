import React, {
  createContext, useContext, useState, useEffect, useRef, useCallback,
} from 'react';
import {
  AgentOption, ChatCtxType, ChatMessage, Conversation, PlanApproval, PlanStep,
} from './types';
import keycloak from '../../lib/keycloak';
import { WORKFLOW_RUNTIME_URL } from '../../lib/config';

const authHdrs = (): Record<string, string> =>
  keycloak.token ? { Authorization: `Bearer ${keycloak.token}` } : {};

/* ─── localStorage helpers ───────────────────────────────────────── */

const CONVS_KEY = 'aeroflow_chat_convs';
const msgKey = (id: string) => `aeroflow_chat_msgs_${id}`;

const loadConvs = (): Conversation[] => {
  try { return JSON.parse(localStorage.getItem(CONVS_KEY) ?? '[]'); } catch { return []; }
};
const saveConvs = (cs: Conversation[]) =>
  localStorage.setItem(CONVS_KEY, JSON.stringify(cs));

const loadMsgs = (id: string): ChatMessage[] => {
  try { return JSON.parse(localStorage.getItem(msgKey(id)) ?? '[]'); } catch { return []; }
};
const saveMsgs = (id: string, msgs: ChatMessage[]) =>
  localStorage.setItem(msgKey(id), JSON.stringify(msgs.filter(m => !m.isStreaming)));

/* ─── Parse plan steps from __pending_review payload ─────────────── */

function parsePlanSteps(raw: unknown): PlanStep[] {
  const detail = (raw as any);
  const arr: any[] =
    Array.isArray(detail) ? detail :
    Array.isArray(detail?.steps) ? detail.steps :
    [];
  if (arr.length > 0) {
    return arr.map((s: any, i: number) => ({
      id: String(i),
      label: typeof s === 'string' ? s : (s.label ?? s.name ?? `Step ${i + 1}`),
      description: typeof s === 'object' ? s.description : undefined,
      approved: null,
    }));
  }
  // Fallback: split description by newlines if no structured steps
  return [];
}

/* ─── Context ────────────────────────────────────────────────────── */

const Ctx = createContext<ChatCtxType | null>(null);

export const useChatContext = () => {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useChatContext must be inside ChatProvider');
  return ctx;
};

/* ─── Provider ───────────────────────────────────────────────────── */

export const ChatProvider = ({ children }: { children: React.ReactNode }) => {
  const [conversations, setConversations] = useState<Conversation[]>(loadConvs);
  const [activeConvId, setActiveConvId]   = useState<string | null>(null);
  const [messages, setMessages]           = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming]      = useState(false);
  const [agents, setAgents]               = useState<AgentOption[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [selectedAgentId, setSelectedAgentId] = useState('');

  const abortRef   = useRef<AbortController | null>(null);
  const msgsRef    = useRef(messages);
  const convIdRef  = useRef(activeConvId);
  useEffect(() => { msgsRef.current   = messages; }, [messages]);
  useEffect(() => { convIdRef.current = activeConvId; }, [activeConvId]);

  /* Load agents once on mount */
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${WORKFLOW_RUNTIME_URL}/api/catalog/agents`, {
          headers: {
            ...authHdrs(),
            'X-Tenant-ID': (import.meta as any).env.VITE_TENANT_ID ?? 'tenant-dev',
          },
        });
        const data: { id: string; name: string; description: string; routing_keywords?: string[] }[] =
          await res.json();
        const opts: AgentOption[] = data.map(a => ({
          id: a.id, name: a.name, description: a.description, routing_keywords: a.routing_keywords,
        }));
        setAgents(opts);
        if (opts.length > 0) setSelectedAgentId(opts[0].id);
      } catch { /* backend not running */ }
      finally { setAgentsLoading(false); }
    })();
  }, []);

  /* Persist conversations list */
  useEffect(() => { saveConvs(conversations); }, [conversations]);

  /* Load messages when active conversation changes */
  useEffect(() => {
    if (!activeConvId) { setMessages([]); return; }
    setMessages(loadMsgs(activeConvId));
  }, [activeConvId]);

  /* Persist messages (skip in-flight streaming message) */
  useEffect(() => {
    if (activeConvId) saveMsgs(activeConvId, messages);
  }, [messages, activeConvId]);

  /* ── Post-stream critique loop detection ── */
  const checkCritiqueLoop = useCallback(async (convId: string, placeholderId: string) => {
    try {
      await new Promise(r => setTimeout(r, 2500));
      const res = await fetch(`${WORKFLOW_RUNTIME_URL}/api/traces/${convId}`, {
        headers: authHdrs(),
      });
      const { observations } = await res.json();
      if (!Array.isArray(observations)) return;

      const nameCounts: Record<string, number> = {};
      observations
        .filter((o: any) => o.type === 'SPAN' && String(o.name).startsWith('judge'))
        .forEach((o: any) => { nameCounts[o.name] = (nameCounts[o.name] ?? 0) + 1; });

      const maxRetries = Math.max(0, ...Object.values(nameCounts)) - 1;
      if (maxRetries >= 2) {
        setMessages(prev => prev.map(m =>
          m.id === placeholderId ? { ...m, critiqueRetries: maxRetries } : m,
        ));
      }
    } catch { /* traces unavailable */ }
  }, []);

  /* ── Core SSE streaming ── */
  const doStream = useCallback(async (
    query: string,
    convId: string | null,
    history: ChatMessage[],
    placeholderId: string,
    agentId: string,
  ) => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setIsStreaming(true);
    let resolvedId = convId;
    let fullText   = '';

    try {
      const res = await fetch(`${WORKFLOW_RUNTIME_URL}/api/conversations/run`, {
        method: 'POST',
        headers: { ...authHdrs(), 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({
          query,
          agent_id: agentId,
          conversation_id: convId ?? undefined,
          messages: history.slice(-10).map(m => ({ role: m.role, content: m.content })),
        }),
      });

      const newId = res.headers.get('X-Conversation-Id');
      if (newId && !convId) {
        resolvedId = newId;
        setActiveConvId(newId);
      }

      const reader  = res.body!.getReader();
      const decoder = new TextDecoder();

      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const line of decoder.decode(value).split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);

          if (data === '[DONE]') break outer;

          if (data.startsWith('[ERROR]')) {
            const err = data.slice(7).trim() || 'Đã xảy ra lỗi.';
            setMessages(prev => prev.map(m =>
              m.id === placeholderId ? { ...m, content: err, isStreaming: false } : m,
            ));
            break outer;
          }

          try {
            const parsed = JSON.parse(data);

            if (parsed?.__pending_review) {
              const pr = parsed.__pending_review;
              const rawPayload = pr.payload ?? null;
              const approval: PlanApproval = {
                convId:        pr.conv_id ?? resolvedId ?? '',
                placeholderId,
                payload: rawPayload ? {
                  title:       rawPayload.title,
                  description: rawPayload.description,
                  steps:       parsePlanSteps(rawPayload.detail ?? rawPayload.steps),
                } : null,
              };
              setMessages(prev => prev.map(m =>
                m.id === placeholderId
                  ? { ...m, pendingApproval: approval, isStreaming: false }
                  : m,
              ));
            } else if (parsed?.__suggestions) {
              setMessages(prev => prev.map(m =>
                m.id === placeholderId ? { ...m, suggestions: parsed.__suggestions } : m,
              ));
            } else {
              const token = typeof parsed === 'string' ? parsed : JSON.stringify(parsed);
              fullText += token;
              setMessages(prev => prev.map(m =>
                m.id === placeholderId ? { ...m, content: fullText } : m,
              ));
            }
          } catch {
            fullText += data;
            setMessages(prev => prev.map(m =>
              m.id === placeholderId ? { ...m, content: fullText } : m,
            ));
          }
        }
      }

      setMessages(prev => prev.map(m =>
        m.id === placeholderId ? { ...m, isStreaming: false } : m,
      ));

      /* Upsert conversation record */
      const agent = agents.find(a => a.id === agentId);
      const title  = query.length > 48 ? query.slice(0, 48) + '…' : query;
      if (!convId && resolvedId) {
        setConversations(prev => [{
          id: resolvedId!,
          title,
          agentId,
          agentName:   agent?.name ?? 'Agent',
          createdAt:   Date.now(),
          lastMessage: fullText.slice(0, 80),
        }, ...prev]);
      } else if (resolvedId) {
        setConversations(prev => prev.map(c =>
          c.id === resolvedId ? { ...c, lastMessage: fullText.slice(0, 80) } : c,
        ));
      }

      if (resolvedId) checkCritiqueLoop(resolvedId, placeholderId);
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setMessages(prev => prev.map(m =>
          m.id === placeholderId
            ? { ...m, content: 'Lỗi kết nối backend.', isStreaming: false }
            : m,
        ));
      } else {
        setMessages(prev => prev.map(m =>
          m.id === placeholderId ? { ...m, isStreaming: false } : m,
        ));
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [agents, checkCritiqueLoop]);

  /* ── Public API ── */

  const sendMessage = useCallback((text: string) => {
    if (!text.trim() || isStreaming || !selectedAgentId) return;
    const now  = new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
    const uid  = `u_${Date.now()}`;
    const aid  = `a_${Date.now() + 1}`;
    const uMsg: ChatMessage = { id: uid, role: 'user',      content: text, timestamp: now };
    const aMsg: ChatMessage = { id: aid, role: 'assistant', content: '',   timestamp: now, isStreaming: true };
    setMessages(prev => [...prev, uMsg, aMsg]);
    doStream(text, convIdRef.current, [...msgsRef.current, uMsg], aid, selectedAgentId);
  }, [isStreaming, selectedAgentId, doStream]);

  const stopStreaming = useCallback(() => { abortRef.current?.abort(); }, []);

  const editResend = useCallback((messageId: string, newContent: string) => {
    const idx = msgsRef.current.findIndex(m => m.id === messageId);
    if (idx === -1) return;
    const now  = new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
    const aid  = `a_${Date.now()}`;
    const uMsg: ChatMessage = { id: `u_${Date.now() - 1}`, role: 'user', content: newContent, timestamp: now };
    const aMsg: ChatMessage = { id: aid, role: 'assistant', content: '', timestamp: now, isStreaming: true };
    const truncated = msgsRef.current.slice(0, idx);
    setMessages([...truncated, uMsg, aMsg]);
    doStream(newContent, convIdRef.current, [...truncated, uMsg], aid, selectedAgentId);
  }, [selectedAgentId, doStream]);

  const regenerate = useCallback(() => {
    const msgs = msgsRef.current;
    const lastUserIdx = [...msgs].reverse().findIndex(m => m.role === 'user');
    if (lastUserIdx === -1) return;
    const uIdx = msgs.length - 1 - lastUserIdx;
    const uMsg = msgs[uIdx];
    const now  = new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
    const aid  = `a_${Date.now()}`;
    const truncated = msgs.slice(0, uIdx + 1);
    const aMsg: ChatMessage = { id: aid, role: 'assistant', content: '', timestamp: now, isStreaming: true };
    setMessages([...truncated, aMsg]);
    doStream(uMsg.content, convIdRef.current, truncated, aid, selectedAgentId);
  }, [selectedAgentId, doStream]);

  const createConversation = useCallback(() => {
    setActiveConvId(null);
    setMessages([]);
  }, []);

  const selectConversation = useCallback((id: string) => { setActiveConvId(id); }, []);

  const deleteConversation = useCallback((id: string) => {
    localStorage.removeItem(msgKey(id));
    setConversations(prev => prev.filter(c => c.id !== id));
    setActiveConvId(prev => { if (prev === id) setMessages([]); return prev === id ? null : prev; });
  }, []);

  const renameConversation = useCallback((id: string, title: string) => {
    setConversations(prev => prev.map(c => c.id === id ? { ...c, title } : c));
  }, []);

  const submitApproval = useCallback(async (
    convId: string,
    placeholderId: string,
    approved: boolean,
    note?: string,
  ) => {
    try {
      const res = await fetch(`${WORKFLOW_RUNTIME_URL}/api/reviews/${convId}/resume`, {
        method: 'POST',
        headers: { ...authHdrs(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved, note }),
      });
      const data = await res.json();
      const text  = data.response ?? (approved ? '' : 'Yêu cầu không được phê duyệt.');
      setMessages(prev => prev.map(m =>
        m.id === placeholderId ? { ...m, pendingApproval: undefined, content: text } : m,
      ));
    } catch {
      setMessages(prev => prev.map(m =>
        m.id === placeholderId
          ? { ...m, pendingApproval: undefined, content: 'Lỗi khi xử lý phê duyệt.' }
          : m,
      ));
    }
  }, []);

  const submitCritiqueFeedback = useCallback((messageId: string, feedback: string) => {
    setMessages(prev => prev.map(m =>
      m.id === messageId ? { ...m, critiqueRetries: undefined } : m,
    ));
    sendMessage(feedback);
  }, [sendMessage]);

  return (
    <Ctx.Provider value={{
      conversations, activeConvId, messages, isStreaming,
      agents, agentsLoading, selectedAgentId, setSelectedAgentId,
      sendMessage, stopStreaming, editResend, regenerate,
      createConversation, selectConversation, deleteConversation, renameConversation,
      submitApproval, submitCritiqueFeedback,
    }}>
      {children}
    </Ctx.Provider>
  );
};
