# 测试计划

| 项 | 内容 |
|---|---|
| 适用对象 | QA / 开发 |
| 配套 | [architecture/SDD.md §14](../architecture/SDD.md) / [API 规范](../api/) / [database/schema.sql](../database/schema.sql) |

## 1. 测试层级

### 1.1 单元测试
- 范围：限速器 / 状态机 guard / 风险评分 / KB 检索 / 错误分类 / 工具 schema 校验
- 框架：pytest
- 覆盖门槛：核心模块 ≥ 80%
- 运行：`pytest backend/tests/unit/`

### 1.2 集成测试
- 范围：Agent 节点 → MCP tool → Mock APK Server / DB 迁移 / checkpoint 续跑
- 框架：pytest + httptest mock
- 运行：`pytest backend/tests/integration/`

### 1.3 E2E 测试
- 范围：真机端到端（发布 / 互动 / 回采）
- 框架：pytest + 真机 / 模拟器
- 运行：`pytest backend/tests/e2e/`
- 触发：每日 CI + PR merge 前

### 1.4 性能 / 负载
- 范围：模拟 50-100 台设备并发
- 框架：locust / k6
- 触发：每周 / 上线前

### 1.5 风控对抗验证
- 范围：真机 24h 连续运营
- 触发：MVP 前 + 重大变更后

## 2. 单元测试用例

### 2.1 限速器

```python
# backend/tests/unit/test_rate_limiter.py
def test_token_bucket_capacity():
    bucket = TokenBucket(capacity=5, refill_rate=1)
    for _ in range(5):
        bucket.acquire(timeout=0.1)
    with pytest.raises(RateLimitTimeout):
        bucket.acquire(timeout=0.1)

def test_token_bucket_refill():
    bucket = TokenBucket(capacity=5, refill_rate=10)  # 10/s
    for _ in range(5):
        bucket.acquire(timeout=0.1)
    time.sleep(0.5)
    bucket.acquire(timeout=0.1)  # refill 后应能取到

def test_jitter_distribution():
    delays = [jitter_delay(1.0) for _ in range(10000)]
    assert 0.5 < np.percentile(delays, 5) < 1.0
    assert 1.0 < np.percentile(delays, 95) < 2.0
```

### 2.2 状态机 guard

```python
def test_review_to_schedule_guard():
    state = Draft(content="...", persona_match=0.9, forbidden_hits=0, dup_score=0.1)
    assert can_review_to_schedule(state) == True

def test_review_to_revise_forbidden():
    state = Draft(content="...", persona_match=0.9, forbidden_hits=1, dup_score=0.1)
    assert can_review_to_schedule(state) == False
    assert can_review_to_revise(state) == True
```

### 2.3 工具 schema 校验

```python
def test_device_publish_schema():
    valid = {
        "device_id": str(uuid.uuid4()),
        "note": {
            "title": "test",
            "content": "...",
            "images": ["http://..."],
            "tags": ["tag1"]
        },
        "request_id": str(uuid.uuid4())
    }
    assert validate_tool_input("device_publish", valid) == True

def test_device_publish_missing_field():
    invalid = {"device_id": "...", "note": {"title": "..."}}
    with pytest.raises(SchemaError):
        validate_tool_input("device_publish", invalid)
```

## 3. 集成测试

### 3.1 Agent 节点测试

```python
@pytest.fixture
def mock_apk_server():
    with MockAPKServer() as server:
        yield server

async def test_research_to_draft(mock_apk_server, db_session):
    state = ResearchNode()
    result = await state.run(goal="净涨 500 粉", db=db_session)
    assert len(result.topics) > 0
    assert result.next_state == "DRAFT"

async def test_publish_success(mock_apk_server, db_session):
    state = PublishNode()
    result = await state.run(
        device_id=mock_apk_server.device_id,
        note=sample_note(),
        request_id=str(uuid.uuid4())
    )
    assert result.ok == True
    assert result.data["platform_note_id"] is not None
    assert db_session.query(Note).filter_by(platform_note_id=result.data["platform_note_id"]).first()
```

### 3.2 Checkpoint 续跑

```python
async def test_resume_from_checkpoint(db_session):
    # 创建一个跑了一半的 run
    run = create_test_run(state="DRAFT")
    db_session.add(run)
    db_session.commit()

    # 重启模拟
    await resume_runs()

    # 验证从 DRAFT 续跑
    assert run.current_state in ["DRAFT", "REVIEW", "SCHEDULE", ...]
```

### 3.3 并发任务

```python
async def test_concurrent_dispatch():
    # 创建 100 个 task
    tasks = [create_test_task() for _ in range(100)]

    # 并发调度
    await asyncio.gather(*[dispatch(t) for t in tasks])

    # 验证：全部完成
    success = db.query(Task).filter_by(status='success').count()
    assert success == 100
```

## 4. E2E 测试

### 4.1 端到端发布

```python
@pytest.mark.e2e
def test_e2e_publish(real_device, real_xhs_account):
    # 前置：APK 已配对 / 账号已登录
    # 1. 创建 Goal
    goal = create_goal(type="publish", target={"count": 1})

    # 2. 触发 Agent
    run_agent(goal.id)

    # 3. 等待完成（最多 10 min）
    wait_for_goal_completion(goal.id, timeout=600)

    # 4. 验证
    notes = query_notes(account_id=real_xhs_account.id, status="published")
    assert len(notes) >= 1
    assert notes[0].platform_url is not None
    # 平台端验证（人工或 API）
```

### 4.2 选择器回归

```python
@pytest.mark.e2e
@pytest.mark.parametrize("screen", [
    "home", "publish_entry", "publish_editor", "profile"
])
def test_selector_match(real_device, screen):
    # 1. 启动 XHS
    apk.open_app("com.xingin.xhs")
    apk.wait_for({"text": screen_titles[screen]}, timeout=10)

    # 2. 验证关键选择器
    selectors = load_selectors(screen)
    for sel in selectors:
        node = apk.find_selector(sel)
        assert node is not None, f"Selector {sel} not found on {screen}"
```

### 4.3 数据回采

```python
def test_metrics_collect(real_device, published_note):
    # 等待笔记曝光 1 小时
    sleep(3600)

    # 回采
    result = apk.device_collect(scope="recent_24h")
    assert result.ok

    # 验证 metrics 写入
    metrics = db.query(NoteMetrics).filter_by(note_id=published_note.id).all()
    assert len(metrics) > 0
    assert any(m.views > 0 for m in metrics)
```

## 5. 性能 / 负载

### 5.1 调度吞吐

```python
# locustfile.py
class MasterUser(HttpUser):
    @task
    def dispatch_task(self):
        self.client.post("/api/v1/internal/dispatch", json={
            "device_id": "...",
            "account_id": "...",
            "action": "device_publish",
            "payload": {...}
        })
```

测试场景：
- 100 并发用户 / 5 分钟
- 目标：P95 < 500ms
- DB 连接池无打满

### 5.2 DB 性能

```sql
EXPLAIN ANALYZE
SELECT * FROM tasks
WHERE status = 'pending' AND scheduled_at <= NOW()
ORDER BY scheduled_at
LIMIT 100;
```

目标：< 10ms（10万 task 量级）

## 6. 风控对抗验证

### 6.1 24h 真机运行

```python
def test_24h_no_ban(real_device, real_xhs_account):
    # 配置：每小时发 1 篇笔记 + 5 次互动
    config = {
        "publish_per_hour": 1,
        "interact_per_hour": 5,
        "active_hours": "09:00-23:00"
    }

    # 启动 Agent
    start_agent(real_xhs_account.id, config)

    # 运行 24h
    sleep(86400)

    # 验证
    assert real_xhs_account.status == "active"
    # 检查流量是否降权（人工或运营者观察）
```

### 6.2 拟人化指标

```python
def test_human_like_pattern(real_device):
    # 抓取 7 天的操作日志
    operations = db.query(Task).filter(
        Task.device_id == real_device.id,
        Task.executed_at > now() - timedelta(days=7)
    ).all()

    # 验证操作间隔分布
    intervals = compute_intervals(operations)
    assert intervals.std() / intervals.mean() > 0.3  # 波动足够大

    # 验证操作序列（不应全是相同动作）
    sequences = compute_sequences(operations)
    assert len(set(sequences)) > 5  # 序列多样性
```

## 7. 测试数据

### 7.1 测试账号

- 准备 3-5 个测试 XHS 账号（**仅用于测试，不发布真实内容**）
- 标记 `accounts.purpose = 'test'`，UI 中隐藏

### 7.2 测试知识库

- 1 个测试 persona
- 5 个测试 topic
- 10 个测试 rule

## 8. CI 流水线

```yaml
# .github/workflows/test.yml
name: tests
on: [push, pull_request]

jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install poetry
      - run: poetry install
      - run: pytest backend/tests/unit/ --cov=matrix --cov-fail-under=80

  integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
    steps:
      - uses: actions/checkout@v4
      - run: pytest backend/tests/integration/

  e2e:
    runs-on: [self-hosted, android]  # 自托管 runner（真机）
    steps:
      - uses: actions/checkout@v4
      - run: pytest backend/tests/e2e/ -m e2e
```

## 9. 验收签收标准

- 所有 P0 验收用例（见 PRD §11）通过
- 单元测试覆盖 ≥ 80%
- 集成测试 100% 通过
- E2E 测试每日回归通过
- 性能测试 P95 < 500ms
- 风控对抗 24h 通过（无封号 / 无降权）

## 10. 缺陷管理

- 用 GitHub Issues 跟踪
- 标签：`bug` / `test-failure` / `flaky`
- 严重等级：`P0`（阻塞）/ `P1`（主流程）/ `P2`（边界）/ `P3`（UI 体验）
- P0 / P1 必须 24h 内修
