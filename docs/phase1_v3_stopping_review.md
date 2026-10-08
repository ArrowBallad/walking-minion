# Phase 1 V3 stopping behavior review

Runtime：`phase1-v3-stopping-contract`

本审查只观察 stopping behavior，不评价临床答案，不把某个停止轮次或检索顺序设为正确答案。V3 没有 deterministic sufficiency rule、planner、completion tracker、新工具或自动停止逻辑。

## 1. Dizziness development/regression replay

Run：`p1-run-de4bffa55f0b`  
Case label：`dizziness_longitudinal_001_regression_replay_v3`

结果：step 14 主动产生合法 final，未达到 30 轮上限。

- 14 次模型响应、10 次实际工具调用。
- 4 次索引搜索、3 次全文读取、2 次 working-context 更新。
- 读取全文：`dz-rec-014`、`dz-rec-015`、`dz-rec-024`。
- 40 条 case records 中只读取 3 条；已发现 18 条中仍有 15 条未读。
- Final 时 working context 保留 3 个 unresolved questions；final 正文也明确保留未读心电、化验、动态心律监测和超声结果等不确定性。
- 0 次重复全文读取。
- Final evidence refs 为 `dz-rec-014`、`dz-rec-015`、`dz-rec-024`、`patient-DZ-LONG-001`，全部通过 inspected/patient 边界。
- 3 次批量工具响应被原子调用契约拒绝；其中 step 12 一次返回 10 个 get_record 请求，说明“批量补齐”冲动仍存在。随后模型只读取了电话随访 `dz-rec-024`，并在下一轮结束。

对观察问题的回答：

- 能否在 iteration limit 前 final：能，step 14。
- Final 时仍保留什么：检查结果未读、药物相关性尚非完全确定、停药后更长期随访和血压复测缺失。
- 是否因未读记录继续无意义检索：没有读完索引或翻完病例；但宽搜索有重复，检索层面仍有低效。
- 是否重复搜索/全文：多次宽搜索返回重叠索引；全文无重复。
- Evidence 边界：全部合法。

## 2. 新 unseen stopping case

Case：`cases/phase1_challenge/cough_stopping_001/`  
规模：35 条记录，其中核心问题可由少量近期记录回答，并包含大量无关历史记录。无 gold、rubric 或写入任务。该 case 在 V3 哈希记录后创建。

Run：`p1-run-ef4785479f90`  
Case label：`cough_stopping_001_unseen_v3`

结果：step 16 主动产生合法 final，未达到 iteration limit。

- 16 次模型响应、12 次实际工具调用。
- 2 次索引搜索、5 次全文读取、4 次 working-context 更新。
- 读取全文：`cs-rec-029`、`cs-rec-002`、`cs-rec-025`、`cs-rec-031`、`cs-rec-030`。
- 35 条记录中仍有 30 条未读；已发现 14 条中有 9 条未读。
- Final 时保留 2 个 unresolved questions：咳嗽相关检查工作流/结果是否存在，以及更晚随访或复发是否存在。
- 0 次重复全文读取。
- Final evidence refs 为 5 个 inspected records 加 patient record，未包含 preview-only ID。
- 2 次批量工具响应被拒绝。
- Step 12 已尝试 final，但 JSON 格式错误；收到 final validation feedback 后，模型读取一条相关替代用药记录、更新 context，并在 step 16 成功 final。

对观察问题的回答：

- 能否在 iteration limit 前 final：能，step 16。
- Final 时仍保留什么：未读检查结果与缺少长期复发随访。
- 是否因未读记录继续无意义检索：没有。Agent 明确表示这些次要不确定性不改变核心 assessment，并在 30 条记录仍未读时结束。
- 是否重复搜索/全文：无重复全文；两次搜索目的不同。
- Evidence 边界：全部合法。

人工提醒：索引中实际存在标题明确的胸部 X 线报告 `cs-rec-028`，但 Agent 没有读取，并在 final 中把相关检查概括为未验证缺口。它没有把 preview 当成事实，符合证据边界；但对索引覆盖的文字描述并不完全精确。这是输出质量观察，不据此修改 stopping contract。

## 3. 总结

在同一 V3 runtime 下，一个已见 regression case 和一个新 unseen case 都在 iteration limit 前主动 final，同时保留 unresolved questions，并在大量记录未读时停止。与 V2 dizziness replay 的 30 轮未收敛相比，这支持 stopping contract 对行为产生了预期方向的影响，但两次样本不足以证明稳定性或临床正确性。

残余问题主要是工具调用形式与检索效率：模型仍会偶发批量调用、重复宽搜索或产生无效 final JSON。当前阶段不增加 planner、completion tracker 或病例专属规则。

两个 final 都按既有 schema 产生了 patient memory updates；本阶段没有重新加载这些 memory、没有开展跨 run 比较，也不把它们纳入 stopping 结论。
