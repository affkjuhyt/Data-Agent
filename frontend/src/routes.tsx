import React from 'react';
import type { AreaId } from './components/shell/areas';

const HomePage = React.lazy(() => import('./features/home/HomePage'));
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

type RouteEntry = { render: () => React.ReactNode };

/** Page key for areas that have a single screen and no rail. */
export const SINGLE_SCREEN = '_';

/**
 * area → page → screen, for the six v3.1 areas (constants.ts AREA_RAILS).
 * Nothing here is a placeholder: every entry is a real screen.
 */
export const ROUTE_MAP: Record<AreaId, Record<string, RouteEntry>> = {
  home: {
    [SINGLE_SCREEN]: { render: () => <HomePage /> },
  },

  'needs-you': {
    [SINGLE_SCREEN]: { render: () => <ReviewCenter /> },
  },

  agents: {
    TEAM:          { render: () => <AgentRuntimeView /> },
    RUNS:          { render: () => <TraceExplorer /> },
    EVALUATE:      { render: () => <AgentPlayground /> },
    ORCHESTRATION: { render: () => <WorkflowEngine /> },
  },

  analysis: {
    ASK:       { render: () => <AskDataPage /> },
    SESSIONS:  { render: () => <Conversations /> },
    DASHBOARD: { render: () => <ExecutiveDashboard /> },
  },

  data: {
    [SINGLE_SCREEN]: { render: () => <KnowledgeOpsCenter /> },
  },

  admin: {
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
