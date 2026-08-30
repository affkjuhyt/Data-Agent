import { 
  Bot, 
  Cpu, 
  Settings, 
  Zap, 
  Terminal, 
  Play, 
  History as HistoryIcon, 
  ShieldAlert, 
  Search,
  Plus,
  Network,
  Eye,
  Activity,
  Layers,
  Database,
  Wrench,
  ShieldCheck,
  MemoryStick,
  GitBranch,
  Repeat,
  CheckCircle2,
  AlertTriangle,
  Clock,
  ArrowUpRight,
  MoreHorizontal,
  ChevronRight,
  FileCode,
  Globe,
  Lock,
  User,
  ExternalLink,
  Save,
  MessageSquare,
  BarChart3,
  Trash2,
  Copy,
  FileText
} from 'lucide-react';

export type AgentStatus = 'ACTIVE' | 'IDLE' | 'BUSY' | 'FAILED' | 'UNHEALTHY' | 'QUEUED' | 'WAITING_APPROVAL';
export type AgentType = 'RAG' | 'GRAPHRAG' | 'TOOL_USE' | 'WORKFLOW' | 'COORDINATOR' | 'HITL' | 'API' | 'DOC_PROC';
export type Environment = 'DEV' | 'UAT' | 'PROD';

export interface Agent {
  id: string;
  name: string;
  type: AgentType;
  tenant: string;
  project: string;
  environment: Environment;
  status: AgentStatus;
  version: string;
  kbConnection: string;
  tools: string[];
  lastRun: string;
  errorRate: number;
  cost: number;
  owner: string;
  latency: string;
  healthScore: number;
  description: string;
  businessPurpose: string;
}

export interface Trace {
  id: string;
  agentId: string;
  agentName: string;
  tenant: string;
  project: string;
  query: string;
  status: 'SUCCESS' | 'FAILURE' | 'PENDING';
  latency: number;
  tokens: number;
  cost: number;
  toolCalls: number;
  retrievalCount: number;
  error?: string;
  timestamp: string;
}

export interface ConfigItem {
  id: string;
  name: string;
  type: 'AGENT' | 'PROMPT' | 'RETRIEVAL' | 'TOOL' | 'MODEL' | 'GUARDRAIL' | 'MEMORY' | 'DEPLOYMENT';
  version: string;
  usedBy: string[];
  environment: Environment;
  lastModified: string;
  owner: string;
  validationStatus: 'PASS' | 'FAIL' | 'WARNING';
  content: string; // YAML or JSON string
}

export interface AgentRun {
  id: string;
  agentId: string;
  agentName: string;
  tenant: string;
  project: string;
  environment: Environment;
  trigger: 'API' | 'SCHEDULED' | 'MANUAL';
  status: AgentStatus;
  startedAt: string;
  duration: string;
  cost: number;
  toolCalls: number;
  retrievalCalls: number;
  approvalState: 'NONE' | 'PENDING' | 'APPROVED' | 'REJECTED';
}
