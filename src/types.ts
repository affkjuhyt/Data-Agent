import type {
  Tier,
  DocStatus,
} from './lib/enums';

export type Role = 'PLATFORM_ADMIN' | 'AI_ENGINEER' | 'EXECUTIVE';
export type Industry = 'GENERAL' | 'GOVERNMENT' | 'BANKING' | 'RAILWAY' | 'HEALTHCARE';
export type Environment = 'DEV' | 'UAT' | 'STAGING' | 'PROD';

export interface PagedResponse<T> {
  items: T[];
  page: number;
  page_size: number;
  has_next: boolean;
}

// Canonical layer values match the postgres KBTier enum (lowercase).
export type KnowledgeLayer = Tier;
export type { DocStatus };

export type ConnectorStatus = 'HEALTHY' | 'SYNCING' | 'ERROR' | 'PAUSED';
export type ConnectorType = 'DOCUMENT' | 'ENTERPRISE' | 'COMMUNICATION' | 'DATABASE' | 'WEB' | 'MEDIA';

export interface KBConnector {
  id: string;
  name: string;
  type: ConnectorType;
  status: ConnectorStatus;
  lastSync: string;
  volume: string;
  health: number; // 0-100
  category: string;
}

export interface TenantContext {
  organization: string;
  workspace: string;
  project: string;
  environment: Environment;
}

export interface User {
  id: string;
  name: string;
  email: string;
  role: Role;
  avatar?: string;
}

export interface KnowledgeDocument {
  data_id: string;
  name: string;
  source_type: string;          // doc | web | image | video | warehouse
  extension?: string | null;
  current_tier: KnowledgeLayer; // bronze | silver | gold
  status: DocStatus;            // derived server-side from current_tier
  added_by?: string | null;     // user_id of uploader
  added_on?: string | null;     // timestamp
  abstract?: string | null;
  metadata: {
    doc_type?: string | null;   // computed from source_type + extension
    language?: string | null;
    access_role?: string | null;
    url?: string | null;
    author?: string | null;     // document's true author — NOT the uploader
    published_date?: string | null;
    warehouse_type?: string | null;
    width?: number | null;
    height?: number | null;
    color_space?: string | null;
    file_size?: number | null;
    total_frame?: number | null;
  };
  score?: number;
  conflicts?: boolean;
}

export interface IngestionJob {
  id: string;
  name: string;
  startTime: string;
  progress: number;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED' | 'QUEUED';
  type: 'EMBEDDING' | 'OCR' | 'GRAPH_SYNC' | 'CONNECTOR_SYNC';
  priority: 'HIGH' | 'NORMAL' | 'LOW';
}

export interface NavItem {
  id: string;
  label: string;
  icon: any;
  roles: Role[];
  industry?: Industry;
}
