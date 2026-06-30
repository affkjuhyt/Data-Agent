export type AgentDomain = "gov" | "enterprise" | "stockllm" | "forecast" | "document"

export interface MCPTool {
  id: string
  name: string
  description: string
  domain: AgentDomain[]
  category: "data" | "gov" | "analysis" | "output"
  icon: string
  isDefault: boolean
  isSensitive?: boolean
}

export type ArtifactType = "markdown" | "html" | "code" | "chart" | "table" | "pdf" | "graph" | "image"

export type GraphNodeType = "database" | "table" | "image" | "document" | "entity" | "api" | "stream"

export interface GraphNodeData {
  label: string
  nodeType: GraphNodeType
  description?: string
  status?: "active" | "inactive" | "error"
  properties?: Record<string, string>
  [key: string]: unknown
}

export interface GraphEdgeData {
  label?: string
  [key: string]: unknown
}

export interface GraphData {
  nodes: Array<{ id: string; type?: string; position: { x: number; y: number }; data: GraphNodeData }>
  edges: Array<{ id: string; source: string; target: string; label?: string; animated?: boolean }>
}

export interface SchemaColumn {
  name: string
  type: string
  nullable: boolean
  pk?: boolean
  fk?: string
  description?: string
}

export interface SchemaTable {
  name: string
  rowCount?: string
  columns: SchemaColumn[]
}

export interface ChartData {
  type: "bar" | "line" | "pie"
  title: string
  labels: string[]
  values: number[]
  unit?: string
}

export interface Artifact {
  id: string
  type: ArtifactType
  title: string
  content: string
  language?: string
  graphData?: GraphData
  chartData?: ChartData
  imageUrl?: string
  imageAlt?: string
  schemaData?: SchemaTable[]
  createdAt: Date
  messageId: string
}

export type InputMode = "text" | "sql" | "python" | "file" | "voice"
export type MessageRole = "user" | "assistant"
export type AgentStepStatus = "pending" | "running" | "done" | "error"
export type DataSourceStatus = "connected" | "disconnected" | "slow"

export interface AgentSkill {
  id: string
  name: string
  description: string
  promptTemplate: string
  icon: string
  color: string
  domain: AgentDomain[]
  category: "output" | "behavior" | "language" | "analysis"
}

export interface AgentStep {
  id: string
  workerName: string
  description: string
  status: AgentStepStatus
  durationMs?: number
  detail?: string
}

export interface Citation {
  source: string
  type: "mcp" | "neo4j" | "sql" | "document"
}

export interface HITLAction {
  id: string
  label: string
  variant: "confirm" | "cancel" | "preview"
}

export interface HITLChoice {
  id: string
  label: string
  description?: string
  badge?: string
}

export interface Message {
  id: string
  role: MessageRole
  content: string
  timestamp: Date
  agentTrace?: AgentStep[]
  chartData?: ChartData
  tableData?: { headers: string[]; rows: (string | number)[][] }
  hitl?: {
    title: string
    description: string
    actions: HITLAction[]
    expiresAt?: Date
    choices?: HITLChoice[]
  }
  citations?: Citation[]
  isStreaming?: boolean
}

export interface Conversation {
  id: string
  title: string
  domain: AgentDomain
  messages: Message[]
  createdAt: Date
  updatedAt: Date
  activeAgents?: string[]
}

export type ProjectFileType = "docx" | "pdf" | "md" | "txt" | "csv" | "other"

export interface ProjectFile {
  id: string
  name: string
  type: ProjectFileType
  lines?: number
  sizeKb?: number
  uploadedAt: Date
  status?: "ready" | "indexing" | "error"
}

export interface Project {
  id: string
  name: string
  description?: string
  color: string
  conversationIds: string[]
  memory?: string
  instructions?: string
  files?: ProjectFile[]
  createdAt: Date
  updatedAt: Date
}

export interface DataSource {
  id: string
  name: string
  type: "warehouse" | "graph" | "realtime" | "document"
  status: DataSourceStatus
}

export interface ActiveAgent {
  id: string
  name: string
  description: string
  status: AgentStepStatus
}
