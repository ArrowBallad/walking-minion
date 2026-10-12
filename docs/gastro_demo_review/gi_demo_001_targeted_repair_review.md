# GI Demo 001：E01/E03 定向诊断、有限修复与受控重跑

## 结论

本轮修复解决了两个原始终止故障：E01 不再因工具额度超限停止，E03 不再因 32 KB Prompt 上限报错。受控重跑中 E01、E02、E03 均产生合法 Final，分别使用 6、6、11 次工具调用；三个 encounter 的模型输入均未出现未来 record ID。

Memory 的真实生命周期也形成了闭环：E01 保存“病因未明、乳糜泻未排除”的 active unresolved memory；E02 加载该条 memory，用新读取的血清学、内镜、病理和诊断记录将其写为 resolved；E03 同时加载 active 与 inactive/resolved 视图，并据此优先读取 rec-014 至 rec-018。

结果并非全绿。E03 主动尝试保存 Working Context，但一个不存在的 question entry ID 使包含 8 个 operation 的整次调用原子回滚，随后发生 3 次重复读取。之后首个 Final 又因引用了本次未重读的 rec-003、rec-007、rec-013 被拒绝；修订版合法完成，但临床信息密度明显下降。这说明容量问题已修复，增量 Context 接口的失败隔离仍是一个明确但更窄的剩余问题。本轮按约定不再根据结果继续调参或重跑。

## P0：E03 Prompt 容量诊断

### 修复前的增长

| 阶段 | 总 Prompt bytes | 主要增长来源 |
| --- | ---: | --- |
| Step 1 | 17,587 | patient memory 5,114；system 4,715；tools 3,661 |
| Step 2 | 31,590 | retrieval catalog 7,575；record visibility 3,798；memory 5,114；activity 831 |
| Step 3，4 个 recent results | 37,085 | recent results 5,330；catalog 7,571；memory 5,114；visibility 3,778 |
| Step 3，删光 recent results | 32,137 | catalog 7,571；memory 5,114；visibility 3,782；omission/context 1,443 |

旧的降级循环只删除 recent results。每删除一条结果，omission ledger 反而增长；当结果全部删完时仍比 32,000 bytes 多 137 bytes，因此故障是确定性的，不是临床正文偶然过长。

主要重复包括：

- `patient_memory.entries` 与 `active_entries` / `inactive_entries` 重复携带同一条 memory。
- retrieval catalog、retrieval state、record visibility、omission ledger 和 recent activity 重复出现 record ID 与读取状态。
- 完整 visibility、omission 和 activity 是 Viewer/调试信息，却被每轮重新发送给模型。

冻结诊断可由 `scripts/analyze_encounter_prompt_growth.py` 复核。失败的 step 3 没有生成 snapshot，因此脚本保留了修复前、对应旧 runtime SHA-256 的容量测量值。

### 实施的修复

`runtime.py` 新增统一的 `PromptBudgetManager`：

- 每轮记录 system、tool schemas、user payload 和每个顶层 payload section 的 UTF-8 bytes。
- 模型侧 memory 只保留一份 `entries` 和计数，不再重复 active/inactive 列表。
- 模型侧 retrieval catalog 最多 12 条；完整 catalog 仍保存在 prompt snapshot metadata。
- 模型侧只接收紧凑的可见性摘要；完整 per-record visibility 与 omission 原因保存在 artifacts。
- recent activity 限为紧凑窗口，并移除与 catalog 重复的 returned record ID 列表。
- 统一按 activity、catalog、recent results 的顺序有界裁剪；task、patient memory、working context 和 execution budget 是受保护区，不会静默删除。
- 每次裁剪均写入 `prompt_budget.trims`，并保留完整 Viewer/调试账本。

修复后的 E03 六轮 Prompt 分别为 18,032、31,687、30,566、26,454、30,868、26,535 bytes，全部在 32,000 上限内。最高轮通过明确记录的 3 次 catalog 裁剪达到 31,687 bytes；没有提高容量上限。

## P1：E01 为什么不结束

修复前 E01 的证据链很清楚：

- Step 3：模型知道 rec-002 至 rec-005 已读取且正文仍可见；它决定补读唯一未读的 rec-001，并在输出中明确表示下一步应更新 context 后结束。
- Step 4：五条记录均已 inspected，但没有一条事实被持久化到 Working Context；最近窗口只保留 rec-005 与 rec-001 正文。模型明确说要重新核对已淘汰正文，于是重读 rec-002 至 rec-004。
- Step 5：Prompt 没有剩余工具额度。模型再请求 rec-005、rec-001、get_patient 和 search_records 共 4 次，而 Controller 只剩 2 次额度；整批按原子预算规则被拒绝。

因此原始 E01 不是“模型完全不知道自己读过什么”：它知道 inspected 集合，但把 `inspected_without_visible_body` 理解为需要重读，且没有使用 Working Context 保存刚读到的事实。缺少显式剩余额度使最后一次 4-call batch 的失败成为可确定修复的协议问题。

修复后的每轮 Prompt 增加 `execution_budget`，包括当前/最大 step、已执行/剩余工具调用数、每批上限，以及“超过 remaining_tool_calls 将整批不执行并停止”的明确说明。受控 E01 在第 3 次模型调用直接完成：读取 4 条相关全文，无重复读取，共 6 次工具调用，最高 Prompt 21,274 bytes。

## P2：Memory 生命周期

新增确定性测试覆盖以下完整状态迁移：

1. E01 读取证据并写入 active unresolved 与 active prior_work。
2. E02 首轮 Prompt 能加载这两条 active memory；读取新证据后分别写入 related `resolved` 与 `superseded` transition。
3. E03 的 effective view 将旧条目标记为 resolved/superseded，不再把它们列入 active entries，同时仍保留可审计历史。

真实 LLM 重跑也完成了核心闭环：

- E01 写入 4 条 memory，其中 `mem-d4871f3ff6f8` 表示病因未明、乳糜泻尚未排除。
- E02 加载全部 4 条，并写入 `mem-ef5c351257a3`，以新证据关联并 resolve `mem-d4871f3ff6f8`。
- E03 加载 8 条有效视图记录（6 active、2 inactive/resolved），用其优先选择确诊后的营养基线、补充治疗、饮食教育和六个月随访记录。

这已经展示“发现问题 → 保存未解决问题 → 新证据到达 → 重新验证 → resolved”的实际链路。E03 对 E02 的 follow-up next step 只写入新的 active 事项，没有把旧 next step 正式 supersede，因此后半段的状态整理仍不完整。

## 受控重跑

| Encounter | 状态 | 模型调用 | 工具调用 | 全文读取（唯一/总计） | 重复读取 | 最大 Prompt |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| E01 | complete | 3 | 6 | 4 / 4 | 0 | 21,274 |
| E02 | complete | 3 | 6 | 4 / 4 | 0 | 27,449 |
| E03 | complete | 6 | 11 | 5 / 8 | 3 | 31,687 |

三次运行均由环境中的 `DEEPSEEK_API_KEY` 调用，请求模型为 `deepseek-chat`，provider 返回的模型标识为 `deepseek-flash`。每个 encounter 只运行一次，使用同一患者实例和同一 memory store；未进行结果驱动的第二次运行。

E03 的 3 次重复读取不是容量管理器造成的静默丢失。Step 3 的 `update_working_context` 同时提交 4 个 finding 与 4 个 question operation，其中 `resolve_question(q-followup-adherence-efficacy)` 引用了当前 context 中不存在的 ID。Controller 返回 recoverable `invalid_arguments` 并保持 state 不变，整个 Context 仍为空；模型随后重读 rec-017、rec-018、rec-014，并补读 rec-016。

E03 Step 5 的首个 Final 内容较完整，但 evidence_refs 包含本次未读取的 rec-003、rec-007、rec-013，因此被证据边界拒绝。Step 6 修订版移除非法引用后完成。这个过程说明证据验证工作正常，也说明 Final 修订反馈会挤占最近正文窗口、使修订质量下降；本轮没有进一步修改该行为。

## 验收判断

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 三阶段完整结束 | 通过 | 三个 run 均 `complete / final_response` |
| 无未来信息泄漏 | 通过 | E01/E02/E03 Prompt 扫描均无不可用 future record ID；确定性隔离测试通过 |
| 工具调用在预算内 | 通过但 E03 接近上限 | 6 / 6 / 11，预算均为 12；无 batch budget rejection |
| Prompt 容量受控且可审计 | 通过 | 所有 Prompt ≤32,000；component bytes 与 trims 写入 snapshots |
| Working Context 保留主动选择事实 | 机制通过、真实重跑未通过 | 原有增量测试通过；E03 主动调用但因一个坏 operation 原子回滚 |
| Memory 跨阶段使用与更新 | 核心闭环通过 | E01 unresolved 在 E02 被 related resolved；E03 加载并用于检索 |
| 全过程可追溯 | 通过 | prompts、model calls、tools、state events、memory、final 均保存且 JSON/JSONL 校验通过 |

## 范围边界与建议

本轮没有修改临床事实、没有扩展到其他患者、没有增加 Prompt 上限，也没有为特定诊断写死工具顺序。当前 Demo 已足以说明工具、当前 Prompt、Working Context、跨运行 Memory 与 Controller 验证的分工。

若还有下一轮，唯一值得优先处理的是增量 Context 的失败隔离：避免一个无效的 `resolve_question` 让同一调用中已经合法的 `add_finding` 全部丢失。可选方案是限制每次 operation 数量、在 Prompt 中暴露可 resolve 的稳定 question IDs，或为纯新增 finding 与状态迁移拆分提交。该项应单独设计和测试，不应与更多临床数据或新功能绑定。

## Artifacts

- 冻结诊断：`scripts/analyze_encounter_prompt_growth.py`
- 重跑汇总：`scripts/analyze_controlled_encounter_rerun.py`
- 实验清单：`runs/gastro_demo_encounters_v2/experiment_manifest.json`
- E01：`runs/gastro_demo_encounters_v2/E01/p1-run-2ed3c307f21d/`
- E02：`runs/gastro_demo_encounters_v2/E02/p1-run-7b4ee7428483/`
- E03：`runs/gastro_demo_encounters_v2/E03/p1-run-94272b0cdf88/`
