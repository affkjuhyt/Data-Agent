import React from 'react';

import AskDataPage from './features/ask/AskDataPage';
import KnowledgeOpsCenter from './components/knowledge/KnowledgeOpsCenter';
import AgentPlayground from './components/agent-builder/AgentPlayground';
import Conversations from './components/agent-builder/Conversations';
import ReleaseManagementCenter from './components/ReleaseManagementCenter';
import ReviewCenter from './components/agent-builder/ReviewCenter';
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
    VERSIONS:   { render: () => <ReleaseManagementCenter /> },
    PLAYGROUND: { render: () => <AgentPlayground /> },
  },

  ops: {
    CONVERSATIONS: { render: () => <Conversations /> },
    TRACES:        { render: () => <TraceExplorer /> },
    REVIEWS:       { render: () => <ReviewCenter /> },
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
