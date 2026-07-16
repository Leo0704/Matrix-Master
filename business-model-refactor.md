# 矩阵项目业务层重构 实施文档

> 本文档描述把"业务"作为项目核心聚合根的完整数据模型与代码改造方案。
> 实施时按文档分期逐步交付，每期都有明确的验收标准。

---

## 一、背景与核心约束

老板经过仔细推敲，发现当前数据模型有 4 条根本性约束没体现：

1. **业务是项目根** —— 不是 goal 主题级。整个项目围绕一个业务（不是多业务并跑），goal 是业务的**阶段容器**（如"测爆款/放量/复盘"），不是独立主题。
2. **账号是业务的载体，且一旦起号绑死** —— 小红书一个账号天然只能做一个业务方向。换业务 ≠ 改 `account.business_id`（旧号不能"变"），换业务 = **新建 Business + 新起账号**。
3. **业务不频繁切换但要支持归档** —— 同时只有一个 active，但已归档业务可以长期保留供历史查询；FK 软归档天然兼容。
4. **当前数据模型完全没体现这 4 条** —— 25 张表（实际 26 张）都是平级独立，没有"业务"这个聚合根。账号 / 设备 / persona / goal / note / kb 全部挂在全局 pool 里。

**附带修复一个生产 bug**：`backend/matrix/api/_agent_factory.py:139-142` 没注入 `DefaultRoundSlotAllocator`，导致 orchestrator 永远走 fallback 路径。修业务模型同时把这个 bug 一起补了。

**改造范围**：26 → 27 张表（+1 Business）；7 张核心表加 `business_id` NOT NULL；所有路由层校验业务上下文；chat 鉴权链路新增；前端业务切换器 + 管理页。

---

## 二、关键决策（已确定）

| 问题 | 选择 | 理由 |
|---|---|---|
| Business 归档语义 | **软归档**（`status='archived'` + `archived_at`），不删行 | 历史资源保留只读可查；FK 只在硬 DELETE 触发，软归档自然兼容 |
| `business_id` nullable 节奏 | **3 步迁移**：015 加 nullable → 016 独立脚本回填 → 017 NOT NULL + FK | 回填失败可回退；migration 可重复 |
| archived 业务资源能否进 allocator pool | **不能**（pool 永远只看 `status='active'`） | 三层防御：allocator SQL + 路由层 + orchestrator 扫描 |
| archived 业务的 goal | 推进到当前 round DONE 即停（不再开新轮）；历史数据保留只读 | 保留复盘价值 |
| ChatRequest 缺 `business_id` | **422 拒绝**（不静默 fallback） | 默认 active 业务会让行为不可追溯、归档后越权 |
| 业务切换 UI | Topbar dropdown + ui-store；活跃业务在 `localStorage['matrix.business.active_id']` | 与现有 `ui-store.ts` 风格一致 |
| chat history localStorage | 按业务分区：`matrix.chat.messages.v1.<business_id>` | 切换业务看到不同上下文 |
| 回填 legacy 数据 | 独立 `scripts/backfill_business.py`，不放 migration | migration 必须可重放；回填是单次运营动作 |
| Persona UNIQUE 约束 | `UNIQUE(name)` → `UNIQUE(business_id, name)` | 跨业务允许重名（如 A 业务和 B 业务都有"平价学生党"人设） |
| `business_id` 可改性 | **创建后不可改**（app 层 PATCH schema 不暴露字段） | 老板明确说"换业务=账号死亡" |
| 修生产 bug 的时机 | **第 1 期一并修** | 同一个 commit 一起交付，避免中间态混乱 |
| 业务层 trigger | **先不做**（依赖路由层 + ORM 层）；未来真有合规再加 | 维护成本低 |

**默认建议**（老板可调整）：
- legacy 业务命名：`slug='legacy-default'` / `name='历史数据'`
- 业务切换器放 topbar（业务切换是高频操作）

---

## 三、Business 表设计

### 3.1 新 ORM 类 `Business`（`backend/matrix/db/models.py` 新增）

插在 `DeviceHeartbeat` 之后、`Account` 之前（约 line 130）：

```python
class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          server_default=sa_text("uuid_generate_v4()"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False,
                                        server_default=sa_text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=sa_text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=sa_text("NOW()"))
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="businesses_status_check"),
    )
```

**设计要点**：
- `id` 用 UUID（与现有所有表一致）
- `slug` 全局 UNIQUE（路由前缀 + 脚本引用锚点）
- `status` 二态：active / archived
- 不放 owner_id / tenant_id（单机工具，未来需要再加）

### 3.2 FK 关系总图（7 个新 FK）

| 从表 | 列 | FK 行为 | 理由 |
|---|---|---|---|
| `devices.business_id` | `Business.id` | `ON DELETE RESTRICT` | 业务归档不删业务本身 |
| `accounts.business_id` | `Business.id` | `ON DELETE RESTRICT` | 账号绑死业务 |
| `personas.business_id` | `Business.id` | `ON DELETE RESTRICT` | 人设绑死业务 |
| `goals.business_id` | `Business.id` | `ON DELETE RESTRICT` | 历史目标不可丢 |
| `notes.business_id` | `Business.id` | `ON DELETE RESTRICT` | 已发布笔记不可丢 |
| `kb_documents.business_id` | `Business.id` | `ON DELETE RESTRICT` | 经验卡是业务知识沉淀 |
| `agent_runs.business_id` | `Business.id` | `ON DELETE RESTRICT` | 执行轨迹不可丢 |

`ON DELETE RESTRICT` 是因为本方案永不物理删 business 行（只软归档）。

---

## 四、7 张核心表加 `business_id`

| 表 | nullable | 唯一约束变化 | 业务归档可改? |
|---|---|---|---|
| `devices.business_id` | NULLABLE → NOT NULL | UNIQUE(nickname) 不变；+ idx | **否**（schema 不暴露字段） |
| `accounts.business_id` | NULLABLE → NOT NULL | UNIQUE(handle) 不变；+ idx | 否 |
| `personas.business_id` | NULLABLE → NOT NULL | **`UNIQUE(name)` → `UNIQUE(business_id, name)`** | 否 |
| `goals.business_id` | NULLABLE → NOT NULL | + idx(business_id, phase) | 否 |
| `notes.business_id` | NULLABLE → NOT NULL | + idx(business_id, created_at) | 否 |
| `kb_documents.business_id` | NULLABLE → NOT NULL | + idx(business_id, type) | 否 |
| `agent_runs.business_id` | NULLABLE → NOT NULL | + idx(business_id, goal_id, round_number) | 否 |

**索引**（017 migration 创建）：
- `idx_goals_business_phase` ON `goals(business_id, phase)` WHERE `deleted_at IS NULL`
- `idx_notes_business_created` ON `notes(business_id, created_at DESC)` WHERE `deleted_at IS NULL`
- `idx_kb_documents_business_type` ON `kb_documents(business_id, type)` WHERE `deleted_at IS NULL`
- `idx_accounts_business_status` ON `accounts(business_id, status)` WHERE `deleted_at IS NULL`
- `idx_devices_business_status` ON `devices(business_id, status)` WHERE `deleted_at IS NULL`
- `idx_agent_runs_business_goal_round` ON `agent_runs(business_id, goal_id, round_number)`

**Persona UNIQUE 变更**：017 migration `ALTER TABLE personas DROP CONSTRAINT personas_name_key; ALTER TABLE personas ADD CONSTRAINT personas_business_id_name_key UNIQUE (business_id, name);`

---

## 五、三步迁移策略

### 5.1 迁移 015 — `015_add_businesses_table_and_nullable_business_id.py`
- **head**: `f1e2d3c4b5a6`（migration 链完全线性：`014 → d0a5fb51f30f → f1e2d3c4b5a6`，015 直接接 `f1e2d3c4b5a6`，**无需 merge revision**）
- **做**：
  1. 创建 `businesses` 表 + 索引 + CHECK
  2. 7 张表加 `business_id UUID` 列（**全部 NULLABLE，无 FK**）
- **downgrade**: 反向 DROP COLUMN + DROP TABLE

### 5.2 016 — `scripts/backfill_business.py`（**独立脚本，不放 alembic**）

**为什么独立**：migration 必须可重放；回填是单次运营动作。

**回填优先级**（伪代码）：
```python
# 1) 创建 legacy-default 业务（slug='legacy-default', active）
# 2) notes: account_id→accounts.business_id；空→goal_id→goals.business_id；
#         还空→run_id→agent_runs.business_id；30 条真孤儿→legacy-default
# 3) agent_runs: goal_id→goals.business_id；空→legacy-default
# 4) goals / accounts / devices / personas / kb_documents: 全部 legacy-default
# 5) 报告：每张表多少行被分配、legacy-default 各占多少
```

**前置门**（017 跑前必跑）：`scripts/backfill_business.py --verify` 模式检查 `SELECT count(*) FROM accounts WHERE business_id IS NULL` 等 7 张表，必须全 0。

### 5.3 迁移 017 — `017_business_id_not_null_and_constraints.py`
- **做**：
  1. 7 张表 `ALTER COLUMN business_id SET NOT NULL`
  2. 7 个 FK：`REFERENCES businesses(id) ON DELETE RESTRICT`
  3. 创建 6 个复合索引
  4. Persona 唯一约束切换（UNIQUE(business_id, name)）
  5. **顺手修 ORM/DB drift**：`agent_runs.goal_id` ORM 加 `ondelete="CASCADE"` 与 DB 对齐
- **不可逆**：017 跑完降级会丢数据完整性约束，README 强制写"017 跑前必须先 016 通过 verify"。

### 5.4 head 链更新

```
014_device_identity_nullable
  └─ d0a5fb51f30f_add_strategy_card_to_kb_documents_type_
      └─ f1e2d3c4b5a6_phase1_notifications_and_collect_at
          └─ 015_add_businesses_table_and_nullable_business_id
              └─ 017_business_id_not_null_and_constraints
```

**注**：migration 链是**完全线性**的，没有分叉（`f1e2d3c4b5a6.down_revision = "d0a5fb51f30f"`，`d0a5fb51f30f.down_revision = "014_device_identity_nullable"`），所以**不需要** merge revision。

016 不在迁移链上（独立脚本）。

---

## 六、修生产装配 bug（**第 1 期必做**）

### 6.1 Bug 位置

`backend/matrix/api/_agent_factory.py:139-142` 当前：
```python
if scheduler is None:
    from matrix.scheduler import DefaultSlotPicker
    scheduler = DefaultSlotPicker(session_factory)  # ← 只装 picker，没装 allocator
```

**修法**：同一处同时装 picker + allocator，并在 `build_agent_services(...)` 调用处追加 `round_allocator` 参数。完整修改如下：

```python
# 1. _agent_factory.py:139-142 块（装 allocator）
if scheduler is None:
    from matrix.scheduler import DefaultSlotPicker
    from matrix.scheduler.round_slot_allocator import DefaultRoundSlotAllocator

    picker = DefaultSlotPicker(session_factory)
    scheduler = picker
    round_allocator = DefaultRoundSlotAllocator(session_factory)

# 2. _agent_factory.py:162-173 build_agent_services 调用处（追加参数）
services = build_agent_services(
    llm=llm,
    kb_retriever=_LazyRetriever(session_factory, embedder),
    kb_writer=_LazyWriter(session_factory, embedder),
    device_adapter=ApkHttpClient(resolver=DeviceEndpointResolver(session_factory)),
    config=_LazyConfigReader(session_factory),
    task_writer=task_writer,
    note_writer=db_note_writer,  # v0.7 Phase 5：DRAFT 草稿直接落 notes 表
    scheduler=scheduler,
    notifier=notifier,
    llm_rate_limiter=llm_rate_limiter,
    round_allocator=round_allocator,  # ← 新增（第 1 期 bug 修复）
)
```

`AgentServices` schema 已支持该参数（`backend/matrix/agent/_services.py:71` 已有 `round_allocator: Any | None = None`）。

### 6.2 同时修 orchestrator 的 N 计算不一致（`orchestrator.py:146`）
- **当前主路径**（`orchestrator.py:146`）：`n = min(active_devices, DEFAULT_MAX_ROUND_FANOUT)`（只看设备数和上限，**不看 `notes_per_round`**）
- **当前降级路径**（`_count_target_for_round(goal)`，`orchestrator.py:115-117`）：`n = min(goal.notes_per_round, DEFAULT_MAX_ROUND_FANOUT)`（看 `notes_per_round`，但不看 active 数量）
- **改后统一**（主路径也走 `_count_target_for_round`）：`n = min(_count_target_for_round(goal), active_devices, DEFAULT_MAX_ROUND_FANOUT)`
  - 即：`n_target = _count_target_for_round(goal)` → `n = min(n_target, active_devices, DEFAULT_MAX_ROUND_FANOUT)`
  - 这样降级路径仍是 `_count_target_for_round(goal)`（行为不变），主路径与降级路径用同一个目标函数，差异只在主路径额外受 active_devices 约束

---

## 七、allocator + slot_picker 改造

### 7.1 `DefaultRoundSlotAllocator`（`round_slot_allocator.py`）

3 个方法全加 `business_id` 必填参数，SQL 都 JOIN `businesses` 加 `b.status = 'active'`：

```python
async def allocate(self, *, brief, n, base_time=None, stagger_minutes=15,
                   persona_config=None, business_id: UUID) -&gt; list[ChosenSlot]
async def count_active_devices(self, *, business_id: UUID) -&gt; int
async def is_slot_valid(self, *, device_id, account_id, business_id: UUID,
                         now=None) -&gt; bool
```

### 7.2 `DefaultSlotPicker`（`slot_picker.py`）
`choose_slot(*, draft, persona_config=None, now=None)` 的 `draft` dict 必含 `business_id`（**来源链路**：orchestrator 的 `_build_run_payload` 写入 `preassigned_slot["business_id"]` → schedule 节点从 `preassigned_slot` 取出后传入 `draft` → slot_picker 读出，详见 8.5 节），SQL 加 `WHERE a.business_id = :business_id AND b.status = 'active'`。

---

## 八、orchestrator 改造

### 8.1 `_prepare_round` 透传 `business_id`（`orchestrator.py:205-271`）
写 `AgentRun` 时加 `business_id=goal.business_id`（主路径 + fallback 都加）。

### 8.2 `_allocate_round_slots` 透传 + 统一 N（`orchestrator.py:120-170`）
**N 计算统一**：主路径不再单独写 `min(active, MAX)`，改用 `_count_target_for_round(goal)` 取 n_target（含 `notes_per_round` 上限），再与 active 数取小。降级路径的 `_count_target_for_round(goal)` 行为不变：

```python
n_target = _count_target_for_round(goal)
n_active = await services.round_allocator.count_active_devices(business_id=goal.business_id)
n = min(n_target, n_active, DEFAULT_MAX_ROUND_FANOUT)
slots = await services.round_allocator.allocate(
    brief=..., n=n, persona_config=persona_cfg, business_id=goal.business_id,
)
```

archived 业务 short-circuit：`if goal.business_id is None: return [], 0`（兜底）。

### 8.3 orchestrator scan 过滤 archived（`orchestrator_runner.py:45`）
`_scan_once` SQL JOIN `Business` 加 `Business.status = 'active'`，archived 业务 goal 不进主循环。

### 8.4 Schedule 校验 slot（`backend/matrix/agent/nodes/schedule.py:67`）
`is_slot_valid` 方法定义在 `backend/matrix/scheduler/round_slot_allocator.py:163`，schedule 节点在 `:67` 调用前从 `preassigned_slot.business_id` 取值（`preassigned_slot` 是 `_build_run_payload` 写入的），缺则返 `NO_PREASSIGNED_SLOT_INVALID`。

### 8.5 `_build_run_payload` 写 `business_id` 到 preassigned_slot
在 `preassigned_slot` dict 加 `"business_id": str(goal.business_id)`。整条链路：

```
orchestrator._build_run_payload  →  preassigned_slot["business_id"] = str(goal.business_id)
                                  ↓
schedule 节点                       →  从 preassigned_slot 取出，传给 slot_picker
                                  ↓
slot_picker.choose_slot(draft=...)  →  draft["business_id"]  →  SQL WHERE a.business_id = :business_id
```

schedule 节点在调用 `choose_slot` 前缺 `business_id` 时返 `NO_PREASSIGNED_SLOT_INVALID`（兜底）。

---

## 九、chat 鉴权链路

### 9.1 ChatRequest 必填 `business_id`（`schemas/chat.py:47`）
```python
class ChatRequest(BaseModel):
    message: str
    history: list[ChatHistoryMessage] = Field(default_factory=list)
    session_id: Optional[uuid.UUID] = None
    business_id: uuid.UUID  # ← 新增必填；Pydantic 自动 422
```

### 9.2 chat.py 主路由（`routes/chat.py:136`）
`body.business_id` 取出后传给所有 `CHAT_TOOL_DISPATCH[intent]` 调用。

### 9.3 chat_tools 5 工具加过滤（`agent/chat_tools.py`）
- `ask_data`（line 142）：所有 subcommand SQL 加 `WHERE g.business_id = :business_id`；`single` subcommand 校验 `goal.business_id == business_id`
- `diagnose`（line 265）：`_resolve_goal_filter` 必传 `business_id`；KB 检索也按业务过滤
- `preview_change / apply_change`（line 438, 540）：`_resolve_goal_filter` 必传 `business_id`；`_do_change` 加 `operator_business_id` 校验（`goal.business_id != operator_business_id` 抛 `ValueError("cross_business_modification_forbidden")`）
- `browse_kb`（line 611）：`KbDocumentORM.business_id == business_id`

### 9.4 `_resolve_goal_filter` 加 `business_id`（`chat_tools.py:49-91`）
```python
async def _resolve_goal_filter(session, filter_args, *, business_id: UUID) -&gt; list[GoalORM]:
    stmt = select(GoalORM).where(
        GoalORM.deleted_at.is_(None),
        GoalORM.business_id == business_id,  # ← 强制
    )
```

### 9.5 `_CONFIRMATION_STORE` key 含 `business_id`（`routes/chat.py:51`）
```python
_CONFIRMATION_STORE: dict[str, dict[str, Any]] = {}
# token → {"args": dict, "business_id": uuid.UUID, "expires_at": float}

def _store_token(token: str, args: dict[str, Any], business_id: uuid.UUID) -> None: ...
def _consume_token(token: str) -> tuple[Optional[dict[str, Any]], Optional[uuid.UUID]]:
    entry = _CONFIRMATION_STORE.pop(token, None)
    if entry is None or entry["expires_at"] < time.time():
        return None, None
    return entry["args"], entry["business_id"]
```

`/confirm <token>` 路径（`routes/chat.py:144`）：校验 `token_business_id != body.business_id` 时返 `parse_error` + `business_mismatch`，拒绝跨业务确认。

### 9.6 `_do_change` 加 operator 校验（`chat_tools.py:129`）
```python
def _do_change(goal, field, to_value, *, operator_business_id: UUID) -&gt; Any:
    if goal.business_id != operator_business_id:
        raise ValueError(f"cross_business_modification_forbidden: ...")
    # ...
```

---

## 十、API 路由改造

### 10.1 7 张表 Pydantic schema 加 `business_id`
- `AccountCreate / DeviceRegisterRequest / PersonaCreate / GoalCreate / NoteCreate / KbDocumentCreate`：加 `business_id: uuid.UUID`（必填）
- 所有 `Update` schema：**不暴露 business_id**（Pydantic 字段不存在 → 自动拒绝修改）

### 10.2 POST handler 校验 archived
所有 `POST /accounts / /devices / /personas / /goals / /kb/documents` 都加：
```python
biz = await session.get(BusinessORM, body.business_id)
if biz is None: raise HTTPException(404, "business not found")
if biz.status == "archived": raise HTTPException(409, "cannot create under archived business")
```

`POST /notes`：`body.business_id` 可选；缺则从 `account_id` 或 `goal_id` 推断；都缺返 400。

### 10.3 list 路由全加 `?business_id=` 过滤

`backend/matrix/api/routes/` 目录实际有 17 个路由文件（不含 `__init__.py`），按性质分两类：

**业务数据类（14 个，需加 `?business_id=` 过滤）**：
`accounts.py / devices.py / personas.py / goals.py / notes.py / kb.py / interactions.py / agent_runs.py / analytics.py / metrics.py / alerts.py / notifications.py / learning.py / chat.py` 都加 `business_id: Optional[uuid.UUID] = Query(None)` 参数；不传返全业务，传则过滤（其中 `chat.py` 用 body 传，不用 query）。

**基础设施类（3 个，**不加**过滤）**：
- `health.py`：服务健康检查，无业务语义
- `settings.py`：全局配置，业务无关
- `logs.py`：全局日志，按业务过滤收益小（运维场景）

`POST /learning/summarize-goal/{goal_id}` 加 `business_id` query 必传，校验 `goal.business_id == business_id` 后才执行。

### 10.4 新增 `routes/businesses.py`（CRUD + archive）
```python
@router.get("")  # 列业务（含 archived）
@router.post("")  # 建业务（slug 唯一）
@router.get("/{business_id}")  # 详情
@router.patch("/{business_id}")  # 改 name/description/slug（不改 status）
@router.post("/{business_id}/archive")  # 软归档
@router.post("/{business_id}/unarchive")  # 恢复
```

注册到 `app.py:413` 之后：`app.include_router(businesses_routes.router, prefix=API_PREFIX)`。

### 10.5 Pydantic schemas
新增 `schemas/business.py`：`Business / BusinessCreate / BusinessUpdate / BusinessListResponse`。`schemas/__init__.py` 加导出。`schemas/{account,device,persona,goal,note,kb}.py` 都在 model 类加 `business_id`。

---

## 十一、前端改造

### 11.1 `types/api.ts`
新增 `Business / BusinessStatus / BusinessCreate / BusinessUpdate` 类型。`Account / Device / Persona / Goal / Note / KbDocument / AgentRun` interface 都加 `business_id: string`。`AccountCreate / DeviceRegisterRequest / PersonaCreate / GoalCreate / NoteCreate / KbDocumentCreate` 都加 `business_id: string`。

### 11.2 `stores/ui-store.ts` 加 `activeBusinessId`
```typescript
interface UIState {
  // ... 现有
  activeBusinessId: string | null;
  setActiveBusinessId: (id: string | null) =&gt; void;
}
```
启动时从 `localStorage['matrix.business.active_id']` 恢复；切换时写回。

### 11.3 `hooks/use-businesses.ts`（新增）
```typescript
export function useBusinesses(params?: { status?: BusinessStatus })
export function useActiveBusiness()
export function useSetActiveBusiness()
```

### 11.4 7 个 use-* hook 加 `business_id` 透传
`use-accounts / use-devices / use-personas / use-goals / use-notes / use-kb / use-agent-runs`：所有 list query 加 `business_id: useActiveBusiness()` 参数。

### 11.5 `use-chat.ts` 注入 active business_id
`useChat` / `useConfirmChat` 都从 `useActiveBusiness()` 拿值注入 request body。

### 11.6 topbar 加 BusinessSelector（`components/layout/topbar.tsx`）
下拉选 active business；切换触发 `matrix:business-changed` 事件，chat 页响应清空当前对话。

### 11.7 `pages/chat.tsx` localStorage 按业务分区
```typescript
function getStorageKey(businessId: string | null): string {
  return `matrix.chat.messages.v1.${businessId ?? 'unknown'}`;
}
```
`loadMessages / saveMessages / reset` 都接受 `businessId` 参数；切换业务触发重 load。

### 11.8 14 个页面 header 显示当前业务名 + 过滤
所有 list 页面：header 显示当前 active business 名；list query 自动带 business_id；空数据时引导"先建业务"。

### 11.9 新增 `/businesses` 管理页（`pages/businesses.tsx`）
- 表格：name / slug / status / created_at / actions（编辑 / archive）
- 创建对话框（name / slug / description）
- 编辑对话框
- Archive 确认弹窗
- 侧栏（`sidebar.tsx`）加菜单项"业务管理"（图标 Briefcase）
- 路由（`App.tsx`）：`<Route path="/businesses" element={<Businesses />} />`

---

## 十二、测试改造

### 12.1 `tests/test_db.py:153` `EXPECTED_TABLES` 加 `businesses`

### 12.2 新增 `tests/conftest.py`
共享 fixture：`business_factory / default_business / device_fixture / account_fixture / persona_fixture / goal_fixture / note_fixture / kb_document_fixture`（每个都接受 business 参数；不传则用 default）。

### 12.3 现有 29 个测试文件批量加 business_id
所有用到 `Account / Device / Persona / Goal / Note / KbDocument` 的 fixture 调用都加 `business_id=default_business.id`。

> 注：`backend/tests/` 实际有 29 个 `test_*.py`（不含 `__init__.py` 和 `_fake_adapters.py`）。

### 12.4 新增 `tests/test_cross_business_authorization.py`（5 个负向测试）
```python
async def test_account_other_business_returns_404(biz_a, biz_b)
async def test_chat_modify_other_business_goal_forbidden(biz_a, biz_b)
async def test_allocate_archived_business_returns_empty(biz_archived)
async def test_create_account_under_archived_business_rejected(biz_archived)
async def test_confirmation_token_cross_business_rejected(biz_a, biz_b)
```

### 12.5 新增 `tests/test_agent_factory.py`（修生产 bug 验证）
```python
async def test_build_runtime_services_injects_round_allocator()
async def test_orchestrator_uses_notes_per_round_for_n(monkeypatch)
```

### 12.6 新增 `tests/test_migrations.py`（alembic 真库测试）
临时 PG schema 跑 `alembic upgrade head`，断言每张表存在、约束正确。

---

## 十三、分期交付

### 第 1 期：Bug 修复 + 数据层 + migration（无 API/前端变更）

**后端**：
- `_agent_factory.py:139-148` 注入 `DefaultRoundSlotAllocator`
- `orchestrator.py:140-148` 改 n 计算为 `min(notes_per_round, active, MAX)`
- 015 / 017 alembic migration
- `scripts/backfill_business.py` + `--verify` 模式
- `models.py` 加 `Business` 类 + 7 张表加 `business_id`（nullable 起；017 升级 NOT NULL）
- 6 个复合索引创建

**前端**：无

**测试**：
- `test_round_slot_allocator.py` 28 个调用加 `business_id` 参数
- `test_orchestrator.py` 加 `test_allocate_round_slots_n_uses_notes_per_round`
- `test_agent_factory.py` 加 `test_build_runtime_services_injects_round_allocator`
- `test_db.py` 加 `businesses` 到 EXPECTED_TABLES（26 → 27）
- 新增 `conftest.py`

**验收**：
- `pytest backend/tests/test_round_slot_allocator.py backend/tests/test_orchestrator.py` 全绿
- 生产环境跑：015 → 016 dry-run → 016 真跑 → 017
- `SELECT count(*) FROM accounts WHERE business_id IS NULL` = 0
- 看 orchestrator 日志现在会出 `orchestrator.allocated` 而非 `no_candidates`（主路径真跑）

### 第 2 期：API 层 + chat 鉴权（前端可选）

**后端**：
- 7 张表 ORM `business_id` 验证
- 所有 POST 加 business_id + archived 校验
- 所有 PATCH 不暴露 business_id
- 所有 list 加 `?business_id=` 过滤
- 新增 `routes/businesses.py` + 注册
- 新增 `schemas/business.py`
- chat 鉴权链路全做（ChatRequest 加 business_id 必填、5 工具过滤、_CONFIRMATION_STORE key、_do_change operator 校验）

**前端**（可选最小改动）：
- `types/api.ts` 加 Business 类型
- `use-chat.ts` 注入 active business_id
- 14 个页面 list 在请求加 `business_id` 参数（不强校验：ui-store 无值就调全业务）

**测试**：
- `test_cross_business_authorization.py` 5 个负向测试
- 现有 API 测试加 business_id fixture
- chat 鉴权测试

**验收**：
- `pytest backend/tests/` 全绿
- curl 测：`POST /accounts` 不传 business_id → 422；archived 业务 POST → 409

### 第 3 期：前端业务切换 + 管理页

**前端**：
- `ui-store.ts` 加 activeBusinessId + localStorage 持久化
- `topbar.tsx` 加 BusinessSelector
- 14 个页面 header 显示业务名 + 过滤严格生效
- `chat.tsx` localStorage 按业务分区 + 切业务 reload
- `pages/businesses.tsx` 管理页
- 路由 `/businesses`
- 侧栏菜单项

**测试**：
- Vitest BusinessSelector 组件
- Vitest ui-store 持久化
- E2E：建业务 → 切业务 → 看到不同数据

**验收**：
- 浏览器操作：切业务各页面实时更新
- chat 切业务看到新业务历史
- 管理页可 CRUD + archive

### 第 4 期：可选增强（按需触发，不在本期）
- `notifications / alerts` 加 business_id
- DB 层 trigger 强制 business_id 不可改
- chat history localStorage 加密
- 多业务对比 dashboard

---

## 十四、复用现有资产

- **Alembic 迁移模式**：`op.execute()` + hash revision（参考 `f1e2d3c4b5a6` 写法）
- **Pydantic schema 模式**：参考 `PersonaCreate / PersonaUpdate` 分离（Create 含 business_id，Update 不暴露）
- **路由 ListResponse 模式**：参考 `accounts.py:29-45` 加 Query 参数
- **PostgreSQL JSONB 索引**：现有 `Goal.target` 已用 `astext` 索引，新加的 `business_id` 用 B-tree 即可
- **`_CONFIRMATION_STORE` 模式**：现有进程内 dict + TTL（`routes/chat.py:51-85`），扩展为含 business_id
- **front-end `ui-store.ts` 模式**：localStorage 持久化 + Zustand-like store
- **`BusinessSelector` 复用 `radix-ui Select`**：现有 `select.tsx` 组件
- **后端 logging 结构**：所有 `logger.warning/info` 用 `logger.xxx(event=..., **fields)` 模式

---

## 十五、风险与缓解

| 风险 | 缓解 |
|---|---|
| 016 回填漏行 | 必跑 `--verify`；017 前置门 `count WHERE business_id IS NULL = 0` |
| archived 误用 | 三层防御（allocator SQL + 路由层 + orchestrator 扫描）；任一层失守不影响其他层 |
| chat 鉴权绕过 | `_do_change` 加 operator_business_id（即使路由漏检也兜底） |
| `_build_run_payload` 漏写 business_id | `schedule.py` 加校验（payload 缺则 NO_PREASSIGNED_SLOT_INVALID） |
| 测试 fixture 加 business_id 漏改 | conftest.py 提供 `default_business`；一处生效 |
| `_agent_factory.py` 修复回退 | 加 `test_build_runtime_services_injects_round_allocator` 守住 |

---

## 十六、关键文件改动清单

**后端核心**：
- `backend/matrix/db/models.py` — 新增 Business 类（约 line 130，DeviceHeartbeat 之后、Account 之前）+ 7 张表加 `business_id` 字段（line 131-173 / 242-271 / 331-388 / 418-461 / 493-550 / 670-710）
- `backend/matrix/api/_agent_factory.py:139-148` — 注入 `DefaultRoundSlotAllocator`（修生产 bug）
- `backend/matrix/agent/orchestrator.py:140-148, 205-271` — 改 n 计算 + 透传 business_id
- `backend/matrix/agent/chat_tools.py:49, 129, 142, 265, 438, 540, 611` — 5 工具全加 business_id 过滤 + `_do_change` 鉴权
- `backend/matrix/scheduler/round_slot_allocator.py:60, 144, 163` — 3 方法加 business_id + SQL JOIN `businesses`
- `backend/matrix/scheduler/slot_picker.py:32` — choose_slot 加 business_id
- `backend/matrix/api/routes/businesses.py` — **新文件**（CRUD + archive）
- `backend/matrix/api/schemas/business.py` — **新文件**

**路由层**：
- `routes/{accounts,devices,personas,goals,notes,kb,learning}.py` — POST 加 business_id 校验；list 加 `?business_id=` 过滤；PATCH schema 不暴露 business_id
- `routes/chat.py:51, 136, 144` — ChatRequest 必填 + confirmation token 含 business_id（`51` = `_CONFIRMATION_STORE` dict 定义；`136` = 主路由；`144` = `/confirm` 短路）

**Migration**：
- `backend/matrix/db/migrations/versions/015_add_businesses_table_and_nullable_business_id.py` — **新文件**
- `backend/matrix/db/migrations/versions/017_business_id_not_null_and_constraints.py` — **新文件**
- `scripts/backfill_business.py` — **新文件**

**注**：migration 链完全线性（`014 → d0a5fb51f30f → f1e2d3c4b5a6`），**不创建 merge revision 文件**。

**前端**：
- `shell/src/types/api.ts` — 新增 Business 类型 + 7 张表 interface 加 business_id
- `shell/src/stores/ui-store.ts` — 加 activeBusinessId
- `shell/src/hooks/use-businesses.ts` — **新文件**
- `shell/src/components/layout/topbar.tsx` — 加 BusinessSelector
- `shell/src/pages/businesses.tsx` — **新文件**
- `shell/src/pages/chat.tsx:17-50, 72-108` — localStorage 按业务分区
- `shell/src/App.tsx:27-45` — 加 `/businesses` 路由
- `shell/src/components/layout/sidebar.tsx:15-25` — 加菜单项

**测试**：
- `backend/tests/conftest.py` — **新文件**（共享 fixture）
- `backend/tests/test_cross_business_authorization.py` — **新文件**（5 负向测试）
- `backend/tests/test_agent_factory.py` — **新文件**（修 bug 验证）
- `backend/tests/test_migrations.py` — **新文件**（alembic 真库）
- `backend/tests/test_db.py:153` — EXPECTED_TABLES +1
- 现有 28 个测试文件加 business_id fixture

---

## 十七、验证步骤

### 跑测
```bash
cd /Users/lylyyds/Desktop/矩阵
docker compose run --rm test pytest backend/tests/ -v 2>&1 | tail -30
# 期望：所有现有测试 + 新测试全绿
docker compose run --rm frontend pnpm typecheck && pnpm lint
# 期望：通过
```

### 真实环境跑 migration（dev 环境）
```bash
# 第 1 期先在 dev 环境跑：
docker compose exec backend alembic upgrade 015_add_businesses_table_and_nullable_business_id
docker compose exec backend python -m scripts.backfill_business --dry-run  # 先看分配
docker compose exec backend python -m scripts.backfill_business           # 真跑
docker compose exec backend python -m scripts.backfill_business --verify  # 验证 7 表全 0 NULL
docker compose exec backend alembic upgrade 017_business_id_not_null_and_constraints
```

### E2E
```bash
# 1) 验证生产 bug 修复：orchestrator 真跑主路径
curl http://localhost:8666/api/v1/health
# 创建 1 个 business + 1 个 goal，看 orchestrator 日志：
docker compose logs -f backend | grep "orchestrator.allocated"
# 期望：出现（之前是 no_candidates）

# 2) 验证业务归档拦截：
curl -X POST http://localhost:8666/api/v1/businesses \
  -H 'Content-Type: application/json' \
  -d '{"name":"test-biz","slug":"test-biz","description":"测试"}'
# → 返回 business_id
curl -X POST http://localhost:8666/api/v1/businesses/{id}/archive
# 期望：返回 status=archived
curl -X POST http://localhost:8666/api/v1/accounts \
  -H 'Content-Type: application/json' \
  -d '{"handle":"@test","business_id":"{id}"}'
# 期望：409 cannot create under archived business

# 3) 验证 chat 跨业务拦截：
# 在 business_a 上下文拿 token，然后切到 business_b 调 /confirm：
# 期望：parse_error + business_mismatch
```

### 老板演示清单
- [ ] 浏览器建一个 business → 看到 topbar 出现
- [ ] 切业务 → 列表数据变
- [ ] chat 在不同业务下历史不同
- [ ] 归档业务 → 不能新建 goal/account/device
- [ ] 后端日志看 orchestrator 现在跑主路径（不是 fallback）

---

## 十八、当前事实速查（来自调研）

| 事实 | 来源 |
|---|---|
| 26 张 ORM 表（不是 25；`test_db.py:150` 注释仍写"25 张表"是既存 drift） | `tests/test_db.py:150, 153-180` |
| `models.py:938` 的 `__all__` **漏导出 `GoalRound`**（class 定义在 line 551，但 `__all__` 只列 25 个 ORM 类）—— 既存 drift | `models.py:938-965` |
| 本机 PG head = `f1e2d3c4b5a6`（线性链 `014 → d0a5fb51f30f → f1e2d3c4b5a6`，无分叉） | `alembic_version` + 各 migration `down_revision` |
| accounts=1, devices=3, personas=0, goals=57, notes=483, kb_documents=358 | 本机 DB 实时快照 |
| Note 全部 `account_id=NULL`（v0.7 DRAFT 先落库） | `models.py:339-340` |
| 14 个数字迁移 + 2 个 hash revision（`d0a5fb51f30f`、`f1e2d3c4b5a6`），链式线性 | `db/migrations/versions/` |
| 生产 bug：`_agent_factory.py:139-142` 没注入 `DefaultRoundSlotAllocator` | `_agent_factory.py:139-142` |
| ORM/DB drift：`agent_runs.goal_id` ORM 没写 `ondelete="CASCADE"` 但 DB 有 | `models.py:670-679` vs `010_goal_fk_cascade.py` |
| 14 个 FK：6 CASCADE / 6 SET NULL / 2 NO ACTION | `models.py` 全表 |
| `backend/tests/` 实际有 29 个 `test_*.py`（不含 `__init__.py` 和 `_fake_adapters.py`） | `backend/tests/` 目录 |
| `backend/matrix/api/routes/` 实际 17 个路由文件（不含 `__init__.py`） | `backend/matrix/api/routes/` 目录 |
| chat 创建 goal 已被砍（v0.7+），但 chat_tools 还有写操作需要鉴权 | `routes/chat.py:1-5` 注释 + `chat_tools.py` |
| **本轮审核新增 drift**：`AgentServices.round_allocator` 字段在 `_services.py:71`（不是 `_agent_factory.py:71`）| `backend/matrix/agent/_services.py:71` |
| **本轮审核新增 drift**：`is_slot_valid` 方法定义在 `round_slot_allocator.py:163`，调用点在 `agent/nodes/schedule.py:67`（不存在 `scheduler/schedule.py`，别引用错）| `round_slot_allocator.py:163` + `agent/nodes/schedule.py:67` |
| **本轮审核新增 drift**：`routes/chat.py` 主路由 `@router.post("")` 在 line 136（不是 137）| `routes/chat.py:136` |

---

## 十九、文档维护说明

- 本文档由 Claude Code 在 2026-07-16 根据老板业务洞察 + 代码事实调研产出
- 实施时按第十三节"分期交付"逐步推进，每期完成更新对应章节的完成情况
- 代码变更必须同步更新本文件相关章节（事实变了文档也得变）
- 如发现本文档与代码事实不符，以代码为准并更新文档