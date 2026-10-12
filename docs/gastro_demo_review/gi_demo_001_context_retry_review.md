# GI Demo 001：Working Context ID 作用域、结构化修正与 E03 单次重跑

## 结论

本轮修复保持 `update_working_context` 的批次原子性，没有重构 Memory，也没有增加额外 LLM。Controller 现在能区分当前 run 的 Working Context `entry_id` 与跨 run 的 Patient Memory `memory_id`，对坏操作返回可定位、可修正的结构化错误，并在有界 retry context 中保留被拒绝的原提案。

确定性测试完整覆盖了用户指定的修正路径。唯一一次真实 E03 重跑也在 3 次模型决策、6 次工具调用内完成，无重复读取，Prompt 峰值为 31,936 / 32,000 bytes，并报告了 Hb、铁蛋白、tTG-IgA、维生素 D 等具体结果。

有一个必须明确保留的验收差异：真实模型这次直接从已读记录生成 Final，没有调用 `update_working_context`。因此真实运行没有保存 rec-017/018 findings 到 Working Context；“失败提案可修正且无需重读”的能力由确定性测试验证，而不是由这次随机 LLM 行为验证。该 run 写入了 3 条跨次 Memory。

## 失败机制判断

v2 E03 的当前 Working Context 没有任何 active question，但模型提交了 `resolve_question("q-followup-adherence-efficacy")`。这个 ID 既不是 RunState 中存在的 Working Context `entry_id`，也不是加载的 Patient Memory `memory_id`。结合该字符串的语义，它是在尝试把 E02 留下的跨次随访问题当成本轮 Working Context question 处理。

Controller 拒绝整个混合批次是正确的；问题在于原错误没有充分暴露 ID 作用域、坏操作位置和可用 ID，且下一轮没有保留被拒绝的四个合法 findings，导致模型倾向重新读取。

## 工程修复

### ID 作用域

- `resolve_question` 只接受调用前已存在、当前 active 的 Working Context `question_states[*].entry_id`。
- 加载到当前 run 的 Patient Memory ID 由 Controller 记录；若被传给 `resolve_question`，返回 `id_scope_mismatch`。
- 跨 run 问题只能在 Final 的 `memory_updates[*].related_memory_id` 中追加 `resolved` 或 `superseded` transition。
- Tool schema、system prompt 和每轮 payload 的 `id_scopes` 同时公开真实可用 ID；ID 列表来自 RunState，不由模型生成。

### 结构化错误与原子性

无效更新仍然整批不应用。错误现在包含：

- `code`
- `operation_index`
- `target_id`
- `valid_question_ids`
- `applied: false`
- `validated_prior_operation_count`
- `scope_guidance`

新增明确错误码：`unknown_working_question_id`、`inactive_working_question_id`、`id_scope_mismatch`。未读取全文的证据引用仍会被拒绝，没有放宽 provenance 边界。

### 有界 retry context

可恢复的 `update_working_context` 错误会保存原始 operations 与结构化错误。下一轮 Prompt 可直接修改坏操作，不必再次读取临床记录。该区域最多允许 2 次修正，TTL 为 2 个模型 step；成功、放弃、过期或 Final 后清除，并记录 save/clear artifact 事件。retry context 属于 Prompt Budget Manager 的保护区。

Prompt 同时要求先提交事实 additions，再单独提交 question transitions，以降低单个状态迁移错误使合法 findings 一并回滚的概率；这不是强制固定工具顺序。

## 确定性测试

| 场景 | 结果 |
| --- | --- |
| 4 个合法 `add_finding` + 1 个非法 `resolve_question` | 整批拒绝；状态不变；返回坏操作 index、target、真实 valid IDs 和 `applied=false` |
| 删除非法 resolve 后重提 | 4 个 findings 写入；没有再次 `get_record`；retry context 随成功清除 |
| 用 Patient Memory ID 调用 `resolve_question` | 返回 `id_scope_mismatch`，明确引导到 Final `memory_updates.related_memory_id` |
| 用 `memory_updates` 解决旧问题 | 既有生命周期测试确认 append-only transition 后 effective view 为 resolved/superseded |
| retry 提案引用未读全文 | 仍被拒绝，retry 不能绕过证据规则 |
| E03 规模 retry Prompt | 保持不超过 32,000 bytes |

回归结果：`tests` 16/16，`tests_phase1` 53/53，总计 69/69。

## E03 唯一一次受控重跑

运行：`p1-run-8fb63b04aeaf`，runtime `phase1-v4.3-context-retry`，请求模型 `deepseek-chat`，provider 响应模型 `deepseek-flash`。

| 指标 | 结果 |
| --- | --- |
| 状态 | `complete` / `final_response` |
| 模型决策 | 3 |
| 工具调用 | 6 |
| 读取全文 | rec-017、rec-018、rec-014、rec-015，各 1 次 |
| 重复读取 | 0 |
| Working Context 调用 / 保存 findings | 0 / 0 |
| Memory updates | 3 条 |
| Prompt bytes | 19,721 → 31,706 → 31,936 |
| Final evidence refs | 仅 rec-017、018、014、015 |

Step 2 为满足容量限制裁剪了 5 条 retrieval catalog 尾部记录；Step 3 裁剪了 step 1 的两条旧 activity。Memory、task、Working Context、当前 recent results 均未被删除。

Final 报告了具体变化：Hb 12.7 g/dL、MCV 86 fL、铁蛋白 24 ng/mL、转铁蛋白饱和度 18%、叶酸 8.5 ng/mL、维生素 D 29 ng/mL、tTG-IgA 19.8 U/mL，并识别共用烤面包机和含小麦酱油暴露。新增 Memory 保留了本轮已读事实、未解决问题与后续事项。

## 验收判断与边界

- 结构化错误、原子回滚、无需重读的修正、Memory ID 作用域和 provenance 保持：通过确定性测试。
- E03 完成、工具预算、Prompt 预算、无重复读取、具体数值输出：真实 run 通过。
- rec-017/018 findings 保存到 Working Context：真实 run 未发生，因为模型没有选择调用该工具；不能标为通过。
- 跨次 Memory：真实 run 写入 3 条新记录，但没有通过 `related_memory_id` 关闭 E02 的旧随访项，因此这次不是一次完整的 resolved transition 演示。
- Final 中把当前数值与来自 Patient Memory 的历史数值作了自然语言比较，而本轮没有重读 rec-003/007。结构化 evidence refs 合法，但 runtime 目前不能语义级识别这种历史来源复述；按本轮范围不修改 Final 证据协议，将其作为 Viewer 应展示的 provenance 限制。

建议停止继续针对这个随机输出调 Prompt，进入 Viewer 阶段。Viewer 应把“本轮 Working Context 未使用”、本次全文可见记录、Memory 来源、Prompt trims 和上述历史比较限制直接展示出来。这样既保留可解释性，也不把确定性机制测试与一次模型行为混为一谈。

## Artifacts

- `runs/gastro_demo_e03_context_retry/experiment_manifest.json`
- `runs/gastro_demo_e03_context_retry/E03/p1-run-8fb63b04aeaf/`
- `work/gastro_demo/gi-demo-001-e03-context-retry-v1/checkpoint_provenance.json`
- `work/gastro_demo/gi-demo-001-e03-context-retry-v1/memory/patient_memory.jsonl`
