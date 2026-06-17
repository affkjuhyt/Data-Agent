import {
  Database,
  Bot,
  Activity,
  Settings,
  Stethoscope,
  TrainFront,
  Building2,
  Lock,
  BarChart3,
  History,
  Rocket,
  Cpu,
  LayoutGrid,
  Users,
  Package,
  GitBranch,
  Coins,
  Key,
  MessageSquare,
  MessageCircle,
  ScrollText,
  FlaskConical,
  Route,
  Wrench,
  ClipboardCheck,
  PlugZap,
  Webhook,
} from 'lucide-react';
import { Role, NavItem } from './types';

const ALL_ROLES: Role[] = ['EXECUTIVE', 'PLATFORM_ADMIN', 'AI_ENGINEER'];

export const NAV_ITEMS: NavItem[] = [
  { id: 'build',    label: 'Build',    icon: Package,      roles: ALL_ROLES },
  { id: 'test',     label: 'Test',     icon: FlaskConical, roles: ALL_ROLES },
  { id: 'release',  label: 'Release',  icon: Rocket,       roles: ALL_ROLES },
  { id: 'monitor',  label: 'Monitor',  icon: Activity,     roles: ALL_ROLES },
  { id: 'settings', label: 'Settings', icon: Settings,     roles: ALL_ROLES },
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
  'build': [
    { id: 'AGENTS',    label: 'Agents',    icon: Bot },
    { id: 'ROUTER',    label: 'Router',    icon: Route,    comingSoon: true },
    { id: 'KNOWLEDGE', label: 'Knowledge', icon: Database },
    { id: 'TOOLS',     label: 'Tools',     icon: Wrench,   comingSoon: true },
  ],

  'test': [
    { id: 'PLAYGROUND', label: 'Playground', icon: MessageSquare },
    { id: 'EVALS',      label: 'Evals',      icon: ClipboardCheck, comingSoon: true },
  ],

  'release': [
    { id: 'VERSIONS',    label: 'Versions',      icon: GitBranch },
    { id: 'EXPERIMENTS', label: 'Experiments',   icon: FlaskConical, comingSoon: true },
    { id: 'ENDPOINT',    label: 'Chat Endpoint', icon: PlugZap,      comingSoon: true },
    { id: 'WEBHOOKS',    label: 'Webhooks',      icon: Webhook,      comingSoon: true },
  ],

  'monitor': [
    { id: 'CONVERSATIONS', label: 'Conversations', icon: MessageCircle },
    { id: 'TRACES',        label: 'Traces',        icon: History },
    { id: 'ANALYTICS',     label: 'Analytics',     icon: BarChart3 },
  ],

  'settings': [
    { id: 'TEAM',      label: 'Team & Access', icon: Users },
    { id: 'PROVIDERS', label: 'AI Providers',  icon: Cpu },
    { id: 'SECRETS',   label: 'Secrets',       icon: Key },
    { id: 'BILLING',   label: 'Billing',       icon: Coins },
    { id: 'AUDIT',     label: 'Audit Log',     icon: ScrollText },
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
