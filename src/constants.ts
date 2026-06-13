import React from 'react';
import {
  LayoutDashboard,
  Database,
  Bot,
  Activity,
  ShieldCheck,
  Settings,
  Globe,
  Stethoscope,
  TrainFront,
  Building2,
  Lock,
  Network,
  Layers,
  BarChart3,
  Server,
  Zap,
  History,
  FileText,
  Scale,
  Fingerprint,
  Sparkles,
  Hash,
  ShieldAlert,
  Rocket,
  AlertTriangle,
  RefreshCcw,
  Cpu,
  LayoutGrid,
  Users,
  Terminal,
  Sliders,
  Plus,
  Package,
  GitBranch,
  Flag,
  Coins,
  Key,
  Boxes,
  CircleDot,
  Eye,
  ShieldHalf,
  BookOpen,
  Code,
  MessageSquare,
  MessageCircle,
  ScrollText,
} from 'lucide-react';
import { Role, Industry, NavItem } from './types';

export const NAV_ITEMS: NavItem[] = [
  // Core Modules
  { id: 'dashboard',          label: 'Executive Pulse',      icon: LayoutDashboard, roles: ['EXECUTIVE', 'PLATFORM_ADMIN'] },
  { id: 'knowledge-ops',      label: 'Knowledge Base',       icon: Database,        roles: ['PLATFORM_ADMIN', 'AI_ENGINEER'] },
  { id: 'ai-runtime',         label: 'AI Runtime',           icon: Bot,             roles: ['AI_ENGINEER', 'PLATFORM_ADMIN'] },
  { id: 'operations-center',  label: 'Operations Center',    icon: Activity,        roles: ['PLATFORM_ADMIN', 'AI_ENGINEER'] },
  { id: 'release-management', label: 'Deployment Center',    icon: Zap,             roles: ['AI_ENGINEER', 'PLATFORM_ADMIN'] },
  { id: 'governance',         label: 'Governance & Audit',   icon: ShieldCheck,     roles: ['PLATFORM_ADMIN', 'EXECUTIVE'] },

  // Industry Specific
  { id: 'national-ops', label: 'National Ops', icon: Globe, roles: ['EXECUTIVE', 'PLATFORM_ADMIN'], industry: 'GOVERNMENT' },

  // System
  { id: 'settings', label: 'Settings', icon: Settings, roles: ['PLATFORM_ADMIN', 'AI_ENGINEER', 'EXECUTIVE'] },
];

export interface SubNavItem {
  id: string;
  label: string;
  icon: any;
  badge?: number | string;
  comingSoon?: boolean;
  action?: boolean;
}

/* Contextual sub-nav items shown in the secondary panel per module.
 * IDs MUST match the sub-tab IDs used inside each center component
 * so secondary-nav clicks correctly drive the in-module state.
 */
export const MODULE_SUB_ITEMS: Record<string, SubNavItem[]> = {
  'dashboard': [],

  'knowledge-ops': [
    { id: 'FLEET',     label: 'Fleet Overview', icon: Database },
    { id: 'INVENTORY', label: 'Data Layers',    icon: Layers },
    { id: 'KNOWLEDGE', label: 'Knowledge Hub',  icon: Network },
    { id: 'CONFLICTS', label: 'Conflicts',      icon: ShieldAlert, badge: 12 },
    { id: 'POLICY',    label: 'Policies',       icon: ShieldCheck },
    { id: 'ACTION_INGEST',    label: 'Add Document',  icon: Plus, action: true },
    { id: 'ACTION_WAREHOUSE', label: 'Add Warehouse', icon: Package, action: true },
  ],

  'ai-runtime': [
    { id: 'PLAYGROUND',    label: 'Playground',      icon: MessageSquare },
    { id: 'AGENTS',        label: 'Agent Registry',  icon: Bot },
    { id: 'NEW_AGENT',     label: 'New Agent',       icon: Plus },
    { id: 'WORKFLOWS',     label: 'Workflow Engine', icon: Layers },
    { id: 'CONVERSATIONS', label: 'Conversations',   icon: MessageCircle },
    { id: 'LOGS',          label: 'Logs',            icon: ScrollText },
    { id: 'CLI',           label: 'CLI',             icon: Terminal },
    { id: 'CONFIG',        label: 'Config Registry', icon: Sliders },
    { id: 'TRACES',        label: 'Recent Traces',   icon: History },
    { id: 'PROVISION',     label: 'Quick Provision', icon: Zap },
    { id: 'RUNS',          label: 'Run Registry',    icon: Activity },
  ],

  'operations-center': [
    { id: 'HEALTH',        label: 'Platform Health',  icon: Activity },
    { id: 'OBSERVABILITY', label: 'Metric Explorer',  icon: BarChart3 },
    { id: 'FLEET',         label: 'Worker Fleet',     icon: Server },
    { id: 'JOBS',          label: 'Migration Jobs',   icon: Zap },
  ],

  'release-management': [
    { id: 'DEPLOYMENTS', label: 'Deployments',    icon: Rocket     },
    { id: 'HISTORY',     label: 'Release History', icon: History    },
    { id: 'ROLLBACK',    label: 'Rollback Center', icon: RefreshCcw },
  ],

  'governance': [
    { id: 'GOVERNANCE', label: 'Policy Control',        icon: ShieldCheck },
    { id: 'AUDIT',      label: 'Audit Logs',            icon: History },
    { id: 'SECURITY',   label: 'Security Intelligence', icon: Fingerprint },
    { id: 'COMPLIANCE', label: 'Compliance Reports',    icon: FileText },
  ],

  'national-ops': [],

  'settings': [
    { id: 'OVERVIEW',      label: 'Overview',         icon: LayoutGrid },
    { id: 'ORGANIZATION',  label: 'Organization',     icon: Building2 },
    { id: 'WORKSPACES',    label: 'Workspaces',       icon: Boxes },
    { id: 'IAM',           label: 'Users & Roles',    icon: Users },
    { id: 'AUTH',          label: 'SSO',              icon: Lock },
    { id: 'RBAC',          label: 'RBAC',             icon: ShieldHalf },
    { id: 'AI_PROVIDERS',  label: 'AI Providers',     icon: Cpu },
    { id: 'MODEL_REG',     label: 'Model Registry',   icon: Boxes },
    { id: 'SECRETS',       label: 'Secrets',          icon: Key },
    { id: 'SECURITY',      label: 'Security',         icon: ShieldCheck },
    { id: 'BILLING',       label: 'Billing & Quotas', icon: Coins },
    { id: 'API',           label: 'API & SDK',        icon: Code },
    { id: 'FLAGS',         label: 'Feature Flags',    icon: Flag },
  ],
};

export const INDUSTRIES = [
  { id: 'GENERAL',    label: 'Cross-Industry',     icon: LayoutGrid },
  { id: 'GOVERNMENT', label: 'Gov & Public',       icon: Building2 },
  { id: 'BANKING',    label: 'Financial Services', icon: Lock },
  { id: 'RAILWAY',    label: 'Railway & Metro',    icon: TrainFront },
  { id: 'HEALTHCARE', label: 'Medical & Health',   icon: Stethoscope },
];

export const ROLES = [
  { id: 'PLATFORM_ADMIN', label: 'Platform Admin' },
  { id: 'AI_ENGINEER',    label: 'AI Engineer' },
  { id: 'EXECUTIVE',      label: 'Executive' },
];

export const ENVIRONMENTS: { id: string }[] = [
  { id: 'DEV' },
  { id: 'UAT' },
  { id: 'STAGING' },
  { id: 'PROD' },
];
