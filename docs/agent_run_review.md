# Agent 运行与 Trace 人工审查

运行日期：2026-10-08（Asia/Hong_Kong）

Provider：DeepSeek Chat Completions 兼容接口  
模型：`deepseek-flash`  
Thinking：关闭，不保存隐藏思维过程  
API key：仅从环境变量 `DEEPSEEK_API_KEY` 读取，未写入任何 artifact

本文不使用 gold answer 或临床评分 rubric。审查对象仅为实际 prompt、模型可观察响应、工具输入输出、状态变化、memory 和 final 是否一致。

## 原始 Trace 位置

### Development Run 1：成功

Run ID：`run-04d7e9c1b853`

```text
runs/run-04d7e9c1b853/
  metadata.json
  prompt_snapshots.jsonl
  model_calls.jsonl
  tool_calls.jsonl
  state_events.jsonl
  run_state.json
  final.md
```

统计：4 次模型调用、4 次工具调用、10,640 prompt tokens、1,829 completion tokens、12,469 total tokens。

原始执行序列：

1. Step 1：模型同时请求 `get_patient(GI-001)` 和 `search_records(GI-001)`；runtime 按返回顺序执行，取得患者记录和 8 条临床记录。
2. Step 2：模型请求 `create_order`，创建“模拟随访：消化科复查评估”；receipt 返回后状态为 `unverified`。
3. Step 3：模型使用 receipt 中的 order ID 调用 `get_order`；患者、订单类型、理由和 evidence refs 全部匹配，状态转为 `verified`。
4. Step 4：模型返回结构化 final；runtime 写入 3 条 patient memory，并保存中文 `final.md`。

### Development Run 2：被 provenance validator 拒绝

Run ID：`run-6db6185e3b7f`

统计：4 次模型调用、4 次工具调用、11,921 prompt tokens、1,698 completion tokens、13,619 total tokens。加载了 Run 1 创建的 3 条 memory。

原始执行序列与观察：

1. Agent 加载 memory 后仍重新调用 `get_patient` 和 `search_records`，没有直接把 memory 当作当前事实。
2. Agent 再次创建并验证了一个内容相近的随访订单，说明它没有充分利用 memory 中“已有已验证订单”的信息；runtime 当前也没有 exact-duplicate write guard。
3. Final 将已验证 order ID 放进 `evidence_refs`。旧 validator 只接受临床 record ID，因此以 `invalid_final_evidence` 干净停止。
4. 在首次 challenge 前，通用 provenance 契约被修正为允许“本次工具返回的患者/临床记录 ID + 本次独立读回验证的 action ID”。这不是病例专属规则。

### Challenge Run：成功

Run ID：`run-9e0fc16344dd`

```text
runs/run-9e0fc16344dd/
  metadata.json
  prompt_snapshots.jsonl
  model_calls.jsonl
  tool_calls.jsonl
  state_events.jsonl
  run_state.json
  final.md
```

统计：4 次模型调用、4 次工具调用、11,168 prompt tokens、2,024 completion tokens、13,192 total tokens。初始 memory 为空。

原始执行序列：

1. Step 1：`get_patient(GI-CH-001)` 和 `search_records(GI-CH-001)`，取得患者记录与 9 条临床记录。
2. Step 2：`create_order` 创建“消化内科随访评估（含上消化道内镜评估）”；receipt 返回后仍为 `unverified`。
3. Step 3：`get_order` 独立读回；批准字段全部匹配，action 转为 `verified`。
4. Step 4：结构化 final 完成；写入 4 条 patient memory。

## Infrastructure Invariants 审查

| 不变量 | 结果 | Trace 证据 |
| --- | --- | --- |
| 患者数据只通过 tools 进入 Agent context | 通过 | Step 1 prompt 不含病例记录；记录首次出现在 tool result 中 |
| DB、state、prompt、memory、trace 分离 | 通过 | 分别位于 case/work/run artifacts |
| clinical time 与运行 timestamp 分离 | 通过 | clinical records 保留 2021–2026 的 `clinical_time`；事件使用 2026-10-08 runtime timestamp |
| 新旧记录不被静默覆盖 | 通过 | Challenge 同时返回 ch-rec-004、007、008；最终保留并解释冲突 |
| raw tool result 可检查 | 通过 | `tool_calls.jsonl` 保存完整结构化输入输出 |
| memory 不替代数据库重查 | Development Run 2 通过 | 加载 3 条 memory 后仍重新执行 patient/record reads |
| receipt 不等于 verified write | 通过 | create 后为 `unverified`，get_order 匹配后才进入 `verified_actions` |
| trace 可还原执行过程 | 通过 | prompt、model response、tool call/result、state delta、final 均有顺序化事件 |

## Agent 行为人工分析

### 做得较好的部分

- Development final 明确区分旧的“未规律使用抗炎药”、后来的萘普生在用记录和最新停用记录，没有让旧事实覆盖新事实。
- Challenge final 明确保留了同一临床时间的阿哌沙班“暂停”和“在用”两条记录，并将其标为未解决冲突，没有自行选择一条作为真值。
- Challenge 将 2026-04-11 的 Hb 13.8 与 2026-10-01 的 Hb 11.7 作为时间趋势描述，同时结合黑便记录，但仍把出血来源表述为未明确。
- 两个成功运行都严格执行了 receipt → `get_order` → matching verification，没有把写响应直接称为成功。
- Challenge 没有机械复制 development case 的幽门螺杆菌结论或订单内容；它选择了与黑便、血红蛋白下降和抗凝状态冲突相关的随访行动。
- Final、run state、verified action 和 memory 对成功运行中的订单状态保持一致。

### 需要关注的行为

- DeepSeek 在两个成功运行的首个工具调用伴随文本中使用了英文，尽管 system prompt 要求所有自然语言使用中文；正式 final 和病例相关说明为中文。这是模型指令遵循的不完全一致，不是数据层翻译遗漏。
- Development Run 2 在 memory 已记录一个已验证随访订单的情况下又创建了近似订单。当前 Phase 1 没有 duplicate-write guard；该现象现在有真实 trace 支持，可作为后续是否增加机械 exact-duplicate 检查的依据。
- Development 与 Challenge 都采用“读取全部记录 → 创建一个订单 → 读回 → final”的相同高层模式。两个任务都允许可选行动，且 system prompt 强调验证，因此不能仅凭两个案例断言过拟合；但也不能证明 Agent 会在不需要行动的案例中选择不写。
- Challenge final 列出了一些数据库中没有的检查项目作为“缺失信息”。这些表述在“本次返回记录中没有”的意义上成立，但仍应避免把小型合成数据库的缺失等同于真实世界中从未发生。
- Development Run 1 与 Run 2 的 Agent 都一次读取全部记录，没有展示按需分阶段检索。对当前小数据集可接受，但无法证明更大记录集下的 context 选择能力。

## 结论

本次演示支持以下结论：Phase 1 的数据隔离、provenance、时间保留、raw trace、memory 边界和 write verification 均按设计工作；Challenge 中的冲突记录得到显式保留。它不支持“临床结论正确”“订单最优”或“Agent 已泛化”的强结论。

真实运行暴露出的下一步候选只有两个：exact-duplicate write guard，以及更明确地区分“当前数据库未见”与“真实临床上不存在”。二者均应先作为新决策讨论，不在本次 challenge 之后直接修改 runtime。
