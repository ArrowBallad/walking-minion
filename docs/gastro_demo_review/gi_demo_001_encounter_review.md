# GI-DEMO-001 Encounter-Based Tool Calling & Memory Review

## 结论

本实验实现了真实的 encounter 时间隔离、增量 working context、append-only patient memory 的有效视图，以及逐记录的 prompt 可见性账本。无模型测试证明 E01/E02/E03 只能访问 5/13/18 条当时可用记录，直接猜未来 ID 也不能绕过边界。

三次 DeepSeek 运行的结果并不整齐：E01 因重复读取触发 tool-call limit，没有 final 或 memory；E02 成功完成，明确保留并输出了乳糜泻诊断和 Marsh 3b 病理；E03 的 memory 明确引导 Agent 优先读取新随访证据，但 step 3 构造 prompt 时超过 32 KB，未能生成 final 或解决旧 memory。

因此，实验验证了数据流机制，但没有验证稳定的端到端 encounter continuity。原始运行的诊断信息保留问题在 E02 内没有复现；E01 和 E03 则分别暴露了 Agent 停止/重复读取问题与新 runtime 的 prompt 体积缺陷。

## 1. 实现范围

### Encounter 与时间隔离

- `cases/gastro_demo/gi_demo_001/encounters.json` 定义：
  - E01：`2025-01-24T23:59:59Z`，5 条可用记录。
  - E02：`2025-03-07T23:59:59Z`，13 条可用记录。
  - E03：`2025-09-20T23:59:59Z`，18 条可用记录。
- `record_metadata.json` 在不改写原临床 JSONL 的情况下提供独立 `available_at`、可选 `exam_type`、`workflow_id` 和 `workflow_stage`。
- `MockDatabase(as_of=...)` 对 `search_records` 与 `get_record` 使用同一 availability boundary。
- 若历史正文引用了已知但尚未 available 的临床记录，投影视图会移除该引用；不存在的 ID 与未来 ID 都返回 `found=false`，不泄露差异。
- 原始 `clinical_records.jsonl` hash 仍为 `91871F...7B887`，18 个 record ID 未改变。

### 索引调整

- `record_type` 仍是文档类别。
- 索引新增非诊断性的 exam/workflow 元数据，允许按 `exam_type` 或 `workflow_id` 过滤。
- rec-011、rec-012、rec-017 的 search keywords 在 projection 中改为不暴露具体病理异常或治疗结果；全文未删改。
- search 返回存在性、时间、标题、preview、状态和 workflow metadata，不返回 `data`。

### Working context

- 模型可见的 `update_working_context` 改为增量 `operations`：
  - `add_finding`
  - `update_finding`
  - `supersede_finding`
  - `add_question`
  - `resolve_question`
- finding/question 使用稳定 `entry_id`；未涉及条目保持不变。
- supersession 和 resolution 有确定性语义；context 仍限制为最多 20 个 findings、20 个 question states、30 个 relevant refs。
- finding 和 resolution evidence 必须属于本 run 已全文读取的记录。未知或未读引用会被拒绝。
- 旧的完整快照参数仍由 controller 接受，仅用于兼容已有回归脚本；真实 encounter 工具 schema 只公布增量接口。

### Patient memory

- 底层保持 append-only JSONL。
- `effective_for_patient` 通过 `related_memory_id` 计算 active、resolved、superseded 视图。
- bounded view 优先保留 active unresolved，再保留其他 active；省略 active/inactive 数量明确记录。
- memory 保留 source run、evidence refs 和状态，但 prompt 明确声明它是 prior Agent work，不是当前事实。
- resolved/superseded memory update 必须关联一个当前有效视图中可见的旧 memory ID，并引用本 run 已读证据。

### Prompt 可见性

每轮 prompt 对每条已发现或已读记录提供：

- `discovered_through_search`
- `inspected_in_full`
- `full_content_currently_visible`
- `finding_persisted`
- `read_count`
- `last_read_step`

这些字段直接根据本轮实际选择的 recent tool results 和 current context 计算。被 recent window 淘汰、单条截断或因 prompt size 移除的正文均有明确原因。

## 2. 冻结版本与测试

- 原 runtime：`phase1-v4-execution-protocol-patch`。
- 新 runtime：`phase1-v4.1-encounter-memory`，包版本 `0.3.0`。
- Git commit 基线：`5491c687075301bf936f7c8628a572a4c49bbd38`；既有未提交 V4.1 改动全部保留。
- 请求模型：环境变量 `DEEPSEEK_API_KEY` + `deepseek-chat`；provider 响应元数据仍为 `deepseek-flash`。
- 每次固定预算：10 model steps、12 executed tool calls、32,000 prompt bytes。
- 运行前测试：`tests/` 16/16，`tests_phase1/` 47/47，总计 63/63；GI fixture validator 通过。
- 新测试覆盖 availability、直接 ID 绕过、索引泄漏、稳定 record IDs、增量 context、supersession/resolution、未读证据拒绝、memory patient scope/effective view、首轮 prompt 和实际 visibility。
- 完整 hashes 与 run IDs 位于 `runs/gastro_demo_encounters/experiment_manifest.json`。
- 上一实验 `p1-run-e76579fe5063` 的七个原始 artifact hashes 逐一复核，全部未改变。

## 3. 真实运行总览

| Encounter | Run | 状态 | Model calls | Tool requested / executed / rejected | Search | Full reads unique/total | Context updates | Memory loaded/written | Tokens |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| E01 | `p1-run-e544b90b3855` | stopped: `tool_call_limit` | 5 | 14 / 10 / 4 | 1 | 5 / 8 | 0 | 0 / 0 | 28,302 |
| E02 | `p1-run-619c4d4f6844` | complete: `final_response` | 5 | 11 / 11 / 0 | 1 | 8 / 8 | 1 | 0 / 4 | 40,545 |
| E03 | `p1-run-bf871e9a57f2` | error: `prompt_context_too_large` | 2 | 6 / 6 / 0 | 1 | 4 / 4 | 0 | 4 / 0 | 14,366 |

E01 的 4 个 rejected calls 属于同一 atomic batch：剩余预算为 2 时模型请求 4 个调用，整批未执行。E03 没有 tool rejection；错误发生在完成第二批工具后、构造 step 3 prompt 时。

## 4. E01：初始症状、异常实验室与转诊

### 信息访问与临床覆盖

Agent 一次 search 发现恰好 5 条可用记录，并最终全文读取全部 5 条：症状前 CBC、基层症状记录、血常规/铁代谢、粪便筛查和转诊申请。没有 E02/E03 record ID、病理结果或随访数值进入任何 E01 prompt。

从已读证据看，它已经具备形成正确阶段性结论的材料：既往 Hb/MCV 正常，随后出现慢性非血性稀便、体重下降和典型缺铁性小细胞性贫血；常见感染筛查阴性、钙卫蛋白不高；消化内科转诊已申请和排期。此时不应按后来信息评价乳糜泻诊断。

### 失败机制

step 2 读取 rec-002/003/004/005，step 3 读取 rec-001。此时所有可用记录均已读。模型没有调用增量 context，也没有结束；step 4 因前三条正文退出 recent window而重读 rec-002/003/004。step 5 又请求重读 rec-005/001、再次 get patient 和再次 search。该 4-call batch 超过剩余预算 2，被整体拒绝。

最早失败点是 step 4 的决策：模型已拥有全部证据，却选择重读而不是持久化关键事实或生成 final。visibility ledger 正确揭示正文已淘汰，但没有自动替 Agent 选择 context update；这符合“不自动抽取事实”的设计，也暴露模型仍可能用重复读取代替信息保留。

结果：5 个 available records 全部 inspected，但 0 finding persisted、0 memory written、无 final。E02 因此没有可继承的 E01 memory。

## 5. E02：诊断调查与已建立诊断

### 检索、context 与 final

E02 没有 E01 memory，只能重新定向。Agent 一次 search 发现 13 条可用记录，选择读取：

- 新证据：rec-006/007/008/011/012/013。
- 既往背景：rec-003/004。
- 未读：rec-001/002/005/009/010。

step 3 使用一次增量 update 添加 5 个稳定 findings：诊断、血清学、组织学、内镜和治疗计划。所有条目在后续批次离开 recent window 后仍保留于 context。step 5 final 明确写出乳糜泻、Marsh 3b、绒毛萎缩/隐窝增生/上皮内淋巴细胞、内镜所见和终身无麸质饮食计划。

这正面修复了原始单次运行中“病理读过两次却没有进入 final”的问题。E02 没有重复全文读取，并在 11 个工具调用内完成。

### 临床评价

主要结论与 encounter-specific reference 一致：截至 2025-03-07，显著阳性血清学、含麸质背景和 Marsh 3b 十二指肠病理共同支持已明确的成人乳糜泻；缺铁性贫血与吸收不良相符；治疗和营养随访已计划但尚无后续结果。

需要保留两点批评：

1. final 称粪便检查“排除了常见的感染性/炎症性病因”。对常见已检测感染可作较强排除，但正常 CRP/钙卫蛋白只能降低活动性 IBD 可能性，不能绝对排除炎症性病因；reference 的表述更谨慎。
2. Agent 没有读取 rec-009/010，因此没有用全文逐项重建“申请/排期 → 操作完成 → 内镜报告 → 病理结果”。它没有把申请错当成完成，也能从 rec-011/012 建立报告和诊断，但工作流覆盖仍不完整。

### Memory

E02 写入 4 条 active memory：2 条 prior work、1 条 unresolved、1 条 next step。内容覆盖已确诊乳糜泻、检查依据、待评估的 GFD 应答/营养结果/营养师转介，以及下次应优先查找的随访证据。

其中“感染性病因已排除”与“缺铁性贫血归因已闭合”表述偏强。因为 memory 明确标为 prior Agent work 而非事实，这类偏差不会自动成为下一 run 的证据，但可能影响检索重点，必须由后续当前记录复核。

## 6. E03：治疗反应与随访

### Memory 是否影响行为

是。E03 首轮 prompt 加载 E02 的 4 条 active memory。模型在 step 2 明确根据 unresolved/next-step memory 把 rec-014–018 识别为新资料，计划先读随访、营养教育和补充方案，再补读营养基线与此前计划。

实际已读：rec-018、017、016、015。它因此获得了症状消失、体重回升、无意交叉接触、随访 Hb/MCV/铁蛋白/叶酸/维生素 D/tTG-IgA，以及营养师教育与补充方案。没有重复读取，也没有访问边界错误。

### Runtime 失败

step 1 prompt 为 17,587 bytes；search 后 step 2 已达 31,590 bytes。第二批四条全文执行成功后，runtime 在构造 step 3 时即使移除全部 recent tool results 仍超过 32,000 bytes，遂以 `prompt_context_too_large` 停止。

失败来自本次实现的 prompt 结构叠加：18 条记录同时出现在 retrieval IDs、catalog 和 visibility ledger；4 条 E02 memory 较长；被淘汰正文又进入 omission ledger；recent activity 继续保留调用摘要。单项都合理，但组合后没有统一的整体预算或压缩策略。这是可复现的 runtime/context implementation failure，不是 DeepSeek 临床决策失败，也不是病例过长。

因为 step 3 从未成功构造，Agent没有机会读取 rec-014、重读 rec-013、增量保存新 findings、resolve E02 unresolved memory 或生成 E03 final。E02 的 unresolved memory 因而仍为 active，尽管 rec-016/017/018 已能解决其中大部分问题。

按任务卡停止条件，本次不修复、不扩 context、不补跑 E03。

## 7. 效率与原始保留问题

- E01：8 次全文读取覆盖 5 条唯一记录，3 次重复；0 context update，未完成。
- E02：8 次全文读取覆盖 8 条唯一记录，0 重复；1 次 context update，完成。
- E03：4 次全文读取覆盖 4 条唯一记录，0 重复；在 context update 前失败。
- 三次合计 27 个实际工具调用和 12 个模型调用，只产生 1 个 final。不能把工具总数低于原始 run 的 29 视为效率改进，因为 E01/E03 均未交付最终评估。

原始临床信息保留问题的回答是“局部解决、总体仍未解决”：

- E02 证明增量 context 能让诊断与病理在正文淘汰后保留到 final。
- E01 证明接口存在不等于 Agent 会使用；它仍通过重读应对正文淘汰。
- E03 因 runtime prompt failure 未能测试最终综合与 memory resolution。

## 8. 对任务卡最终问题的明确回答

### Agent 能否在未收到完整病历时用工具发现相关信息？

能。三个 encounter 的首轮均只有通用 task、patient ID、as-of 与 memory view；Agent 先 search 索引，再选择全文。时间边界分别严格限制为 5/13/18 条。

### 是否正确区分 available、inspected、currently visible 与 remembered？

Runtime 和 artifacts 可以精确区分。E03 模型也明确把 memory 视为检索线索，并计划重读记录后再作为当前证据。E01 则虽识别正文已不可见，却选择重复读取而非持久化，说明行为层面仍不稳定。

### Memory 是否实质影响后续 encounter？

E01 没有写 memory，所以 E02 无连续性。E02 memory 对 E03 有明确、可追踪的影响：它直接引导模型优先选择 rec-014–018，而非重读全部诊断记录。但 E03 runtime failure 阻止了 memory 的闭环更新。

### Agent 是否回访并解决既往不确定性？

它在 E03 回访了相关证据并取得可解决问题的信息，但没有完成 resolve 操作或 final。因此答案是“开始回访，但未完成解决”。

### 每个 encounter 需要多少调用？

- E01：5 model calls；14 requested tool calls，10 executed，4 rejected。
- E02：5 model calls；11 requested/executed tool calls，0 rejected。
- E03：2 model calls；6 requested/executed tool calls，0 rejected，随后 prompt 构建失败。

### 原始 run 的临床信息保留问题是否复现？

E02 未复现：核心诊断和 Marsh 3b 被持久化并正确进入 final。E01 的重读行为仍体现同一根因，E03 无 final 可评估。因此不能声称问题已整体解决。

### 数据流是否适合离线 Viewer？

基本适合，而且比原始 artifacts 更适合：metadata 含 encounter/as-of，记录有 availability/workflow，state 含 read counts，prompt snapshots 含真实 visibility，memory view 区分 active/inactive，增量 operations 和 call outcomes 可逐步回放。

Viewer 必须忠实展示 E01/E03 失败而不是只展示 E02 成功，并标明 memory 是 prior work。当前还缺少 step 3 oversized prompt 的候选 payload 与分项 byte accounting；现有 trace 只能看到 stop event 和前一 prompt，无法完整解释是哪一块把 prompt 推过限制。这是 Viewer/调试可观测性的已知限制。

## 9. 后续建议（本次不实施）

1. 在新的任务中先为 E03 failure 建立通用 prompt-budget regression，再讨论压缩重复的 catalog/visibility/omission 表示；不要为本病例增加特殊上限。
2. 保留增量 context 与 effective memory 设计，它们在 E02 和 E03 选择行为中已显示价值。
3. 对 memory 中“排除”“归因已闭合”等强结论增加人工审查提示；不要自动把 prior work 升格为事实。
4. 未来可在 prompt 构建失败前保存不含临床正文的 component byte summary，便于 Viewer 解释错误，但不要保存或合成不存在的模型响应。

本任务到此停止，不继续自动优化、不补跑任何 encounter。
