import { LucideIcon } from 'lucide-react';

export enum WorkflowStatus {
  DRAFT = 'DRAFT',
  PUBLISHED = 'PUBLISHED',
  DORMANT = 'DORMANT',
  ERROR = 'ERROR'
}

export enum ExecutionStatus {
  QUEUED = 'QUEUED',
  RUNNING = 'RUNNING',
  WAITING_APPROVAL = 'WAITING_APPROVAL',
  PAUSED = 'PAUSED',
  RETRYING = 'RETRYING',
  FAILED = 'FAILED',
  COMPLETED = 'COMPLETED',
  CANCELLED = 'CANCELLED'
}

export enum WorkflowType {
  AI_AGENT = 'AI_AGENT',
  KB_PIPELINE = 'KB_PIPELINE',
  HUMAN_APPROVAL = 'HUMAN_APPROVAL',
  INCIDENT_RESPONSE = 'INCIDENT_RESPONSE',
  DEPLOYMENT = 'DEPLOYMENT',
  DOCUMENT_PROCESSING = 'DOCUMENT_PROCESSING',
  MULTI_AGENT = 'MULTI_AGENT',
  EVENT_DRIVEN = 'EVENT_DRIVEN',
  SCHEDULED = 'SCHEDULED'
}

export interface WorkflowNode {
  id: string;
  type: string;
  label: string;
  icon: LucideIcon;
  config: any;
  position: { x: number; y: number };
  status?: 'idle' | 'running' | 'success' | 'failed' | 'waiting';
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
}

export interface Workflow {
  id: string;
  name: string;
  type: WorkflowType;
  tenant: string;
  project: string;
  version: string;
  status: WorkflowStatus;
  lastDeployment: string;
  lastExecution?: string;
  successRate: number;
  avgDuration: string;
  owner: string;
  tags: string[];
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface WorkflowExecution {
  id: string;
  workflowId: string;
  workflowName: string;
  trigger: 'WEBHOOK' | 'SCHEDULE' | 'UI' | 'EVENT';
  tenant: string;
  environment: 'PROD' | 'STAGING' | 'DEV';
  status: ExecutionStatus;
  startedAt: string;
  duration: string;
  cost: number;
  currentNodeId?: string;
  waitingReason?: string;
}
