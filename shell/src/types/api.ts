/**
 * API types - mirrors docs/api/master-rest.openapi.yaml
 * NOTE: This is the single source of truth for the frontend.
 * Any change to the OpenAPI spec must be reflected here.
 */

// ---------- Generic ----------

export interface ErrorBody {
  code: ErrorCode;
  message: string;
  retryable: boolean;
}

export interface ErrorResponse {
  ok: false;
  error: ErrorBody;
}

export interface OkResponse {
  ok: true;
}

export type ErrorCode =
  | 'DEVICE_OFFLINE'
  | 'APP_NOT_FOUND'
  | 'SELECTOR_NOT_FOUND'
  | 'TIMEOUT'
  | 'IME_ERROR'
  | 'DRAFT_FAILED'
  | 'UPLOAD_FAILED'
  | 'RISK_BLOCKED'
  | 'RATE_LIMITED'
  | 'PARSE_FAILED'
  | 'INVALID_PARAMS'
  | 'INTERNAL_ERROR'
  | string; // forward-compat for unknown codes

// ---------- Health ----------

export type HealthStatus = 'ok' | 'degraded' | 'down';
export type HealthSubsystem = 'ok' | 'error' | 'connected' | 'disconnected';

export interface Health {
  status: HealthStatus;
  version: string;
  uptime_sec: number;
  db?: HealthSubsystem;
  tailscale?: HealthSubsystem;
}

// ---------- Device ----------

export type DeviceStatus = 'pending' | 'active' | 'offline' | 'tailscale_degraded' | 'disabled';

export interface Device {
  id: string;
  nickname: string;
  model: string;
  android_version: string;
  apk_version?: string;
  tailnet_ip?: string;
  tags?: string[];
  status: DeviceStatus;
  last_heartbeat?: string;
  bound_accounts?: number;
}

export interface DeviceRegisterRequest {
  nickname: string;
  model: string;
  android_version: string;
  apk_version: string;
  tailnet_ip: string;
  adb_serial?: string;
}

export interface DevicePairRequest {
  pair_code: string;
  hmac_key_id: string;
}

export interface DevicePairResponse {
  hmac_key: string;
}

// ---------- Account ----------

export type AccountStatus = 'pending' | 'active' | 'suspended' | 'banned' | 'disabled';

export interface Account {
  id: string;
  handle: string;
  persona_id?: string;
  device_id?: string;
  status: AccountStatus;
  last_active?: string;
  risk_score: number;
}

export interface AccountCreate {
  handle: string;
  device_id: string;
  persona_id: string;
}

// ---------- Persona ----------

export interface Persona {
  id: string;
  name: string;
  tone: string;
  style_guide: string;
  forbidden_words?: string[];
  sample_note_ids?: string[];
  version: number;
}

export interface PersonaCreate {
  name: string;
  tone: string;
  style_guide: string;
  forbidden_words?: string[];
  sample_note_ids?: string[];
}

// ---------- Note ----------

export type NoteStatus =
  | 'draft'
  | 'reviewing'
  | 'scheduled'
  | 'publishing'
  | 'published'
  | 'failed'
  | 'deleted';

export interface Note {
  id: string;
  account_id: string;
  title: string;
  content: string;
  images?: string[];
  tags?: string[];
  status: NoteStatus;
  platform_note_id?: string;
  platform_url?: string;
  scheduled_at?: string;
  published_at?: string;
}

// ---------- Goal ----------

export type GoalStatus = 'active' | 'achieved' | 'failed' | 'cancelled';

export type GoalType =
  | 'publish_note'
  | 'interact'
  | 'collect_metrics'
  | 'warmup'
  | 'login'
  | 'natural_language'
  | 'generic';

/**
 * 结构化主题对象：chat LLM 多轮对话收敛出的"主题 + 人群 + 商品类目"。
 * 所有字段可选，前端按字段缺失降级展示。
 * 允许任意额外字段透传（LLM 创造性输出）。
 */
export interface ThemeTarget {
  theme?: string;
  audience?: string;
  product_category?: string;
  persona_id?: string;
  goal_type?: string;
  extra?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Goal {
  id: string;
  type: GoalType;
  target: ThemeTarget | Record<string, unknown>;
  deadline?: string;
  status: GoalStatus;
}

export interface GoalCreate {
  type: GoalType;
  target: ThemeTarget | Record<string, unknown>;
  deadline?: string;
}

// ---------- Agent Run ----------

export type AgentRunStatus = 'running' | 'success' | 'failed' | 'cancelled' | 'timeout';
export type AgentState =
  | 'IDLE'
  | 'RESEARCH'
  | 'DRAFT'
  | 'REVIEW'
  | 'REVISE'
  | 'SCHEDULE'
  | 'DISPATCH'
  | 'PUBLISH'
  | 'COLLECT'
  | 'ANALYZE'
  | 'ALERT';

export interface AgentRun {
  id: string;
  goal_id?: string;
  current_state: AgentState | string;
  status: AgentRunStatus;
  started_at: string;
  updated_at?: string;
  ended_at?: string;
  /** 主题摘要（仅展示用，agent_runs.payload.brief 透传） */
  brief?: ThemeTarget | Record<string, unknown>;
}

// ---------- Metrics ----------

export interface MetricsSummary {
  devices?: {
    total?: number;
    active?: number;
    offline?: number;
  };
  accounts?: {
    total?: number;
    active?: number;
    high_risk?: number;
  };
  tasks?: {
    pending?: number;
    running?: number;
    success_24h?: number;
    failed_24h?: number;
  };
  llm_cost_24h_usd?: number;
}

// ---------- Chat ----------

export interface ChatHistoryMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatRequest {
  message: string;
  history?: ChatHistoryMessage[];
  session_id?: string;
}

export interface ChatAction {
  type: string;
  payload?: Record<string, unknown>;
}

export interface ChatResponse {
  reply: string;
  theme_confirmed?: boolean;
  theme_payload?: ThemeTarget | Record<string, unknown> | null;
  action?: ChatAction;
}

// ---------- List envelopes ----------

export interface ListResponse<T> {
  items: T[];
  total?: number;
}

// ---------- Alerts ----------

export type AlertSeverity = 'critical' | 'warning' | 'info';

export interface AlertItem {
  id: string;
  code: string;
  severity: AlertSeverity;
  message: string;
  subject_id?: string;
  resolved: boolean;
  created_at: string;
  resolved_at?: string;
}

export type KbType =
  | 'brand'
  | 'persona'
  | 'rule'
  | 'topic'
  | 'history'
  | 'template'
  | 'product';

export interface KbDocument {
  id: string;
  type: KbType;
  ref_id?: string;
  title?: string;
  content: string;
  metadata: Record<string, unknown>;
  version: number;
  is_published: boolean;
  created_at: string;
  updated_at: string;
}

export interface KbDocumentCreate {
  type: KbType;
  content: string;
  title?: string;
  ref_id?: string;
  metadata?: Record<string, unknown>;
  is_published?: boolean;
}

export interface KbDocumentUpdate {
  content?: string;
  title?: string;
  ref_id?: string;
  metadata?: Record<string, unknown>;
  is_published?: boolean;
}

export interface KbSearchRequest {
  query: string;
  type: KbType;
  top_k?: number;
  filters?: Record<string, unknown>;
}

export interface KbSearchHit {
  chunk_id: string;
  doc_id: string;
  doc_type: KbType;
  doc_title?: string;
  chunk_index: number;
  text: string;
  score: number;
  sources: string[];
  metadata: Record<string, unknown>;
}
