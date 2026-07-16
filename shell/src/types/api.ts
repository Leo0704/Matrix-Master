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

// ---------- Business（v0.7+ 业务模型重构） ----------

export type BusinessStatus = 'active' | 'archived';

export interface Business {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  status: BusinessStatus;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

export interface BusinessCreate {
  name: string;
  slug: string;
  description?: string;
}

export interface BusinessUpdate {
  /** 局部更新；status 不暴露（用 /archive /unarchive 端点） */
  name?: string;
  slug?: string;
  description?: string;
}

export interface BusinessListResponse {
  items: Business[];
  total: number;
}

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
  // P2-3：4 字段全 Optional —— APK 上线前回填前都是 null/undefined
  model?: string;
  android_version?: string;
  apk_version?: string;
  tailnet_ip?: string;
  tags?: string[];
  status: DeviceStatus;
  last_heartbeat?: string;
  bound_accounts?: number;
  /** 绑定账号的 handle（严格 1 机 1 账号下最多一个） */
  bound_account_handle?: string | null;
  pair_code?: string;
  /** v0.7+ 业务归属 */
  business_id: string;
}

// P2-3：注册时只填 nickname + 可选 adb_serial；其余 4 字段由 APK 配对时自动回填
export interface DeviceRegisterRequest {
  nickname: string;
  adb_serial?: string;
  /** v0.7+ 业务归属（必填） */
  business_id: string;
}

export interface DevicePairRequest {
  pair_code: string;
  identity?: {
    model?: string;
    android_version?: string;
    apk_version?: string;
    tailnet_ip?: string;
  };
}

export interface DevicePairResponse {
  key_id: string;
  hmac_key: string;
}

// ---------- Account ----------

export type AccountStatus = 'pending' | 'active' | 'suspended' | 'banned' | 'disabled';

export interface Account {
  id: string;
  handle: string;
  persona_id?: string;
  device_id?: string;
  /** v0.7+ 业务归属：账号绑死业务，换业务=起新号 */
  business_id: string;
  status: AccountStatus;
  last_active?: string;
  risk_score: number;
}

export interface AccountCreate {
  handle: string;
  device_id: string;
  persona_id: string;
  /** v0.7+ 业务归属（必填） */
  business_id: string;
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
  /** v0.7+ 业务归属：人设绑死业务，跨业务允许重名 */
  business_id: string;
}

export interface PersonaCreate {
  name: string;
  tone: string;
  style_guide: string;
  forbidden_words?: string[];
  sample_note_ids?: string[];
  /** v0.7+ 业务归属（必填） */
  business_id: string;
}

// ---------- 账号内容表现（数据看板核心指标） ----------

export interface AccountContentStats {
  account_id?: string | null;
  handle: string;
  status: string;
  /** 关联设备昵称（严格 1 机 1 账号下，每个账号对应一台设备） */
  device_nickname?: string | null;
  total_notes: number;
  published: number;
  draft: number;
  scheduled: number;
  avg_views: number;
  avg_likes: number;
  avg_comments: number;
}

export interface AccountContentStatsResponse {
  items: AccountContentStats[];
}

// v0.7+ 多业务对比（dashboard 第 4 期）
export interface BusinessComparisonRow {
  business_id: string;
  business_name: string;
  business_slug: string;
  status: 'active' | 'archived';
  devices: number;
  accounts: number;
  personas: number;
  goals: number;
  notes: number;
  published_notes: number;
  kb_documents: number;
  agent_runs: number;
  successful_runs: number;
  notes_per_account: number;
}

export interface BusinessComparisonResponse {
  items: BusinessComparisonRow[];
  total_businesses: number;
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
  /** v0.7 Phase 5：草稿阶段还没绑账号，account_id 可空（DISPATCH 成功后 publish_node 填上） */
  account_id?: string | null;
  title: string;
  content: string;
  images?: string[];
  tags?: string[];
  status: NoteStatus;
  platform_note_id?: string;
  platform_url?: string;
  scheduled_at?: string;
  published_at?: string;
  /** Phase 1 P1-1：发布成功后由 publish_node 写入 = now + 24h */
  scheduled_collect_at?: string;
  /** Phase 1 P1-1：collect 成功后由 _do_collect 填 */
  collected_at?: string;
  collected_run_id?: string;
  /** v0.7+ 业务归属 */
  business_id: string;
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
  // v0.7 第 1 期：orchestrator 状态机字段
  phase?: GoalPhase;
  current_round?: number;
  max_rounds?: number;
  target_likes?: number;
  notes_per_round?: number;
  learning_summary?: string | null;
  phase_updated_at?: string | null;
  /** v0.7+ 业务归属 */
  business_id: string;
}

export type GoalPhase =
  | 'PENDING'
  | 'PREPARING'
  | 'EXECUTING'
  | 'MONITORING'
  | 'SUMMARIZING'
  | 'DECIDING'
  | 'DONE';

export interface GoalRound {
  id: string;
  goal_id: string;
  round_number: number;
  started_at: string;
  ended_at?: string | null;
  kpi_summary: Record<string, unknown>;
  notes_created: number;
  total_views: number;
  total_likes: number;
  created_at: string;
  updated_at: string;
}

export interface GoalCreate {
  type: GoalType;
  target: ThemeTarget | Record<string, unknown>;
  deadline?: string;
  // v0.7 第 1 期优化：可调字段（不传用后端 default）
  target_likes?: number;
  notes_per_round?: number;
  max_rounds?: number;
  /** v0.7+ 业务归属（必填） */
  business_id: string;
}

export interface GoalUpdate {
  type?: GoalType;
  target?: ThemeTarget | Record<string, unknown>;
  deadline?: string;
  /** 停止目标：active → cancelled / failed；后端会用 enum 校验 */
  status?: GoalStatus;
  target_likes?: number;
  notes_per_round?: number;
  max_rounds?: number;
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

// 状态机状态名 → 中文大白话（运营者看的）。后端逻辑不动，前端渲染用。
export const STATE_LABELS: Record<AgentState, string> = {
  IDLE: '空闲',
  RESEARCH: '找资料',
  DRAFT: '写草稿',
  REVIEW: '检查草稿',
  REVISE: '改稿',
  SCHEDULE: '排时间',
  DISPATCH: '派给手机',
  PUBLISH: '发布',
  COLLECT: '收数据',
  ANALYZE: '分析',
  ALERT: '告警',
};

export function formatState(state: string): string {
  return (STATE_LABELS as Record<string, string>)[state] ?? state;
}

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
  /** v0.7+ 业务归属 */
  business_id?: string;
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
  /** v0.7+ 业务归属（必填；缺则 422） */
  business_id: string;
}

/** 与后端 matrix.api.schemas.chat.ChatActionType 一一对应。
 *  前端按 type 分支渲染（ask_data → 表格，preview_change → 确认弹窗，等）。
 */
export type ChatActionKind =
  // === 正常场景 ===
  | 'ask_data'
  | 'diagnose'
  | 'preview_change'
  | 'apply_change'
  | 'browse_kb'
  | 'chitchat'
  // === 控制类 ===
  | 'noop'
  // === 错误兜底 ===
  | 'llm_error'
  | 'parse_error'
  | 'unknown_intent'
  | 'missing_args'
  | 'batch_too_large'
  | 'partial_success';

export interface ChatAction {
  type: ChatActionKind;
  payload?: Record<string, unknown>;
  /** 仅 preview_change 为 true；前端必须显示"确认/取消"按钮 */
  needs_confirmation?: boolean;
  /** 后端生成的 UUID；前端用 `/confirm <token>` 触发 apply_change */
  confirmation_token?: string;
}

export interface ChatResponse {
  reply: string;
  action?: ChatAction;
  /** 透传 ChatAction.confirmation_token */
  confirmation_token?: string;
  /** 错误类的可读补充，UI 直接展示 */
  error_hint?: string;
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
  /** v0.7+ 业务归属（018 migration 加列；可选字段，向前兼容） */
  business_id?: string;
}

// ---------- Notifications (Phase 1 反向反馈) ----------

export type NotificationSeverity = 'info' | 'success' | 'warning' | 'error';

export interface NotificationItem {
  id: string;
  recipient: string;
  code: string;
  severity: NotificationSeverity;
  title: string;
  body: string;
  goal_id?: string;
  run_id?: string;
  note_id?: string;
  device_id?: string;
  payload: Record<string, unknown>;
  read_at?: string;
  created_at: string;
  /** v0.7+ 业务归属（可选；015/017 migration 加列） */
  business_id?: string;
}

export interface NotificationListResponse {
  items: NotificationItem[];
  total: number;
}

export type KbType =
  | 'brand'
  | 'persona'
  | 'rule'
  | 'history'
  | 'strategy_card';

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
  /** v0.7+ 业务归属 */
  business_id: string;
}

export interface KbDocumentCreate {
  type: KbType;
  content: string;
  title?: string;
  ref_id?: string;
  metadata?: Record<string, unknown>;
  is_published?: boolean;
  /** v0.7+ 业务归属（必填） */
  business_id: string;
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

export interface ViralIngestRequest {
  raw_text: string;
  title?: string;
  metrics?: Record<string, number>;
}

export interface ViralIngestResponse {
  history: KbDocument;
  strategy_card_pending: boolean;
}
