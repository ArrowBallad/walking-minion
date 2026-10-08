# Phase 1 实施计划

Phase 1 验证可检查的 Agent runtime，而不是预先规定的临床答案。

## 硬性基础设施不变量

- Agent 只能通过已注册工具取得患者数据。
- 案例数据、运行状态、prompt snapshot、患者记忆和 trace 分开存储。
- 临床时间不能被运行时间戳替代。
- 新旧记录都保持可单独检查。
- 原始工具输入和输出完整保留。
- Memory 是既往工作，不是当前临床证据。
- 写入 receipt 不等于写入已经验证。
- 验证需要单独且匹配的 read-back。
- 参数错误、工具失败、模型输出格式错误和 iteration limit 都能干净停止。
- 保存的 artifacts 可以还原完整可观察执行过程，但不保存隐藏思维过程。

这些性质使用确定性自动测试验证。

## 观察性的 Agent 行为

Runtime 不编码正确工具顺序、临床结论、订单、未解决问题或 memory 内容。这些内容在运行后检查，不写入基础设施断言。

## 案例

`cases/development/gi_dev_001` 是公开 development case，可以用于调试数据链路。Runtime 基本冻结后，在 `cases/challenge/` 中加入一个具有实质差异的 challenge case。它不包含 private gold、rubric 或自动临床 evaluator；运行 trace 在 `docs/challenge_analysis.md` 中人工审查。

如果在查看 challenge 行为后修改 runtime，后续运行必须标记为探索性运行，不能继续称为对冻结 runtime 的 unseen observation。

所有当前及后续案例的任务说明和病例叙述均使用中文。
