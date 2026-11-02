# 智能数据分析 Agent 平台

> 自然语言提问 → 自动理解表结构 → 生成 SQL → AST 安全校验 → 受限执行 → 失败自动修复 → 输出分析结果，需要时生成报告

基于 **LangGraph** 编排的数据分析平台。用户上传 CSV / Excel 后，由模型在服务端受限工具中自主选择下一步。

系统使用支持 OpenAI 兼容 `tools/tool_calls` 的真实模型（已验证 DeepSeek Flash）；未配置模型时明确提示，不能执行分析。模型选择不等于权限，所有工具都由服务端验证并执行。

---

## 一、它解决什么问题

传统做法是"让大模型生成 SQL 然后直接执行"，这条链路有三个致命问题：

| 问题 | 后果 | 本项目的处理 |
| --- | --- | --- |
| 模型会编造字段名 | 查询报错，用户拿到 500 | Schema 感知 + 数据画像 + 失败后带错误重生成 |
| 模型可能生成 `DROP TABLE` / 读本地文件 | 数据被删、机器被读 | AST 校验、数据表白名单和 DuckDB 外部访问限制 |
| 一次失败就结束 | 体验断裂，用户只能重问 | 执行反馈闭环，SQL 尝试与工具调用受预算限制 |
| 过程是黑盒 | 出错无法定位、无法评估 | 记录模型与工具调用的状态、耗时和轨迹 |

一句话：**它不是一个"SQL 生成器"，而是一个带安全边界和失败恢复能力的数据分析工作流。**

---

## 二、核心设计

### 0. 自主工具 Agent 与多轮会话

自主模式在 LangGraph 中循环执行“模型选择工具 → 服务端执行 → 回传工具结果 → 模型继续或结束”。工具包括 `inspect_schema`（分页）、`profile_columns`、`run_safe_sql`、`build_chart`、`ask_clarification` 和 `select_result`；同一响应中多个调用按顺序执行，并按调用 ID 逐一回传结果。模型可用 `select_result` 指定本轮主要查询结果；未指定时兼容旧行为，返回最后一次成功查询。SQL 错误会返回模型，查询超时则终止本轮。工具结果、多个查询、图表来源、耗时和模型返回的 token 用量可在页面查看。

默认每轮最多 10 次工具调用、4 次 SQL 尝试（含校验失败）、8 次模型调用，以及 150 秒时间预算；时间预算在节点之间检查，并受单次模型/SQL 超时约束，不是强制中断所有代码的端到端 SLA。结果向模型发送有限预览，展示和报告保留多次查询；所属用户、数据集、表白名单都由服务端控制。

关键业务口径不清时，模型可以提问并返回 `needs_clarification`；用户回答后使用同一 `session_id` 继续。会话按用户和数据集隔离，JSON/PG 保存完整消息轮次并用版本号检测并发更新；保留最近最多 8 轮，尽量控制历史大小，超长单轮仍可能超过目标。侧边栏可恢复和删除会话记忆，删除记忆不会删除已有任务、报告及审计。

每个分析任务使用 LangGraph SQLite checkpointer 同步保存已完成节点的状态。服务中断后，可在侧边栏“恢复中断的分析任务”继续，或调用 `POST /api/task/{task_id}/resume`；服务端会验证用户、数据集和会话版本。`DELETE /api/task/{task_id}/checkpoint` 仅删除该任务的检查点，不删除任务、报告或审计。澄清后的回答仍是新的会话请求，并非图内 `interrupt` 恢复。

检查点保存在 `storage/checkpoints.sqlite3`，可能包含问题、会话和查询预览，应保护存储目录并按需清理；删除会话记忆不会自动删除检查点。恢复以节点为单位，发生中断的节点可能重新执行，不能保证外部操作恰好执行一次。已完成的调用次数保留，但恢复时重新计算本次时间预算；SQLite 与文件锁要求工作进程共享本机存储，不提供跨机器高可用。

查询证据根据 SQL 校验器识别的真实授权表判断，空结果也可以支撑“无匹配记录”；仅询问表结构时允许基于结构工具答复。结构问题识别是保守规则，证据标记不能证明最终自然语言答案与查询结果一致，重要结论仍需检查 SQL 和结果。

每次分析只保存任务和结果；需要报告时，在对应结果的“📄 报告”标签中点击“生成报告”。报告使用已有分析结果生成 Markdown，无需额外调用模型；生成后会保存并在后续预览或下载时复用。直接请求报告 API 也会按需生成报告。

### 1. SQL 安全：多层校验

SQLGlot 将 SQL 解析为语法树，检查语句类型、各查询作用域内的表引用和函数；DuckDB 再以只读、禁用外部访问的配置执行。

| 闸门 | 拦截内容 | 实现位置 |
| --- | --- | --- |
| ① 单语句 | `SELECT ...; DROP TABLE ...` | `parse()` 返回多节点即拒绝 |
| ② 只读 | INSERT / UPDATE / DELETE / DROP / CREATE / COPY / ATTACH / MERGE… | AST 节点类型判断 |
| ③ 表白名单 | 访问未上传的表、`information_schema` | 按查询作用域识别 CTE 和实际表 |
| ④ 函数限制 | 读文件及动态执行 SQL 的表函数 | 禁止表函数并检查危险函数 |
| ⑤ 行数与超时 | 结果过大、查询耗时过长 | 注入 LIMIT + 只读连接 + `interrupt()` 超时 |

只读连接同时禁用外部文件和网络访问。LIMIT 限制返回行数，**不保证减少扫描量**；超时中断也不是资源隔离。上述措施适合演示，不应视为完整的恶意 SQL 沙箱。

### 2. 查询失败后的反馈与观测

`run_safe_sql` 返回安全校验或执行错误后，模型可在剩余预算内修改 SQL 并再次调用；超时会终止本轮。工具调用、查询结果、耗时和错误保存在 trace 中，但模型不一定选择修复，也不保证修复成功。恢复时从最近持久化的节点继续；若图已完成但任务结果尚未落库，可直接完成保存。

### 3. 可观测性

页面展示模型与工具轨迹、各次 SQL、结果 ID、Token 和耗时。审计写入失败会产生警告；图异常提前返回的路径不保证审计完整。

### 4. 指标语义层：提供可检查的计算口径

同一个"销售额"，模型可能写成 `SUM(amount)`、`SUM(amount*quantity)`、`AVG(amount)`。SQL 都能跑，口径完全不同——评测里"执行成功但结果错"大多来自这里。

做法是两段式：

**注入**——自主 Agent 从所选控制面读取指标字典，渲染进 Prompt：

```
【指标口径定义】
- 销售额（sales_amount）= ROUND(SUM(amount), 2)，单位：元
  同义说法：销售额、销售金额、GMV、成交额
```

**校验**——查询后对各条成功 SQL 做启发式 AST 比对，标注 `pass / info / deviation`。

校验的关键不是"能查出问题"，而是**不误报**。误报一次，用户就再也不看这个功能了。所以加了四层降级：

| 场景 | 判定 | 理由 |
| --- | --- | --- |
| 问题没提到受管指标 | `not_applicable` | 不校验 |
| 问"最高的一笔"且无 GROUP BY | 不算偏离 | 本来就该用 `MAX` |
| 问"平均 / 占比" | 降级 `info` | 口径变了是预期 |
| `SUM(amount*quantity)` | 降级 `info` | 列超集，可能变口径但不确定 |
| `AVG(amount)` vs `SUM(amount)` | `deviation` | 明确的口径错误 |

**校验只标注，不改 SQL 或任务状态。** 这不是强制执行的语义层，复杂查询仍可能误报或漏报；默认指标需要主动调用 `/api/metrics/seed` 初始化。

---

## 三、架构：控制面与数据面

PG 和 DuckDB 不是"哪个更先进"的关系，而是两种负载形态不同：

| | 控制面（PostgreSQL） | 数据面（DuckDB） |
| --- | --- | --- |
| 存什么 | 用户、数据集元信息、任务、报告、指标、审计 | 上传后的宽表数据 |
| 负载 | 小行数、高频读写、要事务 | 大表聚合、只读分析 |
| 为什么是它 | 行存 + MVCC，天然适合并发写 | 嵌入式 OLAP，零运维，分析快 |

把会话状态塞进 DuckDB，或者把 OLAP 查询压到 PG，都是错配。

```text
浏览器 → Streamlit → FastAPI → LangGraph model ⇄ tools
                                  │               │
                     Repository 控制面          DuckDB 数据面
                    PostgreSQL / JSON       每个数据集一个数据库
```

工具由服务端绑定用户及数据集；查询经 SQL 校验后访问 DuckDB，会话、元数据、任务、报告及审计保存在控制面。

### 为什么要做 Repository 抽象

一套 `MetadataRepository` 接口，两个实现（PostgreSQL / JSON 文件）。JSON 实现不是"降级方案"，而是**验证抽象是否真的成立的手段**：

- 跑测试用 JSON，零外部依赖，秒级完成
- 部署用 PostgreSQL，真实并发与持久化
- 契约测试对两种实现运行相同断言；业务测试也可切换 PostgreSQL

接口契约有 6 条硬约束（详见 `docs/ARCHITECTURE.md`），其中最容易被忽略的是：**时间字段一律 ISO-8601 字符串**。PG 若用 `timestamptz`，psycopg 会返回 `datetime`，而 JSON 只能给 `str`，两个实现就永远无法逐字段比对。

## 四、快速开始

### 方式一：本地运行

```bash
# 使用 Python 3.12，安装经过验证的依赖版本
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock

# 配置支持工具调用的模型；留空时无法运行分析
cp .env.example .env
# 编辑 .env，填入服务地址、模型名和 LLM_API_KEY
# 仓库已包含演示数据，无需重新生成

# 启动后端
uvicorn app.main:app --reload --port 8000
# 接口文档：http://127.0.0.1:8000/docs

# 启动前端（另开一个终端并激活 .venv）
streamlit run frontend/streamlit_app.py
# 打开：http://127.0.0.1:8501
```

前端左侧点 **「加载演示数据」** 即可开始提问。

演示建议：先问“有哪些表和字段？”，再问“按地区统计销售额并画柱状图”，检查工具轨迹、SQL 和图表；继续问“华南地区呢？”观察同一会话续问，最后在需要时生成报告。演示前请确认模型、网络和 Key 可用；模型自主选择工具，具体调用顺序和结果可能变化。

![DeepSeek 演示：按地区查询销售额](docs/evaluation/demo_ui.png)

例如使用 DeepSeek，可在 `.env` 中设置 `LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash` 和自己的 `LLM_API_KEY`。密钥只保存在本地 `.env`，不要提交到仓库。模型服务会收到问题、表结构及查询结果样本，请勿使用未经授权的敏感数据。

### 方式二：Docker

```bash
docker compose up --build
# API:     http://127.0.0.1:8000/docs
# Web:     http://127.0.0.1:8501
```

Docker 默认只监听本机。未设置 `REPOSITORY_BACKEND` 时使用 PostgreSQL，启动 API 前会幂等执行迁移；`.env` 中若显式配置 `REPOSITORY_BACKEND=json` 则使用本地 JSON。端口可通过 `API_PORT`、`WEB_PORT` 和 `POSTGRES_PORT` 调整。

### 手动试用一次

```bash
# 上传
curl -X POST http://127.0.0.1:8000/api/upload \
  -F "files=@data/demo_sales.csv" \
  -F "files=@data/demo_products.csv"

# 分析
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"dataset_id":"<上一步的 id>","question":"各地区的销售额分别是多少？","mode":"agent"}'
```

API 省略 `mode` 时默认使用自主工具 Agent；仅接受 `agent`。返回 `session_id` 后，后续问题带上该 ID；如返回 `needs_clarification`，用户回答同样通过该接口提交。历史任务仍可读取。

---

## 五、接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/upload` | 上传 CSV / Excel，支持多文件（每个文件一张表） |
| GET | `/api/schema/{dataset_id}` | 获取表结构、字段类型、行数 |
| POST | `/api/analyze` | 提交自然语言分析任务，同步返回完整结果 |
| GET | `/api/conversations?dataset_id=...` | 列出当前用户在该数据集的历史会话 |
| GET / DELETE | `/api/conversations/{session_id}` | 恢复会话 / 删除会话记忆 |
| GET | `/api/task/{task_id}` | 查询任务状态 |
| GET | `/api/report/{task_id}` | 按需生成或获取 Markdown 报告 |
| GET | `/api/report/{task_id}/download` | 按需生成并下载报告文件 |
| GET | `/api/datasets` | 列出当前租户已上传的数据集 |
| GET | `/api/metrics` | 列出指标定义（含全局指标） |
| POST | `/api/metrics` | 新建/更新指标（按 name 幂等） |
| DELETE | `/api/metrics/{name}` | 删除自己的指标 |
| POST | `/api/metrics/seed` | 预置默认指标 |
| GET | `/api/audit/logs` | 查询审计记录 |
| GET | `/api/audit/stats` | 成功率 / P95 / 慢查询 / 错误分布 |
| GET | `/api/agent/graph` | 返回自主工具图拓扑和注册工具 |
| GET | `/api/health` | 健康检查，含当前推理引擎模式 |

`/api/analyze` 返回示例：

```json
{
  "task_id": "fd912f8c9f11",
  "status": "success",
  "sql": "SELECT region, ROUND(SUM(amount), 2) AS total_sales FROM \"demo_sales\" GROUP BY region ORDER BY total_sales DESC",
  "columns": ["region", "total_sales"],
  "data": [{"region": "华东", "total_sales": 434296.44}],
  "row_count": 7,
  "analysis": "华东地区销售额最高，占总量的 29.0%…",
  "insights": ["「华东」在 total_sales 上最高，为 434,296.44"],
  "chart": {"type": "bar", "x": "region", "y": "total_sales"},
  "chart_figure": {"data": [...], "layout": {...}},
  "trace": [{"node": "schema", "status": "ok", "elapsed_ms": 157.0}],
  "repair_history": [],
  "retry_count": 0,
  "elapsed_ms": 243.97,
  "llm_mode": "openai-compatible"
}
```

---

## 六、测试与评测

### 自动化测试

```bash
# 默认 JSON 后端，无需外部数据库
pytest -q

# 双实现契约：使用专门的测试库；测试会清空 TEST_POSTGRES_SCHEMA 中的表
TEST_POSTGRES_DSN=postgresql://daa:daa@localhost:5432/daa \
  pytest tests/test_repository_contract.py -q

# 全量测试使用 PostgreSQL，并运行双实现契约
REPOSITORY_BACKEND=postgres POSTGRES_DSN=postgresql://... \
  TEST_POSTGRES_DSN=postgresql://... pytest -q
```

测试数量和结果以当前运行输出为准；PostgreSQL 测试须使用独立测试库。

覆盖重点：

- **SQL 安全**：覆盖写操作、多语句、未知表、CTE 别名绕过、动态 SQL 和外部文件访问。
- **行数上限**：无 LIMIT 时注入、小 LIMIT 保留、超大 LIMIT 收紧。
- **执行反馈**：使用模型替身检查查询错误回传及后续调用，不代表真实模型修复成功率。
- **防死循环**：验证调用预算、SQL 尝试上限、重复调用检查和超时处理。
- **读写隔离**：只读连接不能写表，且禁用外部文件访问。
- **控制面契约**：提供 JSON/PostgreSQL 双后端测试，PostgreSQL 路径需单独配置隔离的测试库。
- **指标误报防护**：`MAX(amount)` 配"最高的一笔"不能判偏离；`AVG` 配"平均"要降级 `info`。
- **租户隔离**：跨租户取 dataset / task / report 全部 404（不是 403）。
- **检查点恢复**：覆盖进程中断、已完成图的结果补存、并发恢复、会话版本冲突和跨用户访问。

### 评测

[Spider 适配评测说明](docs/evaluation/Spider自主Agent适配评测_20261101.md)：公开开发集抽样 50 题，34 题通过 SQLite/CSV/DuckDB 参考一致性预检；一次运行主要 SQL 严格匹配 5/34。同模型 Flash 裁判两次各给 33/34 三维通过，但没有人工校准，不能当真实准确率。代码与运行配置在实验后又有调整，历史数值不代表当前版本新测结果。

[探索性列投影分析](docs/evaluation/spider_holdout50_projection_exploratory_20261101.json)：从 34 题中按参考列名精确匹配，仅 21 题可比，其中 16 题匹配；另 13 题因列名无法唯一映射而不评分。规则是在查看结果后设计的，仅用于排错，不是预注册成绩。[34 题人工复核工作表](docs/evaluation/Spider逐题人工复核工作表_待填写.md)尚待人工填写。

另有 [10 道中文合成样例自查](docs/evaluation/中文Agent样例自查_20261101.md)：调整提示前 9/10 严格匹配；针对失败题修改提示后只复测该题一次通过，不能推断总体提升。

`scripts/run_agent_eval.py` 是 5 题合成开发自查，不能当独立准确率；`scripts/eval_spider_pilot.py`、`scripts/judge_spider_answers.py` 和 `scripts/eval_spider_projection.py` 分别用于适配评测、裁判与离线重分析。真实模型调用脚本限制使用 DeepSeek Flash，调用会产生费用。

---

## 七、能力边界（写清楚比吹嘘更有用）

**能做到**
- 单表 / 多表 JOIN 的结构化查询、分组聚合、TopN、时间窗口、条件筛选
- 模型可根据 SQL 校验或执行错误在预算内尝试修复，不能保证成功
- 对部分缺少字段的问题主动拒绝（例如演示数据缺少利润字段）
- 自主选择受限工具、多次查询、按口径澄清并在会话中继续；记录调用与结果

**做不到 / 刻意不做**
- **因果分析**：只能说"相关、趋势、异常"，不会声称"A 导致 B"。观察性数据做因果需要额外实验设计，这不是 SQL 能解决的。
- **跨机器高可用恢复**：目前使用本机 SQLite 和文件锁，未实现分布式检查点或跨机器协调。
- **复杂 BI 语义**（同环比、留存、漏斗）：可以靠模型生成，但缺少语义层保证，容易出"看起来对但口径错"的结果。
- **超大数据集**：DuckDB 单机 + 500 行返回上限，定位是单机分析，不是数仓。

**已知风险**
- 指标层只提示和事后检查，不强制改写查询；实际业务需核对退款、去重、日期等口径。
- 默认匿名模式和自选 Key 只用于本地演示；启用认证后也只是静态密钥白名单，不包含完整的账号管理或密钥轮换。
- JSON 存储适合单进程演示，不支持跨进程并发写入；真实多用户部署还需网络隔离、资源限制、认证授权和运维方案。
- LLM 输出不稳定；`temperature=0` 也不保证完全一致，应重复评测，并使用未参与调参的独立测试集。
- 用户问题、Schema、字段样例及查询结果预览可能发送给所配置的模型服务；会话记忆、任务和报告也会保存相关内容。敏感数据使用前应确认服务商的数据处理规则并按需脱敏。

---

## 八、配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `REPOSITORY_BACKEND` | `json` | 控制面后端：`json`（本地文件）/ `postgres` |
| `POSTGRES_DSN` | 空 | PostgreSQL 连接串，`REPOSITORY_BACKEND=postgres` 时必填 |
| `POSTGRES_SCHEMA` | `public` | 控制面表所在 schema |
| `POSTGRES_POOL_SIZE` | `5` | 连接池大小 |
| `REQUIRE_API_KEY` | `false` | true 时仅允许预先配置的密钥；未配置密钥则启动失败 |
| `API_KEYS` | 空 | 服务端允许的 Key，逗号分隔；空时回退到 `API_KEY` |
| `API_KEY` | 空 | 前端使用的 Key，也可作为服务端唯一允许的 Key |
| `METRICS_ENABLED` | `true` | 指标语义层开关（关闭后 Prompt 与旧版逐字符相同） |
| `AUDIT_ENABLED` | `true` | 查询审计开关 |
| `FEW_SHOT_ENABLED` | `false` | few-shot 示例库开关 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | 支持工具调用的 OpenAI 兼容接口 |
| `LLM_API_KEY` | 空 | 真实模型密钥；留空不能运行分析 |
| `LLM_MODEL` | `deepseek-flash` | 模型名 |
| `LLM_TEMPERATURE` | `0` | 建议 0 以减少波动，但不保证输出完全一致 |
| `USE_MOCK_LLM` | `false` | 设为 true 禁止真实模型调用，分析不可用 |
| `AGENT_MAX_TOOL_CALLS` | `10` | 自主模式每轮工具调用预算 |
| `AGENT_MAX_SQL_CALLS` | `4` | 自主模式每轮 SQL 尝试预算，包含校验失败 |
| `AGENT_MAX_MODEL_CALLS` | `8` | 自主模式每轮模型调用预算 |
| `AGENT_TIMEOUT_SECONDS` | `150` | 自主模式节点间时间预算（非硬性进程级超时） |
| `SQL_TIMEOUT_SECONDS` | `15` | 单查询超时 |
| `MAX_RESULT_ROWS` | `500` | 结果行数上限，同时作为强制 LIMIT |
| `MAX_UPLOAD_MB` | `50` | 单次请求所有文件合计大小上限 |
| `STORAGE_DIR` | `./storage` | 数据集与任务存储目录 |

---

## 九、目录结构

```text
app/agent/tool_agent.py         自主工具图、参数校验、预算与结果选择
app/agent/llm.py                模型工具调用客户端
app/tools/                      SQL 安全、画像、指标和图表
app/database/duckdb_manager.py  数据库连接、超时和返回行限制
app/repository/                 JSON / PostgreSQL 控制面
app/services/                   上传、分析、任务和报告
frontend/streamlit_app.py       前端
scripts/                        数据、评测与数据库初始化脚本
tests/                          自动化测试
docs/evaluation/                评测原始记录、口径与复核工作表
Dockerfile / docker-compose.yml  构建与启动
```

---
