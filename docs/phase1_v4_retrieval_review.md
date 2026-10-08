# Phase 1 V4 retrieval-awareness review

Runtime：`phase1-v4-retrieval-awareness`  
Run：`p1-run-8b4b4e45accc`  
Case label：`cough_stopping_001_regression_replay_v4`

这是对既有合成病例的 development/regression replay，不是 unseen challenge。本次只观察 retrieval awareness、stopping 与 provenance，不评价某个临床结论是否为预设正确答案。

## 运行结果

- Step 15 主动产生合法 final，未达到 30 轮 iteration limit。
- 15 次模型响应，10 次实际工具调用：1 次索引搜索、7 次全文读取、2 次 working-context 更新。
- 35 条病例记录中读取 7 条全文；首次搜索发现 20 条，停止时仍有 13 条已发现但未读、合计 28 条未读。
- 只有 1 次搜索，没有重复搜索；7 个 `get_record` ID 均不重复。
- `has_more=true`，但 Agent 没有为了翻完全部 35 条记录继续分页。
- 3 次批量工具请求被原子调用契约拒绝，另有 1 次空 final 被校验拒绝；模型随后均继续推进，没有形成机械循环。

## `cs-rec-028` 与 persistent catalog

Step 1 的搜索返回 `cs-rec-028`，索引元数据为：type=`result`、title=`胸部 X 线报告`、preview=`当前干咳评估中的实际影像结果。`。

搜索原始结果在 step 4 已经离开 recent tool results。此后直到读取前，step 4–12 的每个 prompt 仍通过 `retrieval_catalog` 携带上述元数据，并明确显示 `inspected=false`。Agent 在 step 11 的批量请求及 step 12 的原子请求中选择该记录，step 12 成功读取全文；step 13–15 的 catalog 随即显示 `inspected=true`。

因此本次没有再出现 V3 中“已经发现影像报告，却在后期只剩 ID、最终将结果描述为未明确发现”的现象。

## Stopping 与 provenance

- Agent 读取症状记录、相关用药、停药后随访、胸片报告和病毒检测后主动 final，没有退化为读取所有候选。
- Final evidence refs 为 `cs-rec-024`、`cs-rec-025`、`cs-rec-026`、`cs-rec-028`、`cs-rec-029`、`cs-rec-030`、`cs-rec-031`，全部属于 `inspected_record_ids`。
- 未读全文的 X 线操作记录 `cs-rec-027` 只被明确描述为索引所示的工作流记录，并注明不作为临床结论证据；它没有进入 final evidence refs 或 known facts。
- Final 保留“2026-02-15 之后是否完全消失”的 unresolved question，同时根据 catalog 判断明显无关的后续候选无需读取。

## 人工判断

主分类：**retrieval-awareness 已解决**。V4 使已发现候选在 raw search result 退出后仍保留可供 Agent 选择的语义，且没有破坏 V3 stopping。

仍有一个输出层面的 task-completion 精度问题：Agent 已读取 `cs-rec-031`，其全文明确写有“干咳已基本消失，仅清晨偶尔轻咳”，但 final 只笼统写成“记录症状变化”，没有把这一已读的实际转归直接呈现出来。这不是候选发现或证据边界失败，也不据此修改 V4 runtime；若后续研究，应把它视为 Agent 对已读证据的使用质量，而不是继续增加检索规则。

完整原始 artifacts 保存在 `runs/phase1/p1-run-8b4b4e45accc/`。
