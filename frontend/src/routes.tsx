import React from 'react';
import type { AreaId } from './components/shell/areas';

const HomePage = React.lazy(() => import('./features/home/HomePage'));
const NeedsYouPage = React.lazy(() => import('./features/needs-you/NeedsYouPage'));
const ProposalPage = React.lazy(() => import('./features/needs-you/ProposalPage'));
const ConflictPage = React.lazy(() => import('./features/needs-you/ConflictPage'));
const AgentTeamPage = React.lazy(() => import('./features/agents/AgentTeamPage'));
const RunsPage = React.lazy(() => import('./features/agents/RunsPage'));
const RunDetailPage = React.lazy(() => import('./features/agents/RunDetailPage'));
const AnalysisPage = React.lazy(() => import('./features/analysis/AnalysisPage'));
const DataPage = React.lazy(() => import('./features/data/DataPage'));
const AdminPage = React.lazy(() => import('./features/admin/AdminPage'));
const AskDataPage = React.lazy(() => import('./features/ask/AskDataPage'));
const KnowledgeOpsCenter = React.lazy(() => import('./components/knowledge/KnowledgeOpsCenter'));
const AgentPlayground = React.lazy(() => import('./components/agent-builder/AgentPlayground'));
const Conversations = React.lazy(() => import('./components/agent-builder/Conversations'));
const SettingsView = React.lazy(() => import('./components/Settings/Overview'));
const WorkflowEngine = React.lazy(() => import('./components/agent-builder/workflow/Overview'));
const AgentRuntimeView = React.lazy(() =>
  import('./components/agent-builder/agents/AgentRuntime').then(module => ({
    default: module.AgentRuntimeView,
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
 * area → page → screen, for the six v3.1 areas (constants.ts AREA_RAILS, AREA_LANDING).
 * SINGLE_SCREEN is an area's landing.
 * Nothing here is a placeholder: every entry is a real screen.
 */
export const ROUTE_MAP: Record<AreaId, Record<string, RouteEntry>> = {
  home: {
    [SINGLE_SCREEN]: { render: () => <HomePage /> },
  },

  'needs-you': {
    [SINGLE_SCREEN]: { render: () => <NeedsYouPage /> },
    // Detail pages, addressed /needs-you/<page>/<id>.
    PROPOSAL:        { render: () => <ProposalPage /> },
    CONFLICT:        { render: () => <ConflictPage /> },
  },

  agents: {
    TEAM:          { render: () => <AgentTeamPage /> },
    // Off the rail: "Tạo agent" opens the existing builder until P13 · Tạo agent từ mô tả exists.
    BUILDER:       { render: () => <AgentRuntimeView /> },
    RUNS:          { render: () => <RunsPage /> },
    // /agents/run/<id>, under the "Lượt chạy" rail item.
    RUN:           { render: () => <RunDetailPage /> },
    EVALUATE:      { render: () => <AgentPlayground /> },
    ORCHESTRATION: { render: () => <WorkflowEngine /> },
  },

  analysis: {
    [SINGLE_SCREEN]: { render: () => <AnalysisPage /> },
    ASK:       { render: () => <AskDataPage /> },
    SESSIONS:  { render: () => <Conversations /> },
    DASHBOARD: { render: () => <ExecutiveDashboard /> },
  },

  data: {
    [SINGLE_SCREEN]: { render: () => <DataPage /> },
    // Sources, uploads and collections until P04/P05 are rebuilt.
    SOURCES:         { render: () => <KnowledgeOpsCenter /> },
  },

  admin: {
    [SINGLE_SCREEN]: { render: () => <AdminPage /> },
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
