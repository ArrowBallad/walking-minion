# V4 执行协议修复与 Quick Comparison v2

## 结论先行

补丁解决了原实验中的两个交付故障：V4 从 1/3 个案例产生 final 提升到 3/3，模型调用从 56 次降到 15 次，总 tokens 从 352,152 降到 90,840。短 workflow 病例不再因批量工具调用失败；头晕病例的合法主 final 也不再被 3 条非法 `memory_updates` 连带拒绝。

但“能够交付 final”没有等同于“答案完全正确”：

- workflow final 正确区分 ordered、performed 和 resulted；
- cough final 诚实说明未读检查报告，却因此遗漏任务明确要求的病毒和胸片实际结果；
- dizziness final 找到了 RAG 漏掉的体位生命征、电解质和复查记录，但没有呈现部分关键原始数值，把初始低钠/低钾错误概括为已排除，并在正文中使用了多条未读 preview 的工作流与冲突信息。

修复后 V4 的执行可靠性和成本显著改善，但本轮仍没有显示它在任务质量或成本上整体优于 Full-context LLM。相对于简单 RAG，它有更强的跨记录发现能力，也有更明显的语义证据边界风险。

## Runtime 修改

修改范围限于 `src/clinical_agent_explorer_phase1/runtime.py`、`model_client.py`、CLI metadata、相关测试和 quick comparison 版本标识。病例、BM25、retrieval catalog、working context、stopping guidance 和临床任务内容未修改。Runtime 标识为 `phase1-v4-execution-protocol-patch`。

### 批量工具协议

- 每轮允许 1–4 个 tool calls，按模型返回顺序同步执行。
- `parallel_tool_calls` API 配置改为 `true`，表示允许模型提出多个调用；runtime 本身不并发。
- 执行整批前检查总工具预算；若整批会超过剩余预算，本批零执行并停止，不会部分越界。
- 单批超过 4 个调用时整批零执行，返回 `tool_batch_too_large`；沿用连续可恢复错误上限 2，第三次连续超大批次停止。
- 每个实际调用仍通过原 `_handle_tool_call()` 和 `_apply_tool_result()` 独立校验、执行、更新状态并保存原始输入/输出。
- 批次中某调用失败时，之前成功调用的状态和真实副作用保留；后续调用不执行，并分别记录 `tool_call_not_executed` 和 `tool_batch_interrupted`。不会自动重放成功调用。
- 多个 `create_order` 各自保存 receipt，仍必须在后续轮次分别 `get_order` 回读才能进入 `verified_actions`；同一批不能引用前一步调用时尚未知的 order ID。

Patient binding、preview/full-record 证据边界、working-context provenance、订单字段匹配、verified action、工具总预算和错误时 state isolation 均未放宽。

### 批量结果进入 prompt

最近完整批次受到优先保护：即使 `recent_result_limit=2`，最新一批最多 4 个结果也会一起进入下一轮 prompt。更早结果仍按原有限窗口淘汰，完整原始输出始终保存在 `tool_calls.jsonl`。

每条 recent result 带有 `batch_id`、批内位置和批大小。若单条结果先被 8,000 字符 prompt view 截断，或整批因 `max_prompt_bytes` 被裁剪，payload 的 `full_record_bodies_omitted_from_prompt` 和 prompt snapshot metadata 会列出 call ID、record ID、原因及 `full_record_body_included=false`，并要求重新读取；不会仅凭 `inspected` 状态声称正文仍可见。

本次三个真实 V4 run 的最大 prompt 分别为 25,587、27,443、22,666 bytes，均低于 32,000；所有已执行批次的完整结果都进入了紧接的下一轮，没有实际发生 prompt body omission。Workflow 中 step 2 和 step 3 的两个四记录批次分别完整进入下一轮。较早批次随后离开 recent window，模型因此在 step 4 重读了 `wf-rec-001` 至 `wf-rec-004`；这是可见的重复读取，不是同批静默丢失。

### Final 与 memory 解耦

主 final 仍严格要求合法的 `final_text`、`run_summary` 和 `evidence_refs`；非法 final evidence 继续触发原有 final revision/拒绝流程，runtime 不会自动删除引用。

`memory_updates` 现在可省略。每条 update 仍逐项检查 kind、status、非空文本、证据 provenance 和 `related_memory_id`；非法条目不写入 memory，产生 `memory_update_rejected` 事件，但不否定合法主 final。合法条目照常写入。

## 确定性测试

Phase 1 当前 38/38 通过，Phase 0 回归 16/16 通过。新增或调整的覆盖包括：

- 一轮四个 `get_record` 顺序成功，下一轮在 `recent_result_limit=2` 下仍看到整批四个结果；
- 五调用批次零执行并收到有界反馈；连续第三次超大批次停止；
- 工具总预算在整批前检查，不能部分执行；
- 批中可恢复错误保留之前成功状态，后续调用明确标为未执行；
- 批中订单 A 已写入、订单 B 失败时只保留 A，订单 C 不执行且 A 仍需独立回读；
- 同批创建两个订单后仍分别独立回读验证；
- 批量正文超过 prompt 容量时明确列出省略的 call/record，prompt 不超预算；
- 合法 final 加合法/非法混合 memory updates 时完成主任务，只写合法 memory；`memory_updates` 可省略；
- 非法主 final evidence 仍按原机制拒绝；原有单调用、recoverable state isolation、patient scope、catalog 和订单测试继续通过。

## 实验设置

v2 仍使用原三个病例、原任务文本、固定中文 unigram+bigram BM25 Top-8、同一模型配置和同一预算（`max_steps=25`、`max_tool_calls=40`）。三个方法各运行一次，共 9 个真实 API run。请求模型为 `deepseek-chat`、thinking disabled；全部响应仍自报 `deepseek-flash`。

v1 artifacts 保留在 `runs/quick_comparison/quick-comparison-v1/`；新结果位于 `runs/quick_comparison/quick-comparison-v2/`。每个 V4 案例使用新的 mock DB 和空 memory，三个 `orders.jsonl` 均为空。没有在看到结果后补跑或调整 prompt。

## 原 V4 与修复后 V4

下表中 Tool calls 的“尝试”是模型响应提出的调用数；“成功”是实际执行且 `ok=true`；“拒绝”包含整批零执行、旧协议批量拒绝及已执行的验证错误。Full records 为唯一全文记录数。

| 指标 | 原 V4（v1） | 修复后（v2） |
| --- | ---: | ---: |
| 完成三个病例 | 1/3 | 3/3 |
| 模型调用次数 | 56 | 15 |
| Tool calls：尝试 / 成功 / 拒绝 | 84 / 43 / 41 | 39 / 34 / 5 |
| Prompt / Completion / Total tokens | 327,397 / 24,755 / 352,152 | 80,742 / 10,098 / 90,840 |
| Full records inspected（唯一） | 23 | 21 |
| 主 final rejection | 2：`invalid_final`、`invalid_final_evidence` | 0 |
| 被跳过的非法 memory updates | 不适用；会拒绝整个 final | 4：cough 1、dizziness 3 |
| 最终答案质量 | cough 完成；另两例无 final | workflow 正确；cough 明确遗漏检查结果；dizziness 更完整但有关键错误和 preview-only 使用 |

总体上，模型调用减少 73.2%，prompt tokens 减少 75.3%，total tokens 减少 74.2%。这与批量读取和 memory 解耦的预期方向一致，但单次模型采样也可能影响停止时机，不能把全部差值都归因于代码。

| 案例 | 原 V4 → v2 状态 | 模型调用 | Tool 尝试/成功/拒绝（原 → 新） | Total tokens | 唯一全文 |
| --- | --- | ---: | --- | ---: | ---: |
| cough | complete → complete | 20 → 3 | 20/16/4 → 7/7/0 | 125,130 → 17,987 | 8 → 4 |
| dizziness | iteration limit → complete | 25 → 6 | 29/20/9 → 17/12/5 | 175,222 → 40,688 | 10 → 8 |
| workflow | invalid model response → complete | 11 → 6 | 35/7/28 → 15/15/0 | 51,800 → 32,165 | 5 → 9 |

## v2 三方案实际比较

| 方案 | Final | 模型调用 | 实际工具调用 | 全文提供/读取 | Prompt / Completion / Total tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full-context | 3/3 | 3 | 0 | 84 | 12,961 / 2,786 / 15,747 |
| BM25 Top-8 + LLM | 3/3 | 3 | 0 | 24 | 4,497 / 1,657 / 6,154 |
| 修复后 V4 | 3/3 | 15 | 34 | 21 unique（25 次 `get_record`） | 80,742 / 10,098 / 90,840 |

修复后 V4 的总 tokens 仍约为 Full-context 的 5.8 倍、RAG 的 14.8 倍。Full-context 与 RAG 的 prompt tokens 和检索集合与 v1 相同；completion 长度变化来自新的单次模型采样。

## 病例级质量核对

### `workflow_status_001`

**问题 1：短病例是否不再因批量调用提前失败？是。**

V4 首轮搜索发现全部 9 条记录，之后成功执行多个四调用批次，在 step 6 生成 final。回答正确区分：胸片已申请、已执行且有最终阴性报告；尿培养虽申请并采集，但标本拒收、取消且无可解释结果；超声心动图仅申请待排期、未执行、无结果。它没有发生 batch rejection、partial execution 或 memory rejection。

代价仍明显：V4 使用 32,165 tokens、6 次模型调用和 15 次工具调用；Full-context 为 2,020 tokens，RAG 为 1,946。V4 还重复读取了前四条记录，并读取了与任务无关的 CBC。补丁解决了失败，但没有让短任务更高效。

### `dizziness_longitudinal_001`

**问题 2：合法 final 是否能在 memory 非法时交付？是。**

V4 在 step 6 提交了合法主 evidence refs，同时三条 memory update 分别引用未读的 `dz-rec-022/023/027`、`dz-rec-019/020` 和 `dz-rec-019/020/023`。Runtime 产生 3 个 `memory_update_rejected` 事件、不写这些 memory，并接受主 final；v1 的类似输出曾以 `invalid_final_evidence` 拒绝整个回答。

执行层面，step 3 曾返回 5 个调用，被 `tool_batch_too_large` 整批拒绝且零执行；模型收到反馈后改为单个 context update，再在下一轮提交四个读取，最终完成。没有 partial state、跳过调用或 prompt 正文丢失。

答案质量仍有实质问题：

- V4 读取了 `dz-rec-015`，却没有在 final 中给出卧位 142/78 到站立 108/64 的具体变化；只说该记录“用于量化”。
- 它读取了 `dz-rec-017`，却没有报告初始钠 128、钾 3.2，随后又称“电解质紊乱……已被客观检查基本排除”。这与原始初始异常相矛盾；正确时间线应为初始低钠/低钾、停药后复查恢复。
- `dz-rec-025` 的葡萄糖原单位为 mg/dL，final 写成“93 mmol/L 级单位”，单位错误。
- 它没有读取 `dz-rec-019/020/021/022/023/027/028/038` 全文，却在时间线、冲突、检查工作流和建议中使用了这些记录的标题/preview 信息；final evidence refs 没有列这些 ID，因此机械 provenance 校验通过，但语义证据边界仍被违反。Final 后段又把它们列为未读候选，形成内部矛盾。

与之相比，Full-context 正确呈现了卧立位数值、电解质异常与恢复、用药冲突和完整工作流；RAG 仍因固定 Top-8 漏掉这些关键记录，并错误概括“未发现明确相互矛盾的记录”。V4 的检索覆盖优于 RAG，但证据使用质量不如 Full-context，也没有稳定地把已读关键证据写进答案。

### `cough_stopping_001`

V4 仅用 3 次模型调用、7 次工具调用完成，比 v1 的 20 次调用和 125,130 tokens 大幅下降。它读取起始依那普利、症状门诊、停药和电话随访，正确写出“停药约两周后基本消失，仅清晨偶尔轻咳”。一条引用未读检查和换药记录的 memory update 被跳过，主 final 正常完成。

但任务明确要求检查结果，而 V4 已发现却没有读取病毒检测 `cs-rec-026` 和胸片报告 `cs-rec-028`；final 只能说实际结论未知。这是诚实的不确定性，却仍是任务完成遗漏。它还加入“ARB 干咳发生率低”等记录外知识，并把药物因果关系表述得比 Full-context 更确定。Full-context 和 RAG 都读取/获得了病毒阴性与胸片结果，因此该病例中 V4 的快速停止降低了答案完整度。

## 协议问题逐项回答

1. **短 workflow 病例是否不再因批量 tool call 提前失败？** 是；V4 正确完成，但仍远比 baseline 昂贵并有重复读取。
2. **头晕病例是否能在 final 合法、memory update 非法时交付？** 是；3 条非法 memory 被逐条拒绝，主 final 无 revision 即完成。
3. **批量调用是否减少轮数和 tokens？** 是。V4 总模型调用 56→15、total tokens 352,152→90,840；三个案例均下降。Workflow 的实际工具执行从 7 增至 15，因为原先被拒绝的读取现在真正执行并完成任务。
4. **是否出现新的 partial execution、状态不一致或 prompt 信息丢失？** 三个真实 run 中没有 partial execution、state mismatch 或正文裁剪。Dizziness 有一个 5-call 批次按协议整批零执行；所有已执行的最新批次完整出现在下一 prompt。Workflow 对更早批次的重复读取说明有限窗口仍有成本，但同批结果未被静默丢失。确定性测试另外验证了真实 partial failure 时保留前序副作用并跳过后续调用。
5. **任务完成质量是否实际改善？** 交付率明确改善，workflow 质量也从无答案变为正确；dizziness 从无答案变为信息更丰富但有关键事实和 provenance 问题；cough 虽更快却遗漏检查结果。故质量是混合改善，不能仅用 3/3 final 宣称全面胜出。

## 限制与最终判断

这仍是三个开发/回归性质的合成病例，每格一次运行，不是 unseen benchmark。模型具有随机性，v1/v2 的检索路径和停止点并非配对确定执行；补丁与采样效应无法完全分离。所有病例都能轻松放入 Full-context，不能检验超长 context 下 Agent 的潜在价值。

**最终判断：执行协议补丁值得保留，因为它修复了可复现的工程失败，并以显著更低成本把 V4 交付率从 1/3 提升到 3/3；但修复后的 V4 仍没有在这个规模上证明相对 Full-context 的任务质量或成本优势。** 下一瓶颈已经不是批量调用或 memory 附属字段，而是模型对已读证据的准确使用、对未读 preview 的语义越界，以及停止时是否覆盖任务的明确子问题。本任务不据此继续修改 prompt、检索或 Agent 架构。
