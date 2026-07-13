import React from 'react';
import { Route, Wrench, ClipboardCheck, FlaskConical, PlugZap, Webhook } from 'lucide-react';

import KnowledgeOpsCenter from './components/knowledge/KnowledgeOpsCenter';
import AgentPlayground from './components/agent-builder/AgentPlayground';
import Conversations from './components/agent-builder/Conversations';
import ReleaseManagementCenter from './components/ReleaseManagementCenter';
import SettingsView from './components/Settings/Overview';
import { AgentRuntimeView } from './components/agent-builder/agents/AgentRuntime';
import { TraceExplorer } from './components/agent-builder/agents/AgentTraces';
import { ExecutiveDashboard } from './components/ExecutiveDashboard';
import WorkflowEngine from './components/agent-builder/workflow/Overview';

export interface ComingSoonInfo {
  title: string;
  description: string;
  icon?: any;
  plannedFeatures?: string[];
}

export type RouteEntry =
  | { render: () => React.ReactNode }
  | { comingSoon: ComingSoonInfo };

/**
 * module → subTab → screen.
 * Mirrors the AeroFlow reference IA (my_doc/aeroflow/shell.jsx + app.jsx):
 * the left nav drives module + sub-item, and each sub-item resolves to a
 * focused leaf view here. Items with no screen yet render a Coming Soon placeholder.
 */
export const ROUTE_MAP: Record<string, Record<string, RouteEntry>> = {
  build: {
    AGENTS:    { render: () => <AgentRuntimeView /> },
    KNOWLEDGE: { render: () => <KnowledgeOpsCenter /> },
    WORKFLOWS: { render: () => <WorkflowEngine /> },
    ROUTER: {
      comingSoon: {
        title: 'Router',
        icon: Route,
        description: 'Define how incoming messages are classified and routed to the right agent.',
        plannedFeatures: ['Intent classification rules', 'Per-agent routing weights', 'Fallback & escalation paths'],
      },
    },
    TOOLS: {
      comingSoon: {
        title: 'Tools',
        icon: Wrench,
        description: 'Register and manage the tools and function calls your agents can invoke.',
        plannedFeatures: ['Tool registry & schemas', 'Auth & rate limits', 'Sandboxed execution'],
      },
    },
  },

  test: {
    PLAYGROUND: { render: () => <AgentPlayground /> },
    EVALS: {
      comingSoon: {
        title: 'Evals',
        icon: ClipboardCheck,
        description: 'Build evaluation suites and grade agent responses against expected outcomes.',
        plannedFeatures: ['Test-case datasets', 'Automated grading', 'Regression tracking'],
      },
    },
  },

  release: {
    VERSIONS: { render: () => <ReleaseManagementCenter /> },
    EXPERIMENTS: {
      comingSoon: {
        title: 'Experiments',
        icon: FlaskConical,
        description: 'Run A/B experiments across agent versions and compare live performance.',
        plannedFeatures: ['Traffic splitting', 'Metric comparison', 'Auto-promote winners'],
      },
    },
    ENDPOINT: {
      comingSoon: {
        title: 'Chat Endpoint',
        icon: PlugZap,
        description: 'Expose your released agents over a managed chat API endpoint.',
        plannedFeatures: ['API keys & quotas', 'Streaming responses', 'Usage analytics'],
      },
    },
    WEBHOOKS: {
      comingSoon: {
        title: 'Webhooks',
        icon: Webhook,
        description: 'Subscribe external systems to events from your agents and pipelines.',
        plannedFeatures: ['Event subscriptions', 'Delivery retries', 'Signing secrets'],
      },
    },
  },

  monitor: {
    CONVERSATIONS: { render: () => <Conversations /> },
    TRACES:        { render: () => <TraceExplorer /> },
    ANALYTICS:     { render: () => <ExecutiveDashboard /> },
  },

  settings: {
    TEAM:      { render: () => <SettingsView initialSection="IAM" /> },
    PROVIDERS: { render: () => <SettingsView initialSection="AI_PROVIDERS" /> },
    SECRETS:   { render: () => <SettingsView initialSection="SECRETS" /> },
    BILLING:   { render: () => <SettingsView initialSection="BILLING" /> },
    AUDIT:     { render: () => <SettingsView initialSection="SECURITY" /> },
  },
};
