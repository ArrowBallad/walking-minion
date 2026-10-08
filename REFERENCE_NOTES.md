# 下一个 clinical-agent 项目的继承说明

最后核对：2026-10-08（Asia/Hong_Kong）

本文件用于新项目的设计交接。`cat-on-the-bench` 应被视为只读参考仓库：不要在这里继续实现网页 demo，不要修改既有 PhysicianBench task、verifier、controller 或冻结实验。新项目只复制经过选择的最小概念，并通过本文列出的原始路径回查证据。

## 1. 先读结论

最值得继承的不是某个完整 controller 版本，而是下面四个边界：

1. 官方 MiniAgent 的通用 tool-calling loop：模型自由选择查询、写入和停止时机。
2. lossless storage + selective context：原始工具结果完整保存，给模型的是按时间、状态和相关性重建的工作视图，而不是不断增长并被截断的 message history。
3. verified side effects：写操作经过机械 guard，执行后 read-back；写响应本身不等于成功。
4. run-level observability：每次运行有独立 workspace、原始 tool/model/HTTP/graph 日志、预算账本、metadata 和 evaluator 结果。

不要继承以下控制协议：

- v1/v2 的 Task Contract → requirement → gap → evidence/action dependency 硬门控；
- v3.1 的“首轮必须抽取 tracker，所有 item 离开 pending 才能 finalize”协议；
- 用 controller 内部的 `complete` 声称临床正确或证据充分；
- 把较少调用、较低 token 当作效率提升，而不检查是否只是提前失败。

推荐的新核心是：

```text
Original task
    ↓
Main agent ── read proposal ──→ patient-scoped read tools
    ↑                                │
    │                                ↓
    └── WorkingSet ← PatientBeliefState ← EvidenceIndex ← RawEvidenceStore
    │
    ├── write proposal → Guard → Execute → Verify/read-back ──┐
    │                                                         │
    └──────────────────── updated execution/evidence state ←──┘
```

任务进度清单可以存在，但默认只作为 advisory reminder。患者绑定、schema、路径、预算、exact duplicate、写后验证等机械事实才适合 hard gate。

## 2. 事实层级：不要把设计稿当运行事实

| 层级 | 含义 | 主要证据 |
| --- | --- | --- |
| 官方基线 | PhysicianBench 实际接口、14 个工具、MiniAgent loop、runner/evaluator | `PhysicianBench/agent/`, `PhysicianBench/tools/`, `PhysicianBench/scripts/` |
| 当前已实现 | `clinical-controller/` 中 v3.1 的 schema、state、temporal context、guard/verify、日志 | `clinical-controller/src/clinical_controller/` |
| 冻结运行证据 | v2/v3/v3.1 的聚合结果和原始 job artifacts | `docs/final-evaluation-*.md`, `experiments/final-evaluation-*` |
| 后续设计方向 | RawEvidenceStore、EvidenceIndex、PatientBeliefState、WorkingSet、soft progress monitor | `尝试从miniagent一层层加.md` |

最后一行是从失败分析中推导出的下一步架构，当前仓库没有把它完整实现。特别是 `RawEvidenceStore`、可解引用的 evidence pointer 和独立 `PatientBeliefState` 仍是设计概念，不应被描述成已完成组件。

## 3. 官方 benchmark 和运行数据链路

### 3.1 Task 输入与工作目录

一个 task 的核心输入是：

```text
PhysicianBench/tasks/v1/<task>/
├── instruction.md
├── input_files/          # 可选
└── tests/test_outputs.py # evaluator 使用，不应提供给 agent
```

官方单任务 runner 的实际链路：

```text
task/instruction.md
    ↓ replace /workspace/ with this run's absolute workspace path
job_dir/workspace/{input_files, output/}
    ↓
fresh fhir-full:v1 container → FHIR_BASE_URL
    ↓
MiniAgent.run(instruction)
    ↓ side effects
job_dir/workspace/output + container FHIR state
    ↓
scripts/run_eval.py → task pytest verifier
    ↓
job_dir/logs + metadata.json
```

原始入口为 `PhysicianBench/scripts/run_task.py`；job 命名和 metadata 写入见 `PhysicianBench/scripts/job_manager.py`。每次运行必须使用新的绝对 `job_dir` 和新容器，不能在冻结实验目录上重跑。

### 3.2 官方 14 个工具

模型可见接口由 `PhysicianBench/agent/tool_registry.py` 中手写的 OpenAI function schema 决定；Python 实现在 `PhysicianBench/tools/fhir_api_functions.py` 和 `PhysicianBench/tools/file_tools.py`。

| 类别 | 工具 |
| --- | --- |
| 9 个读取 | Condition、lab Observation、vital Observation、Patient、Procedure、MedicationRequest、DocumentReference、ServiceRequest、social-history Observation 搜索 |
| 4 个 FHIR 写入 | MedicationRequest、Communication、ServiceRequest、Appointment create |
| 1 个文件写入 | `write_file` |

统一查询返回形状：

```json
{
  "entries": ["FHIR resource objects"],
  "total": "FHIR Bundle total or null",
  "pages": "number of pages actually read"
}
```

需要保留的接口事实：

- `entries` 是资源数组，不是完整 Bundle；`total` 可能大于 `len(entries)`。
- 搜索通过 Bundle `next` link 分页，但受 `page_limit` 限制；“没搜到”不是全库不存在的证明。
- schema 里的 `default` 只是模型提示，registry 不做 JSON Schema 校验，也不会注入 default；真实缺省值来自 Python 函数签名。
- 部分 schema default 与 Python default 不一致。完整差异已列在 `preparation/PhysicianBench-interface-and-runbook.md`，原始机器可读接口在 `preparation/tool-interfaces.json`。
- Python 函数有 `base_url`、认证、timeout 等非公开参数，不能误称为模型可见字段。
- `DocumentReference` 只会解码内嵌的 text attachment；没有注册 Binary.Read、通用 HTTP、shell 或 `read_file`。
- 官方 `write_file` 没有 workspace confinement；新项目必须在 adapter/guard 层限制路径。

不要手工重抄 14 份 schema 作为第二事实源。新项目如果仍对接 PhysicianBench，应在启动时从固定 commit 的 registry 导入或生成 snapshot，并测试 schema、函数签名和模型可见工具集的一致性。

### 3.3 官方 MiniAgent tool-calling loop

实现位置：`PhysicianBench/agent/mini_agent.py`。

```text
messages = [system, user]
tools = registry.to_openai_tools()

for step in 1..max_steps:
    response = client.chat(messages, tools=tools, ...)
    if response has no tool_calls:
        return response.content
    append assistant message including tool_calls
    for tool_call in response.tool_calls:
        json.loads(arguments)
        registry.dispatch(name, args)
        json.dumps(result)
        append role=tool with matching tool_call_id
return max-step status
```

实际设置和行为：

- `run_task.py` 默认 `max_steps=200`；`MiniAgent` 类本身默认 30。
- `temperature=None` 表示 API default。
- `parallel_tool_calls=True` 是模型请求选项；Python 仍按返回顺序逐个 dispatch，不是并发执行。
- 当前 DeepSeek adapter 不发送 `parallel_tool_calls`，因为它不属于该 provider 的已记录请求参数。
- 单次工具结果发给模型时最多 10,000 字符；trajectory 默认记录传给模型的同一字符串。因此超长结果在 message context 中有尾部丢失风险。
- malformed arguments、unknown tool 和 Python exception 会转换为 error object，反馈给模型。
- 连续相同 error、相同 call+args+result、重复 batch、长期没有新 `(tool, args)` 会终止循环。
- 模型没有 tool call 即视为 agent 结束；这只代表控制流停止，不等于 benchmark 通过。

工具 registry 的实际边界很薄：`dispatch(name, arguments)` 直接执行 `func(**arguments)`，异常通常包装成 `{"error": ...}`。它没有 patient binding、read/write 分离、路径限制、写入去重或 read-back。

### 3.4 模型/provider 设置

`PhysicianBench/agent/llm_client.py` 支持 DeepSeek、OpenRouter、Anthropic 和 OpenAI-compatible 路径。本项目冻结评估使用：

```yaml
model: deepseek-v4-flash
provider: deepseek
reasoning_effort: high
python: 3.12.x
fhir_image: fhir-full:v1
```

秘钥只从环境变量读取：`DEEPSEEK_API_KEY`、`OPENROUTER_API_KEY`、`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`。不要复制 `PhysicianBench/.env`；新建自己的 `.env`，并保持 ignore。

Provider adapter 会对部分可重试 API/连接错误指数退避。新项目必须分别记录：logical model call、SDK/HTTP retry、tool call、graph/node transition；不能把 graph step 当成 inference count。

## 4. 建议继承的数据结构

这里同时列出“现有实现”和“下一项目建议抽象”。建议复制概念和测试，不要无选择复制整个 `ClinicalState`。

### 4.1 现有实现：EvidenceItem

来源：`clinical-controller/src/clinical_controller/schemas.py`。

```yaml
EvidenceItem:
  id: stable evidence id
  patient_id: bound patient
  value: compacted/raw-ish resource value
  source: source tool and/or resource reference
  retrieved_at: retrieval timestamp
  key: grouping key, normally resource type + coding token
  time: clinical timestamp, nullable
  status: observed | verified | missing | conflict
  resource_type: FHIR resource type
  resource_status: original resource status
  encounter: encounter reference
  references: resource references
  retrieval_state: successfully_retrieved | absent | unavailable | failed | unresolved
```

可继承之处：患者作用域、临床时间与检索时间分离、provenance、resource status、missing/unavailable/failed 区分。

需要改造之处：当前 `value` 仍可能携带较大嵌套对象，也没有独立 `raw_ref`。新项目应让 normalized evidence 只保存可索引字段和 `raw_ref`，原始 JSON 单独落盘或放入 append-only store。

### 4.2 现有实现：ClinicalState

来源：`clinical-controller/src/clinical_controller/state.py`。它混合了五类状态：

| 类别 | 当前字段示例 | 下一项目处理 |
| --- | --- | --- |
| 任务 | `task_id`, `instruction`, `task_tracker` | 保留 task；tracker 改 soft/advisory |
| 临床证据 | `evidence`, `clinical_context`, `recent_observation_ids` | 拆成 RawEvidence、EvidenceIndex、BeliefState、WorkingSet |
| 执行事务 | `actions`, `receipts`, `verified_actions`, `verification` | 保留并独立成 ExecutionState |
| 控制与错误 | `budget`, fingerprints, errors, termination reason | 保留机械事实；错误 taxonomy 继续使用 |
| 遥测 | events、parse/context telemetry、expensive usage | 保留，改为 append-only run artifacts |

### 4.3 下一项目建议的最小数据模型

```yaml
RawEvidenceRecord:
  evidence_id: string
  run_id: string
  patient_id: string
  source_tool: string
  request: object
  retrieved_at: datetime
  resource_type: string|null
  resource_id: string|null
  raw_ref: content-addressed file/object reference
  raw_sha256: string

EvidenceCard:
  evidence_id: string
  patient_id: string
  resource_type: string
  resource_id: string|null
  clinical_time: datetime|null
  status: string|null
  code_tokens: [string]
  display: string|null
  value_summary: object
  encounter_ref: string|null
  flags: [missing_time, truncated_source, conflict, superseded]
  raw_ref: string

BeliefClaim:
  claim_id: string
  subject: string
  predicate: string
  value: any
  temporal_state: current|historical|superseded|unknown
  confidence: low|medium|high
  evidence_refs: [evidence_id]
  conflict_refs: [evidence_id]
  updated_at: datetime

WorkingSet:
  turn_id: string
  selected_claims: [claim_id]
  selected_evidence: [evidence_id]
  compact_views: object
  omitted_counts: object
  byte_size: integer
  selection_reasons: object

ExecutionState:
  proposed_actions: object
  approvals: object
  receipts: object
  verification_results: object
  attempted_read_fingerprints: [string]
  attempted_write_fingerprints: [string]
  budgets: object
  errors: [object]
```

关键不变量：

- RawEvidenceStore append-only、lossless；任何压缩或 belief 更新都不覆盖原始证据。
- 每个 EvidenceCard 和 BeliefClaim 都能回到 `raw_ref`。
- `clinical_time` 与 `retrieved_at` 永远分开；未知时间不能用检索时间替代。
- conflict 是一等状态，不通过“选最新一条”静默删除。
- WorkingSet 是本轮注意力，不是 patient truth；未进入 prompt 的记录仍可解引用。
- write receipt 和 verified action 是不同类型；只有 read-back 匹配才进入 verified set。

## 5. 临床数据整理与 context 构建

当前 `build_clinical_context()` 位于 `clinical-controller/src/clinical_controller/context.py`，实现了以下通用整理：

- 从 FHIR 常见字段选择临床时间；
- 按 `resourceType + coding` 分组；
- medication 区分 current 与 historical/superseded，并保留 unknown time；
- Observation 提供 latest 和最多八个 trend points；
- notes、imaging、procedures、other records 按时间倒序；
- 同一 key、同一时间、不同临床 payload 标记 conflict；
- verified actions 回填 context；
- 每个长字符串最多 2,000 字符；临床 snapshot 最多 48,000 UTF-8 bytes；
- 超限时按固定优先级裁剪低优先级尾部，并保留 source references 供重查。

这些机制在 v3 的部分案例中修复了 current/historical medication 和来源冲突，但不是成熟的 clinical state engine：

- “current” 仍主要依赖 FHIR status 和最新记录，不能等同于患者实际服用；
- 选择相关性的词项匹配很浅；
- notes 没有 section-aware index 或按需展开；
- 48 KB 只约束 clinical snapshot，不约束 task、tool schema、tracker、错误等总 payload；
- 原始 resource 当前仍主要依赖 run artifact，并未成为正式可解引用存储服务。

下一项目应把链路拆清楚：

```text
tool result
  ├── raw JSON → RawEvidenceStore (lossless)
  ├── one card per resource/query result → EvidenceIndex
  ├── deterministic temporal/status/conflict reducer → PatientBeliefState
  └── task + recency + unresolved conflicts + newly observed evidence
          → bounded WorkingSet → agent prompt
```

对资源的低风险 normalization：

| Resource | 建议保留 | 禁止自动推断 |
| --- | --- | --- |
| Condition | code/text、clinical status、onset/recorded time、encounter | active resource = 当前真实问题 |
| MedicationRequest | drug、dose、frequency、status、authoredOn、intent | active = 患者正在服药 |
| Observation | code、value、unit、effective time、interpretation、component | latest value = 足够做临床决策 |
| DocumentReference | date、type/title、长度、section/short preview、raw pointer | 截断片段代表完整 note |
| Procedure/ServiceRequest | status、code、time、workflow refs | resource 存在 = 操作已完成 |
| 未知类型 | resourceType、id、time、code/text、status、raw pointer | 无 normalizer = 丢弃/阻塞 |

原则是：优化失败时退化成更慢、更啰嗦和按需展开，而不是停止整个任务。

## 6. 写操作：应原样继承的边界

### 6.1 Guard 只判断机械事实

当前代码位置：

- `clinical-controller/src/clinical_controller/nodes/action_guard.py`
- `clinical-controller/src/clinical_controller/live/environment.py`

建议 hard checks：

- tool 属于允许的 write allowlist；
- 参数符合模型可见 schema，且无未知字段；
- patient 已从 scoped Patient read 绑定；
- FHIR write 的 `patient_reference == Patient/<bound id>`；
- `write_file` resolve 后仍位于本 run workspace；
- canonical `(tool, sorted args)` fingerprint 未写过；
- 至少还有 execute + verify 所需预算。

不建议 hard checks：

- action 必须绑定某个 requirement/gap ID；
- supporting evidence 必须命中 controller 自定义 ID；
- Python 判断证据已足够、治疗失败或医学选择正确；
- 未同步的 progress item 阻止一切写入或结束。

### 6.2 Execute、receipt、Verify 必须分离

当前代码位置：

- `clinical-controller/src/clinical_controller/nodes/execute.py`
- `clinical-controller/src/clinical_controller/nodes/verify.py`

```text
proposed action
  → approved action fingerprint
  → execute once
  → receipt (untrusted completion signal)
  → read-back/search
  → compare patient + resource id + approved key fields
  → verified action OR unresolved verification
```

如果写请求可能已到达服务器但响应丢失，不要盲目重试：先按 action key 搜索已存在副作用；只在确认没找到且预算允许时进行一次 bounded retry。

当前公开工具只能可靠 read-back：

- `ServiceRequest`：通过 `fhir_service_request_search`；
- `MedicationRequest`：通过 `fhir_medication_request_search_orders`；
- `write_file`：本地读取并比较 exact content。

`Communication` 和 `Appointment` 没有匹配的公开读取工具。新项目必须明确标为 `verification_capability=unavailable`，不能因为 create receipt 有 id/status 就升级为 verified。若 demo 需要这两类写入，要么扩展正式工具能力并单独测试，要么在 UI 中诚实显示“已提交但未验证”。

## 7. 结构化模型输出：可以继承，但不要让协议变脆

`clinical-controller/src/clinical_controller/live/support.py` 已实现一个通用恢复顺序：

1. 整段严格按 Pydantic schema 解析；
2. 从 prose/fence/DSML 中流式找 JSON object，仅当恰好一个 unique schema-valid payload 时接受；
3. 使用 invalid output、schema 和最小上下文进行一次只修结构、不重做临床推理的 repair；
4. 仍无效则 `blocked_schema`。

v3.1 的 43 次正常 Agent move 中，15 次 strict、8 次 wrapper extraction、20 次 repair success、0 次 repair failure。这个 parser 解决了 v3 的包装格式问题，值得保留。

失败发生在 parser 之后：模型没有在首轮生成 tracker、后续重复 `new_task_items`，或重复 read，被硬协议拒绝。因此新项目应：

- 让模型输出 schema 尽量只描述“这一步要做什么”；
- 由 controller 容忍重复 advisory metadata，而不是直接终止；
- tracker/progress 从 task 或运行历史派生，更新失败退化为 warning；
- parser repair 不改变语义，不用 repair call 偷做第二次临床推理。

## 8. 轨迹、artifact 与错误 taxonomy

### 8.1 每个 run 建议保存

```text
runs/<run_id>/
├── metadata.json
├── config.json
├── workspace/
│   └── output/
├── raw-evidence/
│   └── <sha256>.json
├── logs/
│   ├── model-calls.jsonl
│   ├── tool-calls.jsonl
│   ├── http-calls.jsonl
│   ├── state-events.jsonl
│   ├── verification.jsonl
│   └── parse-recovery.jsonl
├── final-state.json
└── evaluation/
    └── pytest_output.txt
```

日志原则：

- append-only JSONL；每条有 UTC timestamp、run/turn/node id；
- tool input/output 原样保存在受控 artifact，prompt 中只使用 compact view；
- model log 记录 usage、finish reason、schema、context byte size 和 normal/repair；
- 密钥、Authorization、hidden reasoning、完整 message history不进入发布日志；
- controller state delta 与原始 model/tool artifacts 分开，避免一处记录失败导致事实丢失。

当前可复用代码：

- 官方简洁 trajectory：`PhysicianBench/agent/trajectory.py`；
- controller artifact logger/ledger：`clinical-controller/src/clinical_controller/live/support.py`；
- state event sanitization：`clinical-controller/src/clinical_controller/tracing.py`；
- run/evaluator 目录边界：`PhysicianBench/scripts/run_task.py`。

### 8.2 错误分类

至少分开：

```text
provider_transport
structured_output_schema
tool_schema
tool_permission
resource_not_found
transient_transport
duplicate_write
action_failed
verification_failed
budget_exhausted
no_progress
internal_controller
```

Provider outage、ABI/Python 环境问题不能计入 controller/model 失败。历史上的无效 v3 环境 attempt 已单独隔离，见 `ARTIFACTS.md`。

## 9. 已验证的失败经验

### 9.1 v2：bookkeeping false blocking

v2 用 requirement、ClinicalGap、evidence/action dependency 和 Completion Audit 强约束流程。案例表明它可能因为 namespace 或状态未同步阻断合法动作，却没有捕获真正的 temporal/clinical 风险。

结论：结构一致性提高可审计性，但 controller 的内部账本不能代理临床正确性。

证据：`ver3-大幅简化结构.md`、`docs/final-evaluation-v2.md`、`docs/final-evaluation-v3-analysis.md`。

### 9.2 v3：方向正确，但 context 和 parser 不稳定

v3 删除大量 requirement/gap hard gate，保留 Agent ↔ tools、temporal context、Guard → Execute → Verify。部分病例正确识别 discontinued medication、时间冲突和模糊订单；但 10/20 任务因 prose/DSML 包裹 JSON 而 `blocked_schema`，working context 仍可能漏掉 decision-relevant labs。

结论：context builder 有价值，必须配 bounded context、pointer/dereference 和通用 parser recovery；不能把“格式错误”变成临床流程的永久终止点。

证据：`docs/final-evaluation-v3-analysis.md`。

### 9.3 v3.1：parser 和 48 KB cap 有效，hard tracker 失败

v3.1 在 20 个有效任务中：15 个 `blocked_schema`、5 个 `blocked_no_progress`、0 次 FHIR write、0 次 `write_file`。Task Tracker 的首轮抽取、禁止后续重复、pending finalization gate 让所有任务在副作用前结束。

结论：任务进度应防遗忘，但不应成为脆弱的 permission protocol。低调用/token 只是提前终止，不是效率改进。

证据：`docs/final-evaluation-v31-analysis.md`、`experiments/final-evaluation-v3.1-2026-09-22/diagnostics.json`。

### 9.4 总体实验事实

| 版本 | Passed / available checkpoints | Full-task Pass@1 | 关键解释 |
| --- | ---: | ---: | --- |
| MiniAgent | 20/32（62.50%） | 0% | 分母只含与 v3.1 jointly evaluable checkpoints |
| v2 | 18/57（31.58%） | 0% | false blocking 和内部账本问题 |
| v3 | 13/83（15.66%） | 0% | temporal context 有局部改善，parser 大量提前停止 |
| v3.1 | 2/135（1.48%） | 0% | tracker 协议让全部任务在 write 前停止 |

不同版本 pooled denominator 不同，不能把表中 rate 直接当公平总榜。更强的 matched 结果是 v3.1 对 MiniAgent 的 32 个共同 checkpoint：2 vs 20，0 改善、18 回退。

## 10. 新项目建议的最小目录

```text
clinical-agent-explorer/
├── CONTEXT.md
├── docs/
│   ├── physicianbench_reference.md
│   ├── data_contracts.md
│   └── decisions.md
├── mock_db/
├── tasks/
├── tools/
│   ├── schemas/
│   ├── registry.py
│   └── adapters.py
├── evidence/
│   ├── raw_store.py
│   ├── normalize.py
│   ├── index.py
│   ├── belief_state.py
│   └── working_set.py
├── agent/
│   ├── loop.py
│   ├── schemas.py
│   ├── guard.py
│   └── verify.py
├── runs/
└── tests/
```

第一阶段只做 mock data 和 3–5 个合成任务，证明数据链路与事务边界，不先接完整 PhysicianBench：

1. 超长 tool result 完整落 RawEvidenceStore，WorkingSet 小于上限，仍可按 pointer 重新展开。
2. 同一药物的新 discontinued 记录不会被旧 active 记录覆盖；所有来源仍可追溯。
3. 同一时间的矛盾 observation 显式进入 conflict，而不是静默任选一个。
4. patient mismatch、workspace escape、unknown schema field、exact duplicate write 被机械拦截。
5. write 成功响应但 read-back 不匹配时不得进入 VerifiedAction。
6. ambiguous timeout 先搜索现有资源，再决定是否 bounded retry。
7. progress monitor/tracker 输出无效时只降级提示，主 tool loop 仍能继续。
8. 每次 run 可从 artifact 重放出 model/tool/verification 顺序。

这些成立后，再接固定 PhysicianBench commit 和新 job directory 做一个 smoke task；最后才进行预注册的小规模比较。不要先加 RAG、任务专用医学规则或新的多节点 workflow。

## 11. 复用决策表

| 资产 | 建议 | 原因 |
| --- | --- | --- |
| `PhysicianBench/agent/mini_agent.py` | 参考/小幅改写 | loop 简单可靠；message-history 和截断不应原样继承 |
| `PhysicianBench/agent/tool_registry.py` | 固定 commit 后导入/snapshot | 14 个公开工具的模型可见事实源 |
| `PhysicianBench/tools/*` | 只通过 adapter 调用 | 保持 benchmark 可比，不在新项目内魔改 |
| `PhysicianBench/scripts/run_task.py` | 参考 lifecycle | fresh container、workspace、eval、metadata 边界清楚 |
| `clinical-controller/context.py` | 提取 deterministic utilities 后重写 | 时间/冲突/cap 有价值；需正式 raw pointer 和更好的 selection |
| `clinical-controller/live/environment.py` | 提取 guard/verify 测试和规则 | patient/path/duplicate/read-back 是最强资产 |
| `clinical-controller/live/support.py` | 复用 parser/ledger 思路 | wrapper extraction 和一次 repair 已有运行证据 |
| v1/v2 graph 与 gap/requirement 状态 | 不复制 | 多次产生 false blocking |
| v3.1 hard Task Tracker | 不复制 | 20/20 在 write 前停止 |
| 实验 runner/freeze/summarizer | 选取可移植部分 | hash、protocol、环境隔离和 matched analysis 值得保留 |

## 12. 原始证据索引

### 必读文档

1. `HANDOFF.md`：当前版本、环境、实验结果、恢复与秘密审计。
2. `preparation/PhysicianBench-interface-and-runbook.md`：14 个工具、schema/实现差异、官方运行链路。
3. `尝试从miniagent一层层加.md`：下一阶段 RawEvidence/BeliefState/WorkingSet 设计方向。
4. `ver3-大幅简化结构.md`：为什么从复杂 workflow 收缩到 lightweight safety envelope。
5. `ver3和miniagent区别.md`：与官方 MiniAgent 的真实差异。
6. `docs/clinical-controller-v3-refactor.md`：v3 保留与删除的 hard controls。
7. `docs/clinical-controller-v31.md`：v3.1 的实现契约与明确不保证内容。
8. `docs/final-evaluation-v3-analysis.md`：三个病例和 temporal context/parser 经验。
9. `docs/final-evaluation-v31-analysis.md`：tracker 协议失败的完整证据。
10. `ARTIFACTS.md`：聚合结果 hash 和 raw artifact 位置。

### 关键源码

```text
PhysicianBench/agent/mini_agent.py
PhysicianBench/agent/tool_registry.py
PhysicianBench/agent/llm_client.py
PhysicianBench/agent/trajectory.py
PhysicianBench/tools/fhir_api_functions.py
PhysicianBench/tools/file_tools.py
PhysicianBench/scripts/run_task.py
PhysicianBench/scripts/job_manager.py

clinical-controller/src/clinical_controller/schemas.py
clinical-controller/src/clinical_controller/state.py
clinical-controller/src/clinical_controller/context.py
clinical-controller/src/clinical_controller/graph.py
clinical-controller/src/clinical_controller/nodes/action_guard.py
clinical-controller/src/clinical_controller/nodes/execute.py
clinical-controller/src/clinical_controller/nodes/verify.py
clinical-controller/src/clinical_controller/live/environment.py
clinical-controller/src/clinical_controller/live/runtime.py
clinical-controller/src/clinical_controller/live/support.py
clinical-controller/src/clinical_controller/tracing.py
```

### 代表性运行证据

```text
experiments/final-evaluation-v2-2026-09-15/results.json
experiments/final-evaluation-v3-2026-09-22/results.json
experiments/final-evaluation-v3-2026-09-22/three-case-traces.json
experiments/final-evaluation-v3-2026-09-22/schema-failures.json
experiments/final-evaluation-v3.1-2026-09-22/results.json
experiments/final-evaluation-v3.1-2026-09-22/diagnostics.json
experiments/final-evaluation-v3.1-2026-09-22/jobs/<task>/v3.1/<attempt>/logs/
```

`experiments/`、`trajectory-readable.md` 和部分顶层中文研究笔记包含 patient-like benchmark records、prompt 或模型输出，已被 Git ignore。它们只能作为本机/加密存储中的证据，不要复制进公开新仓库。新项目的 `CONTEXT.md` 只应记录结论和这些相对路径，不要粘贴原始病例内容。

## 13. 给新项目 Codex 的简短指令

可把下面这段放进新项目 `CONTEXT.md`：

> Reference repository: `D:\ArrowBallad\cat-on-the-bench`. Treat it as read-only. Do not implement the new demo inside it and do not modify its experiments, PhysicianBench checkout, tasks, tools, controller, or frozen reports. Start with `REFERENCE_NOTES.md`, then inspect only the cited source/artifact paths needed for the current decision. Reuse the official tool contracts, lossless-evidence/selective-context principle, patient/path/schema guards, write read-back verification, and run-level tracing. Do not copy the v1/v2 requirement-gap workflow or the v3.1 hard Task Tracker protocol. Distinguish implemented behavior, frozen experimental evidence, and unimplemented design proposals in all documentation.
