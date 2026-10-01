import React from 'react';

const AskDataPage = React.lazy(() => import('./features/ask/AskDataPage'));
const KnowledgeOpsCenter = React.lazy(() => import('./components/knowledge/KnowledgeOpsCenter'));
const AgentPlayground = React.lazy(() => import('./components/agent-builder/AgentPlayground'));
const Conversations = React.lazy(() => import('./components/agent-builder/Conversations'));
const ReviewCenter = React.lazy(() => import('./components/agent-builder/ReviewCenter'));
const SettingsView = React.lazy(() => import('./components/Settings/Overview'));
const WorkflowEngine = React.lazy(() => import('./components/agent-builder/workflow/Overview'));
const AgentRuntimeView = React.lazy(() =>
  import('./components/agent-builder/agents/AgentRuntime').then(module => ({
    default: module.AgentRuntimeView,
  })),
);
const TraceExplorer = React.lazy(() =>
  import('./components/agent-builder/agents/AgentTraces').then(module => ({
    default: module.TraceExplorer,
  })),
);
const ExecutiveDashboard = React.lazy(() =>
  import('./components/ExecutiveDashboard').then(module => ({
    default: module.ExecutiveDashboard,
  })),
);

interface ComingSoonInfo {
  title: string;
  description: string;
  icon?: any;
  plannedFeatures?: string[];
}

type RouteEntry =
  | { render: () => React.ReactNode }
  | { comingSoon: ComingSoonInfo };

/** Sub-tab key for modules that have a single screen and no secondary nav. */
export const SINGLE_SCREEN = '_';

/**
 * module → subTab → screen.
 *
 * The IA starts with what a business user needs to DO (ask the data), then
 * knowledge, then the builder console, then operations. A module whose only
 * entry is `SINGLE_SCREEN` renders full-width with no secondary nav.
 *
 * Nothing here is a placeholder: every entry is a real screen. Areas without
 * a screen yet (router, tools, evals, experiments, endpoints, webhooks) are
 * simply absent from the nav until one exists.
 */
export const ROUTE_MAP: Record<string, Record<string, RouteEntry>> = {
  ask: {
    [SINGLE_SCREEN]: { render: () => <AskDataPage /> },
  },

  knowledge: {
    [SINGLE_SCREEN]: { render: () => <KnowledgeOpsCenter /> },
  },

  build: {
    AGENTS:     { render: () => <AgentRuntimeView /> },
    WORKFLOWS:  { render: () => <WorkflowEngine /> },
    PLAYGROUND: { render: () => <AgentPlayground /> },
  },

  ops: {
    CONVERSATIONS: { render: () => <Conversations /> },
    TRACES:        { render: () => <TraceExplorer /> },
    REVIEWS:       { render: () => <ReviewCenter /> },
    ANALYTICS:     { render: () => <ExecutiveDashboard /> },
  },

  // One entry per Settings section; the module's secondary nav is its only nav.
  settings: {
    OVERVIEW:       { render: () => <SettingsView section="OVERVIEW" /> },
    ORGANIZATION:   { render: () => <SettingsView section="ORGANIZATION" /> },
    IAM:            { render: () => <SettingsView section="IAM" /> },
    AUTH:           { render: () => <SettingsView section="AUTH" /> },
    SECRETS:        { render: () => <SettingsView section="SECRETS" /> },
    SECURITY:       { render: () => <SettingsView section="SECURITY" /> },
    AI_PROVIDERS:   { render: () => <SettingsView section="AI_PROVIDERS" /> },
    INFRASTRUCTURE: { render: () => <SettingsView section="INFRASTRUCTURE" /> },
    STORAGE:        { render: () => <SettingsView section="STORAGE" /> },
    BILLING:        { render: () => <SettingsView section="BILLING" /> },
    API:            { render: () => <SettingsView section="API" /> },
    ADVANCED:       { render: () => <SettingsView section="ADVANCED" /> },
  },
};
