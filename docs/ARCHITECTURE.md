# 架构说明：控制面与数据面分离

## 当前版本：自主工具 Agent

[tool_agent.py](../app/agent/tool_agent.py) 实现 LangGraph 的 model/tools 条件回环。模型通过原生工具协议选择结构、画像、SQL、图表、澄清或结果选择；服务端绑定用户与数据集，验证参数并执行预算和 SQL 安全校验。

会话、任务和审计由 JSON 或 PostgreSQL 控制面保存，CSV/Excel 内容写入各数据集独立的 DuckDB。消息记忆不是 LangGraph checkpoint，中途故障不能从节点恢复。历史任务可读取，但分析接口只接受自主工具 Agent。

## 1. 为什么要分两个平面

早期的做法是"所有东西都是 JSON 文件"：数据集元信息写 `meta.json`，任务写 `tasks/{id}.json`，报告写 `.md`。单机能跑，但一旦要回答这些问题就撑不住了：

- 谁上传的数据？谁跑的查询？（没有用户概念）
- 过去一周查询失败率多少？P95 多慢？（只能遍历文件统计）
- "销售额"到底按哪个口径算？（每次让模型猜）

PG 和 DuckDB 的分工不是"哪个更先进"，而是**两种负载形态不同**：

| | 控制面（PostgreSQL） | 数据面（DuckDB） |
| --- | --- | --- |
| 数据形态 | 小行数、高频读写、需要事务 | 大宽表、只读分析、列式扫描 |
| 并发 | 多用户并发写 | 单机分析，结果集小 |
| 典型操作 | INSERT / UPDATE / 条件查询 | 全表聚合、GROUP BY |
| 选型理由 | 行存 + MVCC 天然适合 | 嵌入式 OLAP，零运维，分析快 |

把会话状态塞进 DuckDB，或者把 OLAP 查询压到 PG 上，都是错配。

## 2. 整体结构

```text
浏览器 → Streamlit → FastAPI → LangGraph model ⇄ tools
                                  │               │
                     Repository 控制面          DuckDB 数据面
                    PostgreSQL / JSON       每个数据集独立文件
```

用户身份与数据集归属在 API 和服务端验证；工具执行访问 DuckDB，指标与审计访问控制面。默认匿名模式不提供完整身份认证。

## 3. Repository 抽象

### 接口

`app/repository/base.py` 用 `Protocol` 定义，不是 ABC——JSON 实现不需要继承任何东西，保持实现自由。

方法分七组：生命周期（`cache_key` / `close` / `ping`）、用户、数据集、任务、报告、指标、审计、few-shot。

### 行为契约

两个实现必须同时满足这 6 条，否则"可互换"是假的：

1. **时间一律 ISO-8601 字符串**。PG 用 `TEXT` 存，不用 `timestamptz`——否则 psycopg 返回 `datetime`，JSON 返回 `str`，永远无法逐字段比对。
2. `list_*` 按 `created_at` 倒序。
3. `get_*` 对不存在的 id 返回 `None`，不抛异常。
4. `save_*` 对相同主键是 upsert（幂等覆盖）。
5. 返回值必须是**深拷贝**，调用方改它不影响存储。
6. `owner_id=None` 表示不按租户过滤（内部/测试用）。

### 怎么证明抽象真的成立

`tests/test_repository_contract.py` 一套用例，`pytest.fixture(params=["json", "postgres"])` 跑两遍：

```bash
pytest tests/test_repository_contract.py -q                              # 只跑 JSON
TEST_POSTGRES_DSN=postgresql://... pytest tests/test_repository_contract.py -q   # 两个都跑
```

更有说服力的是**业务测试也能换后端跑**：

```bash
REPOSITORY_BACKEND=postgres POSTGRES_DSN=... pytest -q
```

业务测试可配置 PostgreSQL，仓库契约测试分别验证 JSON 和 PostgreSQL 实现。实际通过数量以当前测试运行结果为准。

### 为什么不让 PG 连不上时自动降级到 JSON

因为控制面数据会分叉到两个地方。今天写 PG 失败悄悄写 JSON，明天 PG 恢复了，用户会发现"昨天的任务不见了"。宁可启动失败，也不要数据分裂。

## 4. 指标语义层

### 问题

"销售额"这三个字，模型可能写成 `SUM(amount)`、`SUM(amount*quantity)`、`AVG(amount)`、`SUM(amount) - SUM(refund)`。SQL 都能跑，口径完全不同。评测里"执行成功但结果错"大多来自这里。

### 方案

指标存在 PG 的 `metrics` 表：

```python
Metric(
    name="sales_amount",
    display_name="销售额",
    expression="ROUND(SUM(amount), 2)",
    default_filter=None,
    synonyms=["销售额", "销售金额", "GMV", "成交额"],
)
```

两段式：

1. **注入**：`metrics` 节点把指标字典渲染成 Prompt 段落。模型看到"销售额 = ROUND(SUM(amount), 2)"后，不会再自己猜。
2. **校验**：`audit` 节点对生成的 SQL 做 AST 比对，标注 `pass / info / deviation`。

### 校验为什么不误报

误报一次，用户就再也不看这个功能了。所以设计了四层降级：

| 场景 | 判定 | 理由 |
| --- | --- | --- |
| 问题没提到受管指标 | `not_applicable` | 不校验 |
| 问"最高的一笔"且无 GROUP BY | 不算偏离 | 本来就该 `MAX` |
| 问"平均/占比" | 降级 `info` | 口径变了是预期 |
| `SUM(amount*quantity)` | 降级 `info` | 列超集，可能变口径但不确定 |
| `AVG(amount)` vs `SUM(amount)` | `deviation` | 明确的口径错误 |

**校验只标注，不改 SQL、不改任务状态**。自动纠偏会让准确率基线无法归因。

## 5. 审计与可观测

每个节点写一条 trace（`{node, status, detail, elapsed_ms}`），`audit` 节点落一行 `query_logs`。

两个耗时口径刻意分开：
- `trace[].elapsed_ms` 累加 = 图内耗时
- `run_agent` 的墙钟 = 端到端耗时（含 Python 开销、图调度）

`/api/audit/stats` 返回 `total / success_rate / avg / p95 / slow_queries / by_error_type`。P95 用 nearest-rank 法（小样本下等于最大值），不用 `int(n*0.95)-1` 那种会被低估的写法。

## 6. 轻量身份

不做注册登录，通过 `X-API-Key` 区分调用方：

```
key 存在 -> owner_id = "u_" + sha256(key)[:12]
key 为空 -> owner_id = "anonymous"
```

默认演示模式不验证 Key；开启 `REQUIRE_API_KEY=true` 后，只有 `API_KEYS` 中的 Key 才能访问受保护接口。数据集、任务、审计均带 `owner_id`，跨身份访问返回 404。该机制仍只是静态 Key 验证，缺少完整的用户管理和密钥轮换。

## 7. 一次分析的完整时序

1. API 解析身份并校验数据集归属，服务层恢复或创建会话。
2. `run_tool_agent` 读取可用指标和数据集元信息，LangGraph 中模型选择工具、服务端验证执行并回传。
3. SQL 在 SQLGlot 校验后由 DuckDB 只读执行；错误反馈模型，由调用与时间预算限制重复尝试。
4. 对成功 SQL 做指标口径检查并尽力记录审计，服务层保存任务和会话。用户需要时生成报告。

## 8. 已知取舍

- **`tasks.payload` 用 JSONB 整体存**：字段多且会演进，逐字段建列维护成本高。常用过滤字段（owner/dataset/status/elapsed）额外冗余成列并建索引，兼顾查询与灵活。
- **报告按需生成**：分析结束时只保存任务；用户预览或下载时才生成并保存 Markdown，后续请求复用。生成过程无需额外调用大模型。
- **few-shot 只做存储不做注入**：当前没有接入实际 Agent 路径，也没有做收益评测。
- **不做自动纠偏**：见第 4 节。
