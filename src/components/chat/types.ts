export interface AgentOption {
  id: string;
  name: string;
  description: string;
  routing_keywords?: string[];
}

export interface PlanStep {
  id: string;
  label: string;
  description?: string;
  editedContent?: string;
  approved: boolean | null;
}

export interface PlanApproval {
  convId: string;
  placeholderId: string;
  payload: {
    title?: string;
    description?: string;
    steps: PlanStep[];
  } | null;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  suggestions?: string[];
  isStreaming?: boolean;
  pendingApproval?: PlanApproval;
  critiqueRetries?: number;
}

export interface Conversation {
  id: string;
  title: string;
  agentId: string;
  agentName: string;
  createdAt: number;
  lastMessage?: string;
}

export interface ChatCtxType {
  conversations: Conversation[];
  activeConvId: string | null;
  messages: ChatMessage[];
  isStreaming: boolean;
  agents: AgentOption[];
  agentsLoading: boolean;
  selectedAgentId: string;
  setSelectedAgentId: (id: string) => void;
  sendMessage: (text: string) => void;
  stopStreaming: () => void;
  editResend: (messageId: string, newContent: string) => void;
  regenerate: () => void;
  createConversation: () => void;
  selectConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  submitApproval: (convId: string, placeholderId: string, approved: boolean, note?: string) => void;
  submitCritiqueFeedback: (messageId: string, feedback: string) => void;
}
