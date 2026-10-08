# Phase 1：受限纵向信息检索

当前 `clinical_agent_explorer` 包保留为 Phase 0，用于证明 tool calling、trace、写入回读和最终证据引用的基础数据链路。Phase 1 使用独立的 `clinical_agent_explorer_phase1` 包，不覆盖 Phase 0。

## 目标

Phase 1 观察：面对 30–50 条分散的纵向记录，Agent 是否能形成有限的信息需求，检索索引、选择少量全文、维护本次运行的 working context，并自行决定何时停止。

Runtime 不编码正确检索顺序、临床结论、订单、未解决问题或 memory 内容。确定性自动测试只覆盖 infrastructure invariants；Agent 行为通过原始 trace 人工分析。

## 最小目录边界

- `src/clinical_agent_explorer_phase1/`：runtime、工具边界、可变 mock DB、memory 和 artifacts。
- `cases/phase1_development/`：公开 development case；可以反复用于发现通用 plumbing 问题。
- `cases/phase1_challenge/`：runtime 冻结后创建的一个 unseen challenge case。
- `tests_phase1/`：只验证确定性工程边界。
- `work/phase1/`：从 case seed 复制出的可变 DB 与 patient memory；不属于 case data。
- `runs/phase1/`：逐次运行的原始 prompt、模型响应、工具输入输出、状态事件和 final。
- `docs/phase1_runtime_freeze.md`：challenge 前的 runtime 哈希与测试状态。
- `docs/phase1_agent_review.md`：development 与 challenge 的人工行为审查；不是 evaluator 或 rubric。

## 数据与 prompt 分层

- Patient DB：全部原始纵向记录。
- Retrieved index：`search_records` 实际发现过的索引级记录。
- Inspected records：`get_record` 实际读取过全文的记录。
- Patient working context：Agent 选择保存的 `known_facts`、`unresolved_questions`、`conflicts` 和相关记录引用。
- Prompt：任务、有限 working context、近期原始工具结果和不含临床正文的近期调用账本。
- Patient memory：跨运行的工作连续性信息；不能代替本轮数据库重查。

## 已硬化的工程边界

- 首轮 prompt 不含病例数据，患者数据只能通过工具访问。
- `search_records` 只返回索引字段并支持过滤、limit 与 cursor；`get_record` 才返回全文。
- Preview 不能支持 working facts、最终证据或写入证据。
- 临床时间与 runtime timestamp 分离；新旧记录不会互相覆盖。
- 原始工具结果完整保留，prompt 只携带有限的最近结果。
- 每轮工具调用是原子的；批量调用会被记录并拒绝，不会部分执行。
- Working context 中的事实和冲突只能引用已读全文记录；冲突至少需要两条已读证据。
- Memory 不自动进入 inspected evidence 集合。
- Receipt 不是 verified write；匹配的独立 read-back 才改变验证状态。
- 工具校验不放宽；允许模型依据公开错误自行修正的校验错误会有界反馈，安全、作用域和基础设施错误仍立即停止。
- 连续可恢复工具错误默认最多 2 次；第三次以 `recoverable_tool_error_limit` 干净停止。
- 无效 model output、iteration limit 和 tool-call limit 都能干净停止。
- 无效 final 最多反馈两次供 Agent 修正，证据边界不会因此放宽。

## Runtime versions 与工具错误分类

- `phase1-v1-frozen-challenge`：首次 unseen challenge 使用的冻结版本。任何 `ToolError` 都立即终止运行。
- `phase1-v2-recoverable-tool-errors`：首次 challenge 后形成的新版本，只增加 bounded recoverable tool-error feedback。既有病例、工具校验、临床判断与检索策略均未改变。

V2 的 recoverable allowlist：

- `invalid_arguments`：参数形状、字段和值不满足公开 schema；发生在状态写入前，模型可以修正或放弃。
- `uninspected_context_ref`：working fact/conflict 引用了未读全文记录；模型可以补读或移除该陈述。
- `unknown_context_ref`：相关记录引用尚未被发现；模型可以检索或移除引用。
- `context_limit`：有限 working context 超出公开大小边界；模型可以缩减快照。
- `uninspected_evidence_refs`：写操作引用未读全文证据；模型可以补读或放弃写入。

这些错误的共同条件是：由模型输入触发、错误信息足以指导下一步、工具在抛错前不应改变 RunState。Runtime 会验证 state 未变化，把原始错误作为最近 tool result 回送，并在任一次成功工具调用后重置连续错误计数。

`patient_mismatch` 是患者作用域安全错误；`unknown_tool` 表示工具接口不一致；`tool_failure`、错误期间 state mutation、未知错误码和其他内部异常可能涉及基础设施或不可判定副作用。这些错误不在 allowlist 中，继续 hard stop。

## 案例策略

公开 development case 可以用于修复与任何病例无关的接口和上下文问题。首次 runtime 冻结后只创建了一个中文 unseen challenge case，不提供 private gold、rubric 或自动临床 evaluator。首次 challenge 暴露通用 ToolError 过硬问题后形成 V2；对同一病例的后续运行只能称为 development/regression replay，不能再称为 unseen challenge。
