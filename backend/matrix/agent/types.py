"""Agent 共享类型。

StateGraph 的 state 必须是 TypedDict（langgraph 会按字段做 partial update），
因此这里用 `AgentState` 作为 schema。运行时 payload 通过 `Dict[str, Any]`
承载未结构化内容，节点写入与读取都在此协议下完成。

为便于测试与重构，把状态机用到的常量集中在这里。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict
from uuid import UUID

from .protocols import DeviceSlot, RetrievedChunk

# ---------------------------------------------------------------------------
# 状态常量
# ---------------------------------------------------------------------------


class State(str, Enum):
    """状态机节点。"""

    IDLE = "IDLE"
    RESEARCH = "RESEARCH"
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    REVISE = "REVISE"
    SCHEDULE = "SCHEDULE"
    DISPATCH = "DISPATCH"
    PUBLISH = "PUBLISH"
    INTERACT = "INTERACT"  # v0.6 发后流量互推（like + comment）
    COLLECT = "COLLECT"
    ANALYZE = "ANALYZE"
    ALERT = "ALERT"


# 9 个主状态（ALERT/REVISE 是异常/中间态，作为节点也注册）
NINE_STATES: tuple[State, ...] = (
    State.IDLE,
    State.RESEARCH,
    State.DRAFT,
    State.REVIEW,
    State.SCHEDULE,
    State.DISPATCH,
    State.PUBLISH,
    State.COLLECT,
    State.ANALYZE,
)

ALL_STATES: tuple[State, ...] = NINE_STATES + (State.REVISE, State.ALERT)


# Run status 常量
class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


# Review 失败原因
class ReviewFailureReason(str, Enum):
    FORBIDDEN_WORD = "forbidden_word"
    DUPLICATE = "duplicate"
    LOW_HUMAN_SCORE = "low_human_score"


# ---------------------------------------------------------------------------
# State 形状
# ---------------------------------------------------------------------------


class DraftPayload(TypedDict, total=False):
    """DRAFT 节点产出。"""

    note_id: UUID
    title: str
    content: str
    images: list[str]
    tags: list[str]


class ReviewPayload(TypedDict, total=False):
    """REVIEW 节点产出。"""

    note_id: UUID
    passed: bool
    score_human: float  # 拟人化 0-1
    score_dup: float  # 与历史最高相似度 0-1
    forbidden_hits: list[str]  # 命中违禁词列表（空表示无）
    reason: str  # 通过/失败原因


class CandidateTopic(TypedDict, total=False):
    """RESEARCH 节点产出的候选选题。"""

    topic_id: UUID | None  # topics.id 或临时生成
    title: str
    rationale: str  # 命中规则 / 人设 / 历史的简短依据


# ---------------------------------------------------------------------------
# 全局 AgentState（langgraph state schema）
# ---------------------------------------------------------------------------


class AgentState(TypedDict, total=False):
    """状态机 state。langgraph 节点返回 dict 仅做 partial update。"""

    # 关联
    run_id: UUID
    goal_id: UUID | None
    plan_id: UUID | None

    # 当前节点（由 langgraph 自动维护；冗余便于读 checkpoint）
    current_state: str

    # 主题摘要（brief）：chat LLM 收敛出的结构化主题对象
    # 包含 theme/audience/product_category/goal_type 等，节点写入稿 / 审稿 / 选题时
    # 优先于 goal_text 使用。注：命名避开前端 UIStore 已占用的 `theme`。
    brief: dict[str, Any] | None

    # 累计迭代计数
    revise_attempts: int

    # 候选与草稿
    candidates: list[CandidateTopic]
    selected_topic: CandidateTopic | None
    draft: DraftPayload | None
    review: ReviewPayload | None

    # 检索上下文（每个节点写入后清空，保持 state 紧凑）
    research_chunks: list[RetrievedChunk]
    review_rules: list[RetrievedChunk]

    # 排期 / 任务 / 执行
    scheduled_at: str  # ISO8601 string
    slot: DeviceSlot | None
    created_task_ids: list[UUID]

    # 发布 / 回采
    publish_result: dict[str, Any]  # PublishResult 序列化
    note_metrics: dict[str, int]  # {'views':..,'likes':.., ...}

    # 互动（v0.6）—— list[{'note_id': str, 'kind': 'like'|'comment', 'content_template'?: str}]
    interact_plan: list[dict[str, Any]]
    interact_results: dict[str, Any]  # {'succeeded': int, 'failed': int, 'details': [...]}
    interact_attempts: int

    # 异常
    last_error: dict[str, Any] | None
    # 触发 ALERT 的错误快照（ALERT 节点清掉 last_error 之前留底）
    last_error_snapshot: dict[str, Any] | None


__all__ = [
    "State",
    "NINE_STATES",
    "ALL_STATES",
    "RunStatus",
    "ReviewFailureReason",
    "AgentState",
    "DraftPayload",
    "ReviewPayload",
    "CandidateTopic",
]
