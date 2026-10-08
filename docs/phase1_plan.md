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
- 参数错误、tool error、无效 model output、iteration limit 和 tool-call limit 都能干净停止。
- 无效 final 最多反馈两次供 Agent 修正，证据边界不会因此放宽。

## 案例策略

公开 development case 可以用于修复与任何病例无关的接口和上下文问题。Runtime 冻结后只创建一个中文 unseen challenge case，不提供 private gold、rubric 或自动临床 evaluator。Challenge 后不再修改冻结 runtime；只根据 trace 分析检索、工作认知、时间处理、冲突处理、停止策略和失败模式。
