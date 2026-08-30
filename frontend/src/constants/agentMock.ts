import { Agent, Trace, ConfigItem, AgentRun } from '../types/agent';

export const MOCK_AGENTS: Agent[] = [
  {
    id: 'agt-001',
    name: 'Customer Support Bot',
    type: 'RAG',
    tenant: 'BlueChip Enterprise',
    project: 'Service Desk',
    environment: 'PROD',
    status: 'ACTIVE',
    version: '4.2.1',
    kbConnection: 'kb-corporate-v4',
    tools: ['ticketing', 'crm_lookup'],
    lastRun: '2m ago',
    errorRate: 0.2,
    cost: 4.25,
    owner: 'Sarah Connor',
    latency: '420ms',
    healthScore: 98,
    description: 'Handles basic customer inquiries using corporate knowledge base.',
    businessPurpose: 'Reduce human agent workload by 40%.'
  },
  {
    id: 'agt-002',
    name: 'Policy Risk Engine',
    type: 'GRAPHRAG',
    tenant: 'Federal Systems',
    project: 'Compliance',
    environment: 'UAT',
    status: 'BUSY',
    version: '1.12.0',
    kbConnection: 'kb-gov-policies',
    tools: ['legal_ref', 'risk_calculator'],
    lastRun: '15m ago',
    errorRate: 1.5,
    cost: 12.80,
    owner: 'James Smith',
    latency: '850ms',
    healthScore: 85,
    description: 'Analyzes policy documents for compliance risks using semantic graph relations.',
    businessPurpose: 'Automate initial compliance reviews.'
  },
  {
    id: 'agt-003',
    name: 'Ops-Automation',
    type: 'TOOL_USE',
    tenant: 'BlueChip Enterprise',
    project: 'Platform Engineering',
    environment: 'DEV',
    status: 'IDLE',
    version: '3.1.2',
    kbConnection: 'none',
    tools: ['k8s_manager', 'db_scanner', 'logs_aggregator'],
    lastRun: '1h ago',
    errorRate: 0.0,
    cost: 0.50,
    owner: 'Elena Rigby',
    latency: '210ms',
    healthScore: 100,
    description: 'Autonomous DevOps agent for infrastructure management.',
    businessPurpose: 'Automate scale-up/down operations.'
  },
  {
    id: 'agt-004',
    name: 'Medical Assistant',
    type: 'WORKFLOW',
    tenant: 'HealthFirst',
    project: 'Clinical Support',
    environment: 'PROD',
    status: 'FAILED',
    version: 'v2.4.0',
    kbConnection: 'kb-medical-v2',
    tools: ['patient_records', 'dosage_check'],
    lastRun: '5h ago',
    errorRate: 12.4,
    cost: 2.10,
    owner: 'Dr. Gregory House',
    latency: '1.2s',
    healthScore: 42,
    description: 'Assists doctors with clinical decision support.',
    businessPurpose: 'Improve diagnosis accuracy.'
  }
];

export const MOCK_TRACES: Trace[] = [
  {
    id: 'trace-8821',
    agentId: 'agt-001',
    agentName: 'Customer Support Bot',
    tenant: 'BlueChip Enterprise',
    project: 'Service Desk',
    query: 'How do I reset my subscription?',
    status: 'SUCCESS',
    latency: 1450,
    tokens: 840,
    cost: 0.012,
    toolCalls: 1,
    retrievalCount: 3,
    timestamp: '2025-05-16 10:42:15'
  },
  {
    id: 'trace-8822',
    agentId: 'agt-002',
    agentName: 'Policy Risk Engine',
    tenant: 'Federal Systems',
    project: 'Compliance',
    query: 'Cross-check HIPAA compliance for Section 4.',
    status: 'FAILURE',
    latency: 3200,
    tokens: 4500,
    cost: 0.150,
    toolCalls: 2,
    retrievalCount: 12,
    error: 'Graph connection timeout',
    timestamp: '2025-05-16 10:45:30'
  }
];

export const MOCK_CONFIGS: ConfigItem[] = [
  {
    id: 'cfg-001',
    name: 'Standard RAG Prompt',
    type: 'PROMPT',
    version: 'v2.1',
    usedBy: ['agt-001', 'agt-005'],
    environment: 'PROD',
    lastModified: '2 days ago',
    owner: 'Sarah Connor',
    validationStatus: 'PASS',
    content: 'template: | \n  You are a helpful assistant...\n'
  },
  {
    id: 'cfg-002',
    name: 'Titanium Guardrails',
    type: 'GUARDRAIL',
    version: 'v1.0',
    usedBy: ['agt-002', 'agt-004'],
    environment: 'UAT',
    lastModified: '5h ago',
    owner: 'Security Team',
    validationStatus: 'WARNING',
    content: 'blocked_topics: ["politics", "internal_salaries"]\n'
  }
];

export const MOCK_RUNS: AgentRun[] = [
  {
    id: 'run-990',
    agentId: 'agt-001',
    agentName: 'Customer Support Bot',
    tenant: 'BlueChip Enterprise',
    project: 'Service Desk',
    environment: 'PROD',
    trigger: 'API',
    status: 'ACTIVE',
    startedAt: '1m ago',
    duration: '14s',
    cost: 0.005,
    toolCalls: 1,
    retrievalCalls: 2,
    approvalState: 'NONE'
  },
  {
    id: 'run-991',
    agentId: 'agt-002',
    agentName: 'Policy Risk Engine',
    tenant: 'Federal Systems',
    project: 'Compliance',
    environment: 'UAT',
    trigger: 'SCHEDULED',
    status: 'WAITING_APPROVAL',
    startedAt: '10m ago',
    duration: '2m 45s',
    cost: 0.450,
    toolCalls: 8,
    retrievalCalls: 24,
    approvalState: 'PENDING'
  }
];
