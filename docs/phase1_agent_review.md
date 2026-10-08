# Phase 1 Agent 运行与人工审查

本文件只审查可观察行为，不给临床正确性打分，不使用 gold 或 rubric，也不把某种工具顺序当作标准答案。隐藏思维过程未启用或保存。

## Runtime 与案例边界

- 公开 development case：`cases/phase1_development/gi_longitudinal_001/`，38 条记录。
- Runtime freeze：`docs/phase1_runtime_freeze.md`。
- Freeze 后创建的 unseen challenge：`cases/phase1_challenge/dizziness_longitudinal_001/`，40 条记录。
- Challenge 运行后未修改 `src/clinical_agent_explorer_phase1/`。

API 请求 metadata 记录的模型为 `deepseek-chat`、thinking disabled、`parallel_tool_calls=false`；provider 在逐次响应中自报模型名为 `deepseek-flash`。两者均原样保留，本文不推断 provider 内部路由。

## Development observations

Development 阶段的目的，是暴露与任何病例无关的 plumbing 问题。

| Run | 结果 | 可观察问题 |
|---|---|---|
| `p1-run-8b5ee8c2e99a` | `tool_call_limit` | 一轮批量读取 6–7 条全文，但下一轮只保留两个最近结果，导致机械重读，working context 为空。 |
| `p1-run-0b9a56437f7d` | `invalid_final_evidence` | Agent 在正文中承认记录仅停留在索引层，却在 memory evidence 中引用这些 ID；runtime 正确拒绝。 |
| `p1-run-05a4e429be67` | `tool_call_limit` | Provider 忽略 `parallel_tool_calls=false`；22 次全文读取中出现 9 次重复。 |
| `p1-run-811863434356` | `iteration_limit` | 原子调用契约后，Agent 形成 5 条带全文证据的 working facts、4 个 unresolved questions，且没有机械批量执行；20 轮不足以完成。 |
| `p1-run-4d845f2ec884` | `invalid_arguments` | Agent 只读了一条门诊全文，就试图记录一个需要两侧证据的药物 conflict；工具按证据边界停止。 |

这些观察促成的通用改动在 challenge 创建前已经冻结：原子工具调用、近期无正文调用账本、context 状态提示、final 校验反馈。没有把幽门螺杆菌、贫血、胃镜或任何预期临床动作写入 runtime。

## Challenge 原始 trace

Run：`p1-run-8e583d9e9e0c`

原始 artifacts 位于 `runs/phase1/p1-run-8e583d9e9e0c/`：

- `metadata.json`：模型请求配置、限制与 case ID。
- `prompt_snapshots.jsonl`：20 个完整 prompt snapshot 与当轮 tool schemas。
- `model_calls.jsonl`：20 个原始可观察模型响应、tool calls 与 token usage。
- `tool_calls.jsonl`：17 次实际执行的原始工具输入输出；被拒绝的批量调用没有执行。
- `state_events.jsonl`：95 个有序事件，可还原 prompt、响应、工具结果、状态变化、拒绝和停止。
- `run_state.json`：停止时 working state。
- `final.md`：停止原因；本次没有临床 final。

运行摘要：

- 20 次模型响应。
- 17 次实际工具调用：1 次 patient read、1 次 index search、7 次全文读取、8 次 context 更新尝试。
- 7 次 context 更新成功，最后一次失败。
- 0 次重复全文读取。
- 2 次批量工具响应被拒绝，均未执行。
- 1 次模型把 context 参数 JSON 作为普通文本返回，被 final schema 校验拒绝；下一轮自行恢复为正确工具调用。
- Prompt 始终低于 32,000 bytes；最大约 19,761 bytes。
- Usage 合计约 84,909 prompt tokens、9,137 completion tokens。
- 未创建订单，未写入 patient memory。

## Challenge 可观察时间线

| Step | 模型行为 | Runtime 结果 |
|---|---|---|
| 1 | 同时请求 patient 与关键词搜索 | 原子调用契约拒绝；均未执行。 |
| 2 | `get_patient` | 成功。 |
| 3 | 不带临床答案的宽索引搜索，limit 20 | 返回 40 条匹配中的前 20 条，`has_more=true`。 |
| 4 | 读取当前基层门诊 `dz-rec-014` | 成功。 |
| 5 | 保存 2 条 facts、6 个 unresolved questions | 成功；已开始使用 working context。 |
| 6 | 同时请求卧立位生命体征与头颅 CT 报告 | 拒绝；均未执行。 |
| 7 | 单独读取卧立位生命体征 `dz-rec-015` | 成功。 |
| 8 | 将 context 参数 JSON 作为普通文本返回 | 被当作不完整 final 拒绝。 |
| 9 | 正确调用 `update_working_context` | 成功。 |
| 10–11 | 读取头颅 CT 报告 `dz-rec-039`，随后更新 context | 成功。 |
| 12 | 重新组织 unresolved questions | 成功，没有新增全文证据。 |
| 13–14 | 读取 CBC `dz-rec-016`，随后更新 context | 成功。 |
| 15–16 | 读取基础代谢面板 `dz-rec-017`，随后更新 context | 成功。 |
| 17–18 | 读取当前心电图 `dz-rec-026`，随后更新 context | 成功。 |
| 19 | 读取临床停用氢氯噻嗪条目 `dz-rec-020` | 成功。 |
| 20 | 尝试保存“停药条目 vs 药房在用条目”的 conflict | 失败：只读取了 `dz-rec-020`，未读取 `dz-rec-019`，且 conflict 只有一个 evidence ref。Runtime 以 `invalid_arguments` 干净停止。 |

## 人工行为审查

### 信息需求与检索

Agent 没有一次性得到全部记录。它从宽索引中选择了当前门诊、卧立位生命体征、头颅 CT 结果、CBC、电解质、心电图和停药条目，选择与任务存在连贯关系。每次全文读取都有明确 `information_need`，没有重复读取已经 inspected 的记录。

不足之处是搜索只完成第一页。虽然 `has_more=true`，Agent 没有使用 cursor 获取后 20 条；本案的近期关键记录已经位于第一页，因此这没有立即阻断进展，但它不能声称已排除第二页的历史信息。它也没有优先读取索引顶部已经出现的停药后随访 `dz-rec-024`、复查电解质 `dz-rec-025` 和动态监测状态 `dz-rec-023`，而是先花较多轮次逐条固化较低争议的 CBC、CT 等信息。

### Working context

这是与 Phase 0 最明显的差异。停止时 context 包含 7 条通过全文证据支持的 facts 和 4 个 unresolved questions；每条 fact 均引用已 inspected record。模型能从原始数值形成有限解释，也会在新证据后删改 unresolved questions。

代价是上下文更新非常频繁：7 次全文读取配套 7 次成功快照更新，加上一次无新增证据的重整。完整快照反复发送使 token 使用偏高。它证明了 working context 真正在执行中被使用，也暴露了下一阶段可能需要研究的增量更新或更紧凑表示问题；本阶段不据此改动冻结 runtime。

### 时间、状态与冲突

Agent 将 2026-02-02 门诊列出的用药状态标为 historical，并把后续临床停药条目视为 current，说明它没有把较早观察静默覆盖为当前事实。它也保留了动态心律监测“是否只有工作流状态、是否已有实际结果”这个问题，没有把设备佩戴或进行中状态直接当成最终结果。

最终失败同样有信息价值：Agent 从索引 preview 知道 `dz-rec-019` 是药房“在用”记录，却在没有读取全文时尝试把它与 `dz-rec-020` 写成正式 conflict。它在自然语言 statement 中提到两侧，但 `evidence_refs` 只有一条。Runtime 没有接受这种“从索引升级为事实”的行为。

### 停止与最终输出

本次没有 clinical final，不是因为达到 iteration 或 tool-call limit，而是因为确定性的 context 参数校验失败。错误被写入原始 tool result、state event 和 `final.md`，状态保持可检查；此前已经确认的 7 条 facts 没有被损坏。

这次单次 unseen observation 说明：冻结后的系统已经能迫使 Agent 显式表现“知道什么、还缺什么、下一步查什么”，并能阻止 preview 冒充已验证事实；但当前 Agent 仍会过早把索引层提示组织成 conflict，而且一次参数错误就终止整次运行。后者是可观察到的 runtime/agent 交互限制，不在 challenge 后回改。
