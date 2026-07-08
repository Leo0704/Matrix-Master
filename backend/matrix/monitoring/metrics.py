"""Prometheus 指标定义。

覆盖 docs/operations/monitoring-runbook.md §2.1-2.5：
- §2.1 设备指标
- §2.2 账号指标
- §2.3 任务指标
- §2.4 Agent 指标
- §2.5 LLM / VLM 成本

约定：
- Histogram buckets: ``LATENCY_BUCKETS`` = [0.1, 0.5, 1, 2, 5, 10, 30] 秒
- Gauge / Counter 命名与 runbook 表格对齐（``_count``/``_seconds``/``_usd`` 后缀）
- 所有指标挂在 ``matrix_`` 前缀下，避免与 SDK / 业务库冲突
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

NAMESPACE = "matrix"

# Latency 桶：100ms -> 30s，覆盖 task / llm / device_call 等场景。
LATENCY_BUCKETS = (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)


# ---------------------------------------------------------------------------
# §2.1 设备指标
# ---------------------------------------------------------------------------

device_online_count = Gauge(
    "device_online_count",
    "当前在线设备数量",
    namespace=NAMESPACE,
)

device_offline_count = Gauge(
    "device_offline_count",
    "当前离线设备数量",
    namespace=NAMESPACE,
)

device_heartbeat_age_seconds = Gauge(
    "device_heartbeat_age_seconds",
    "单设备最近一次心跳距今的秒数（label=device_id）",
    ["device_id"],
    namespace=NAMESPACE,
)

device_battery_low_count = Gauge(
    "device_battery_low_count",
    "电量低于 20% 的设备数量",
    namespace=NAMESPACE,
)

device_tailscale_degraded_count = Gauge(
    "device_tailscale_degraded_count",
    "Tailscale 连接降级的设备数量",
    namespace=NAMESPACE,
)

device_apk_http_latency_seconds = Histogram(
    "device_apk_http_latency_seconds",
    "主控 ↔ APK HTTP 调用延迟（秒）",
    labelnames=["tool"],
    buckets=LATENCY_BUCKETS,
    namespace=NAMESPACE,
)


# ---------------------------------------------------------------------------
# §2.2 账号指标
# ---------------------------------------------------------------------------

account_high_risk_count = Gauge(
    "account_high_risk_count",
    "risk_score > 0.7 的账号数量",
    namespace=NAMESPACE,
)

account_banned_count_24h = Counter(
    "account_banned_count_24h",
    "近 24 小时被封禁的账号计数（由告警 / 平台回调触发）",
    namespace=NAMESPACE,
)

account_publish_success_rate_24h = Gauge(
    "account_publish_success_rate_24h",
    "近 24 小时发布成功率（0~1）",
    namespace=NAMESPACE,
)

account_login_failure_count_24h = Counter(
    "account_login_failure_count_24h",
    "近 24 小时登录失败计数",
    namespace=NAMESPACE,
)


# ---------------------------------------------------------------------------
# §2.3 任务指标
# ---------------------------------------------------------------------------

task_pending_age_seconds = Histogram(
    "task_pending_age_seconds",
    "pending 任务在队列中的停留时长（秒）",
    buckets=LATENCY_BUCKETS,
    namespace=NAMESPACE,
)

task_failure_rate_5m = Gauge(
    "task_failure_rate_5m",
    "近 5 分钟任务失败率（0~1）",
    namespace=NAMESPACE,
)

task_dispatch_throughput_per_min = Gauge(
    "task_dispatch_throughput_per_min",
    "近 1 分钟任务下发吞吐（每分钟）",
    namespace=NAMESPACE,
)

task_queue_depth_pending = Gauge(
    "task_queue_depth_pending",
    "当前 pending 队列深度",
    namespace=NAMESPACE,
)


# ---------------------------------------------------------------------------
# §2.4 Agent 指标
# ---------------------------------------------------------------------------

agent_run_duration_seconds = Histogram(
    "agent_run_duration_seconds",
    "Agent 单 run 总耗时（秒）",
    buckets=LATENCY_BUCKETS,
    namespace=NAMESPACE,
)

agent_state_machine_stuck_count = Counter(
    "agent_state_machine_stuck_count",
    "状态机卡死次数（任一状态停留 > 10min）",
    labelnames=["state"],
    namespace=NAMESPACE,
)

agent_human_takeover_rate_24h = Gauge(
    "agent_human_takeover_rate_24h",
    "近 24 小时人工接管率（0~1）",
    namespace=NAMESPACE,
)

vlm_call_count_per_run = Histogram(
    "vlm_call_count_per_run",
    "单 run 内 VLM 调用次数分布",
    buckets=(0, 1, 2, 3, 5, 10, 20, 50),
    namespace=NAMESPACE,
)

vlm_confidence_distribution = Histogram(
    "vlm_confidence_distribution",
    "VLM 置信度分布（0~1）",
    buckets=(0.1, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0),
    namespace=NAMESPACE,
)


# ---------------------------------------------------------------------------
# §2.5 LLM / VLM 成本
# ---------------------------------------------------------------------------

llm_cost_usd_per_day = Gauge(
    "llm_cost_usd_per_day",
    "LLM 当日累计花费（USD），由后台任务按窗口重置",
    namespace=NAMESPACE,
)

llm_cost_usd_per_run = Histogram(
    "llm_cost_usd_per_run",
    "单 run LLM 花费分布（USD）",
    labelnames=["model"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    namespace=NAMESPACE,
)

llm_latency_seconds = Histogram(
    "llm_latency_seconds",
    "LLM 调用端到端延迟（秒）",
    labelnames=["model"],
    buckets=LATENCY_BUCKETS,
    namespace=NAMESPACE,
)

llm_rate_limit_hit_count_1h = Counter(
    "llm_rate_limit_hit_count_1h",
    "近 1 小时 LLM 限速命中次数（label=model）",
    ["model"],
    namespace=NAMESPACE,
)

vlm_cost_usd_per_day = Gauge(
    "vlm_cost_usd_per_day",
    "VLM 当日累计花费（USD）",
    namespace=NAMESPACE,
)


# ---------------------------------------------------------------------------
# HTTP 请求指标（被 middleware 使用，不在 runbook §2 中但属于必备可观测性）
# ---------------------------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求总数（label=method/path/status）",
    ["method", "path", "status"],
    namespace=NAMESPACE,
)

http_request_latency_seconds = Histogram(
    "http_request_latency_seconds",
    "HTTP 请求处理延迟（秒）",
    labelnames=["method", "path"],
    buckets=LATENCY_BUCKETS,
    namespace=NAMESPACE,
)


# ---------------------------------------------------------------------------
# 辅助：列出所有指标，便于测试 & dashboard 校验
# ---------------------------------------------------------------------------


def all_metrics() -> dict[str, object]:
    """返回所有定义的指标对象。key 为去掉 ``matrix_`` 前缀的指标名。"""
    metrics: dict[str, object] = {}
    for name, value in list(globals().items()):
        if name.startswith("_"):
            continue
        if isinstance(value, (Counter, Gauge, Histogram)):
            metrics[name] = value
    return metrics
