# Phase 1 V2 regression replay 简短审查

Runtime：`phase1-v2-recoverable-tool-errors`  
Run：`p1-run-f79dd2cbe3f0`  
Case label：`dizziness_longitudinal_001_regression_replay_v2`

这是首次 unseen challenge 之后、使用同一 `dizziness_longitudinal_001` fixture 的 development/regression replay，不是新的 unseen challenge。Fixture 中原有 task 文本未改动；运行身份以 metadata 和本审查为准。

## 结果

- 状态：`stopped`
- Stop reason：`iteration_limit`
- 30 次模型响应，27 次实际工具调用。
- 10 次全文读取，涉及 9 个唯一记录；`dz-rec-017` 重复读取一次。
- 9 次 working-context 更新尝试，停止时保留 10 条 facts、6 个 unresolved questions。
- 3 次批量工具响应被原子调用契约拒绝，均未执行。
- 0 次 recoverable ToolError，0 次 final rejection，0 次订单或 memory 写入。

完整 artifacts：`runs/phase1/p1-run-f79dd2cbe3f0/`。

## 四项重点观察

### 1. 收到错误反馈后是否自行恢复

本次真实 replay 没有触发 allowlist 内的 ToolError，因此没有向 Agent 发送 recoverable tool-error feedback；这一问题在该 run 中不可观察，不能把“没有再次因 ToolError 终止”解释为模型成功恢复。

机制层面由 scripted tests 验证：一次 `invalid_arguments` 会保留完整失败 input/output，把 code/message 放入下一轮最近 tool result；模型随后可以提交合法 context 并完成。第三个连续 recoverable error 会以 `recoverable_tool_error_limit` 停止。测试不证明真实模型在每次情形下都会纠正，只证明 runtime 给了它有界纠正机会。

### 2. 是否补读 conflict 另一侧，或合理放弃

Agent 没有读取 `dz-rec-019` 或 `dz-rec-020`，也没有再次提交药物 conflict。它不是在收到错误后主动放弃，而是在这次随机轨迹中没有走到首次 challenge 的同一个错误点。因此不能据此判断它是否会针对该错误补读另一侧。

它确实读取了停药后的复查电解质 `dz-rec-025`，并把“具体停药时间与症状改善尚未核实”保留为 unresolved question；但直到停止仍未读取停药条目或电话随访全文。

### 3. 是否主动产生 final

没有。第 30 轮模型仍请求同时读取 `dz-rec-022` 和 `dz-rec-024`；响应因原子调用契约被拒绝，随后达到 iteration limit。没有 final JSON、final evidence、订单或 memory update。

### 4. 是否出现机械循环或重复读取

没有持续机械循环，但存在有限重复和检索低效：

- `dz-rec-017` 在 step 18、19 连续读取两次。
- Step 3 已完成第一页宽搜索，step 10 又重复第一页宽搜索。
- 一次按不存在的 `record_type="encounter"` 搜索为空；一次以日期字符串作为 keyword 搜索为空。
- 正面变化是 Agent 在 step 6 主动使用 `cursor="20"` 读取第二页，最终发现了全部 40 个索引记录。

## 结论

V2 达成了工程目标：recoverable ToolError 不再必然终止整次运行，且 hard-stop 边界、工具校验和 state isolation 保持不变。但这次 replay 没有自然复现该错误，所以无法从真实 Agent 行为证明它会如何纠正。该 run 暴露的主要行为限制仍是停止策略：模型持续补查和更新 context，没有在 30 轮内主动收敛到 final。按本阶段约束，不据此增加病例专属规则、工具顺序或临床判断。
