# 知识库写作规范

| 项 | 内容 |
|---|---|
| 适用对象 | 运营者 / 知识库管理员 |
| 配套 | [architecture/SDD.md §3.2](../architecture/SDD.md) / [database/schema.sql](../database/schema.sql) |

知识库是 Agent 决策的输入。本规范保证知识库内容**能被 Agent 准确检索 + 正确理解**。

## 1. 核心原则

1. **少而精胜过多而杂**：每个 persona 写透，胜过写 10 个浅 persona。
2. **结构化 + 示例**：抽象描述 + 至少 1 个具体示范。
3. **可被检索**：关键词覆盖典型查询场景。
4. **可被遵守**：明确、具体、可操作，避免空话。
5. **可被验证**：每条规则有明确的"符合/不符合"判据。

## 2. 各类内容写作规范

### 2.1 Persona（人设）

**结构**：

```yaml
name: <人设名，唯一>
tone: <语气关键词，逗号分隔>
style_guide: |
  <长文描述风格，包含：>
  1. 目标受众（年龄 / 性别 / 兴趣）
  2. 内容定位（教程 / 测评 / 日常分享 / 专业知识）
  3. 表达风格（口语化 / 文艺 / 专业 / 搞笑）
  4. 长度偏好（短文案 100-300 字 / 长文 500-1000 字）
  5. 常用话题
  6. 禁忌
forbidden_words: [<明确禁止的词>]
sample_note_ids: [<示范笔记 UUID>]
```

**示例**（美妆号）：

```yaml
name: 美妆小白学姐
tone: 亲切, 实用, 不夸张
style_guide: |
  目标受众：22-28 岁都市女性，关注性价比。
  内容定位：真实测评 + 实用教程，不夸大功效。
  表达风格：第一人称口语化，像学姐跟学妹聊天。
  长度偏好：300-500 字，标题不超过 20 字。
  常用话题：新品种草、避雷、平价替代、化妆技巧。
  禁忌：医疗效果承诺、贬低竞品、过度营销词汇。
forbidden_words:
  - "最"
  - "绝对"
  - "神药"
  - "医生推荐"
sample_note_ids: [uuid1, uuid2]
```

**反面示例**：

```yaml
# 太抽象
style_guide: "用亲切的语气"

# 太营销
style_guide: "极致美妆，绽放独特魅力"
```

### 2.2 Rule（规则）

**结构**：

```yaml
category: <forbidden / best_practice / limit_avoidance>
text: |
  <规则具体内容，含判据>
severity: <1-5，1=软规则，5=硬规则>
source: <来源，如 "XHS 社区规范 v2024" / "内部" / "运营经验">
```

**示例**：

```yaml
category: forbidden
text: |
  禁止承诺医疗效果。
  判据：文案中如出现"治疗"、"治愈"、"根除"、"医生推荐"等词，必须改写。
severity: 5
source: XHS 社区规范 §3.2

category: best_practice
text: |
  发布后 30 分钟内主动回复前 5 条评论，提升账号活跃度。
severity: 2
source: 内部运营经验

category: limit_avoidance
text: |
  单篇笔记话题不超过 2 个。多个话题应拆成多篇。
  判据：标题或正文出现 3 个及以上不相关话题关键词时改写。
severity: 3
source: 内部运营经验
```

**写作要求**：
- 每条规则**含判据**（agent 可程序化判断）
- severity ≥ 4 的规则必须有判据
- 引用来源（如平台规范 / 法规）

### 2.3 Topic（选题）

**结构**：

```yaml
title: <选题标题>
category: <分类>
source: <manual / hot / historical>
heat_score: <0-1，当前热度>
```

**分类**：
- `seasonal` — 季节性
- `trending` — 热点
- `evergreen` — 常青
- `brand` — 品牌相关
- `product` — 产品相关

**示例**：

```yaml
title: 2026 早春显白发色
category: seasonal
source: manual
heat_score: 0.8
```

**写作要求**：
- 标题**像用户会搜的**（不是运营术语）
- heat_score 定期更新（每周）
- last_used 记录最近使用时间，避免短期内重复

### 2.4 Template（模板）

**结构**：

```yaml
name: <模板名>
type: <title / opening / closing / cta>
content: |
  <模板内容，含可替换变量 {{var}}>
variables: [<变量名列表>]
```

**示例**：

```yaml
name: 测评开头模板
type: opening
content: |
  姐妹们，{{product_name}}用了 {{duration}} 啦！
  真实感受：{{main_feeling}}
variables: [product_name, duration, main_feeling]
```

### 2.5 History（历史笔记）

- 系统自动从 `notes` + `note_metrics` 写入，无需手工。
- 运营者无需写 history 内容。

## 3. 检索优化

### 3.1 关键词覆盖

每份内容在写作时考虑：
- **5 个用户可能搜的查询**（如 "敏感肌粉底推荐"、"夏季底妆"）
- 关键词自然融入内容，**不堆砌**

### 3.2 内容长度

- Persona style_guide：300-800 字
- Rule text：50-200 字（含判据）
- Topic title：5-30 字
- Template content：50-200 字

### 3.3 避免内容重复

- 多份 persona / rule / topic 应有明确边界
- 写之前搜索是否已有相似内容

## 4. 版本管理

- 每次修改 `version += 1`
- 旧版本保留 30 天，可回滚
- 重大修改（tone 大改、forbidden_words 大增）需产品 review

## 4.5 评审流程

不同类型的内容有不同的 review 要求：

| 类型 | severity 门槛 | review 流程 | reviewer |
|---|---|---|---|
| **persona** | - | 创建 / 修改 / 删除 必须 2 人 review（含 1 名产品） | 运营 + 产品 |
| **rule** | severity ≥ 3 | 创建 / 修改 必须 1 人 review | 产品 |
| **rule** | severity ≥ 4 | 必须 2 人 review（含 1 名产品） | 运营 + 产品 |
| **rule** | severity 5（硬规则） | 必须产品 + 安全负责人双签 | 产品 + 安全 |
| **topic** | - | 增量添加无需 review；批量导入需抽样 review | 运营 |
| **template** | - | 创建需 review | 产品 |
| **brand** | - | 任何修改必须产品 review | 产品 |

**review 记录**：所有 review 必须留痕，写入 `kb_review_log` 表（待 schema 补充）：
- `doc_id` / `version` / `reviewer` / `decision` (approve/reject) / `comment` / `ts`

**禁止**：未经 review 完成的 persona / rule 不可被 Agent 检索到（在 `kb_documents.is_published` 字段标记）。

## 5. 质量自检清单

发布前运营者自查：
- [ ] 风格指南有具体判据，不只是抽象描述
- [ ] 至少有 2 个示范笔记
- [ ] 违禁词列表是真正的违禁词（不是常用词）
- [ ] 规则都有判据
- [ ] 选题分类准确
- [ ] 内容长度符合规范
- [ ] 在主控"知识库测试"功能中检索 3 个典型查询，能命中

## 6. 常见错误

| 错误 | 修正 |
|---|---|
| 把品牌定位写进 persona | 品牌定位是 brand 类型，不是 persona |
| 在 persona 里堆大量产品术语 | persona 是"如何说话"，不是"说什么产品" |
| 规则没有判据 | 必须有程序化判据 |
| 选题标题用运营术语 | 改用用户会搜的自然语言 |
| 模板变量没有说明 | 列出变量名 + 取值约束 |
| forbidden_words 写"违禁词"这种空词 | 写具体词，如 "最"、"绝对" |

## 7. Agent 视角的提示

写知识库时想象自己是 LLM：
- "如果我看到这段话，能理解要做什么吗？"
- "如果用户问 X，能检索到这条内容吗？"
- "我有歧义吗？"

## 8. 模板参考

`docs/kb-templates/` 目录提供常用模板（待补充）：
- persona-美妆.yaml
- persona-数码.yaml
- persona-母婴.yaml
- rule-违禁.yaml
- rule-限流.yaml
- topic-季节.yaml
