# GI Demo 五患者三阶段 Encounter Evaluation

## 结论

本次将统一的 `phase1-v4.3-context-retry` runtime 应用于 GI-DEMO-001 至 GI-DEMO-005，每名患者按真实时间线执行 E01→E02→E03，共 15 次真实 DeepSeek Agent 运行。每次 Encounter 只运行一次，没有因失败或临床输出不理想而重跑，也没有在看到结果后修改 Prompt、预算、证据规则或临床数据。

结果为 14 次 `complete/final_response`、1 次 `stopped/tool_call_limit`。但 `complete` 不等于临床任务成功：按既有 independent clinical reference 做人工有限审查，10 次可评为通过、4 次部分通过、1 次失败。

基础设施层面表现稳定：未发现未来记录读取、跨患者 Memory 串用、evaluator reference 进入 Prompt 或 32 KB Prompt 超限。主要缺陷属于模型决策和信息保留：Working Context 只在 3/15 次运行中成功写入，共发生 16 次重复全文读取；GI-DEMO-004 E03 甚至读取了关键的 rec-018 数值，随后因未持久化而在 Final 中将其错误标为未知。

## 数据与 Encounter 设计

原始病例正文、record ID 和临床结论均未修改。GI-DEMO-002 至 005 新增了与 GI-DEMO-001 同结构的 `record_metadata.json` 和 `encounters.json`：

| Patient | 病例记录 | E01 | E02 | E03 |
| --- | ---: | --- | --- | --- |
| GI-DEMO-001 | 18 | 初始症状与初筛，5 条 | 诊断调查与治疗计划，13 条 | 六个月随访，18 条 |
| GI-DEMO-002 | 18 | 持续吞咽困难与转诊，5 条 | 胃镜/病理与治疗启动，12 条 | 客观治疗反应与维持，18 条 |
| GI-DEMO-003 | 15 | 急性发作与出院，7 条 | 缓解后结肠镜/病理，13 条 | 结果计划与远期随访，15 条 |
| GI-DEMO-004 | 18 | 血便初筛与转诊，4 条 | 诊断、诱导治疗与初次缓解，13 条 | 停药复发与再次缓解，18 条 |
| GI-DEMO-005 | 18 | 胆绞痛、结石与择期计划，7 条 | 急性住院与 MRCP/ERCP 决策，15 条 | 胆囊切除、病理与术后随访，18 条 |

`available_at` 依据每条记录真实可获取时间设置。MockDatabase 在 as-of 投影时会移除指向未来记录的引用，因此保留了原始工作流链接，同时避免 request 或 procedure 记录泄漏未来 report/pathology ID。

## 确定性验证

统一验证器覆盖五名患者，而不是为每个病例复制一套脚本。验证项目包括：

- 87 条临床记录的 JSONL schema、patient ownership、全局 record ID 唯一性与时间顺序。
- 五套 metadata 与原始记录一一对应，15 个 Encounter 的实际可访问数量符合配置。
- `search_records` 与 `get_record` 使用相同 as-of 边界；直接猜测未来 ID 返回不可见。
- 当前可见记录中的未来工作流引用被投影移除，索引不含正文 `data`。
- request、procedure、report、pathology 引用均指向真实存在的稳定 record ID。
- 五名患者的 Memory 按 patient ID 隔离。
- task 不包含诊断答案、目标 record ID 或 evaluator-only marker。
- 原有病例验证器通过；`tests` 16/16、`tests_phase1` 54/54，总计 70/70。

## 15 个 Encounter 汇总

Tool calls 为 requested/executed/rejected；Reads 为唯一全文读取/重复全文读取；WC 为成功/尝试（最终 findings 数）；Memory 为 loaded/written/transitions。`rejected` 包含未执行的原子批次调用和已执行但被 Controller 拒绝的调用。

| Patient | Enc | 可用记录 | 状态/停止原因 | Model | Tools | Reads | WC | Memory | Max prompt | Final | 关键失败 |
| --- | --- | ---: | --- | ---: | --- | --- | --- | --- | ---: | --- | --- |
| GI-DEMO-001 | E01 | 5 | complete/final_response | 4 | 8/8/0 | 5/0 | 1/1 (4F) | 0/3/0 | 23,620 | Y | — |
| GI-DEMO-001 | E02 | 13 | complete/final_response | 6 | 12/12/0 | 10/0 | 0/0 (0F) | 3/3/1 | 28,525 | Y | 已读关键病理/结果告知，却未报告确诊措辞与 Marsh 3b |
| GI-DEMO-001 | E03 | 18 | complete/final_response | 5 | 11/11/0 | 7/2 | 0/0 (0F) | 6/6/3 | 31,774 | Y | 两次重复读取 |
| GI-DEMO-002 | E01 | 5 | complete/final_response | 4 | 7/7/0 | 5/0 | 0/0 (0F) | 0/2/0 | 21,958 | Y | — |
| GI-DEMO-002 | E02 | 12 | complete/final_response | 3 | 6/6/0 | 4/0 | 0/0 (0F) | 2/2/0 | 27,622 | Y | — |
| GI-DEMO-002 | E03 | 18 | complete/final_response | 5 | 11/11/0 | 6/2 | 0/0 (0F) | 4/3/1 | 31,833 | Y | 两次重复读取 |
| GI-DEMO-003 | E01 | 7 | stopped/tool_call_limit | 5 | 13/9/4 | 7/0 | 0/0 (0F) | 0/0/0 | 23,515 | N | 余 3 次额度时请求 4 次调用；整批拒绝，无 Final/Memory |
| GI-DEMO-003 | E02 | 13 | complete/final_response | 7 | 12/12/0 | 7/2 | 0/0 (0F) | 0/1/0 | 26,687 | Y | Final 格式两次被拒；关键息肉病理细节遗漏；1 条 Memory 被拒 |
| GI-DEMO-003 | E03 | 15 | complete/final_response | 5 | 11/11/0 | 6/2 | 0/0 (0F) | 1/2/0 | 28,550 | Y | 误读 NSAID 记录；两次重复读取 |
| GI-DEMO-004 | E01 | 4 | complete/final_response | 5 | 9/9/1 | 4/0 | 1/2 (4F) | 0/0/0 | 24,458 | Y | 非法 patient evidence 被正确拒绝；合法 findings 保留 |
| GI-DEMO-004 | E02 | 13 | complete/final_response | 3 | 6/6/0 | 4/0 | 0/0 (0F) | 0/3/0 | 26,936 | Y | E01 未写 Memory，故本次实际从空 Memory 开始 |
| GI-DEMO-004 | E03 | 18 | complete/final_response | 5 | 12/12/0 | 5/4 | 0/0 (0F) | 3/2/0 | 31,023 | Y | 已读 rec-018=48，却在 Final 中称数值未知 |
| GI-DEMO-005 | E01 | 7 | complete/final_response | 3 | 6/6/0 | 4/0 | 0/0 (0F) | 0/0/0 | 23,454 | Y | 未写 Memory |
| GI-DEMO-005 | E02 | 15 | complete/final_response | 5 | 11/11/0 | 7/0 | 1/1 (5F) | 0/2/0 | 27,961 | Y | — |
| GI-DEMO-005 | E03 | 18 | complete/final_response | 6 | 12/12/0 | 4/4 | 0/0 (0F) | 2/3/1 | 23,910 | Y | 首次 Final 越过本轮证据边界；修正后四条记录全部重读 |

合计：71 次模型决策，147 次工具请求、143 次实际执行、5 次拒绝；85 次唯一全文读取、16 次重复读取；4 次 Working Context 尝试中 3 次成功；加载 21、写入 32 条 Memory，形成 6 个 resolved/superseded transition；4 次 Final 被拒后修正。总 token usage 为 489,912，Prompt 峰值 31,833 bytes。

## 分患者简短审查

### GI-DEMO-001 — 部分通过

- E01 完整读取 5 条记录，成功保存 4 个 Working Context findings，并形成包含 unresolved 与 next step 的 Memory；贫血、慢性稀便、体重下降、感染筛查与转诊状态均表达准确。
- E02 正确区分申请、操作、报告和病理，也形成了一个 resolved transition；但模型明明读取了 rec-007、rec-012、rec-013，却在 Final 中声称缺少病理/诊断精确措辞，没有报告 tTG-IgA >128、EMA 1:160、Marsh 3b 和正式乳糜泻诊断。这是信息保留/任务完成失败，不是数据访问问题。
- E03 报告了症状缓解、体重 60.4 kg、Hb 12.7、MCV 86、铁蛋白 24、维生素 D 29、tTG-IgA 19.8 和交叉暴露，并用三个 Memory transition 关闭旧事项；临床结论合理，但 rec-017/018 各重复读取一次。

### GI-DEMO-002 — 通过

- E01 正确识别持续固体食物吞咽困难、低剂量 PPI 仅改善烧灼感而吞咽困难持续、短暂食物停顿、特应性背景和转诊状态。
- E02 只读取四条关键正文即区分内镜报告、食管分层病理、诊断说明和高剂量 PPI 处方；准确报告近端 32、远端 48 eos/HPF，并避免把症状当作组织学缓解。
- E03 读取复查 request/procedure/report/pathology 与维持计划，准确报告近端 3、远端 5 eos/HPF、组织学反应与 PPI 减至每日一次，并通过 Memory transition 关闭复查待办。主要不足仅为两次重复读取和长期随访间隔仍未确定。

### GI-DEMO-003 — 部分通过，E01 失败

- E01 已经读取全部 7 条可用记录，但没有写 Working Context；当剩余预算为 3 时又请求 `get_patient` 加 3 次重复读取，共 4 调用，Controller 按原子预算规则整批拒绝，最终无 Final、无 Memory。这是模型预算决策失败，Controller 行为正确。
- E02 面对真实的空 Memory，仍找到 CT、缓解随访、结肠镜及息肉病理并完成；但两次输出非 JSON Final，被 runtime 纠正。最终只说“息肉最终组织学回报”，没有报告 6 mm 管状腺瘤、低级别异型增生、无高级别异型增生/癌这一关键结果；另有一条引用 E02 本轮未读记录的 Memory update 被正确拒绝。
- E03 最终补足腺瘤结果、7–10 年监测计划和半年无憩室炎复发。但它把 rec-015 的“不规律使用 NSAID”（即没有规律使用）误读成“NSAID 仍未改善/需要纠正”，属于临床语义错误。

### GI-DEMO-004 — 部分通过

- E01 成功保存 4 个 findings。随后尝试把 `get_patient` 返回的 patient ID 当作临床全文证据写入，Controller 返回 `uninspected_context_ref`，保持状态不变并保存 retry context；模型没有重读病历，放弃非法人口学 finding 后正常 Final。这是结构化错误与有界 retry 在真实运行中的正向验证。
- E02 因 E01 没有写 Patient Memory，正确面对空 Memory；通过 rec-010 至 013 建立溃疡性直肠炎诊断、局部美沙拉嗪治疗、临床缓解和粪钙卫蛋白降至 72，结论基本准确。
- E03 读取了复发、感染阴性/粪钙卫蛋白 380、恢复美沙拉嗪、症状再次缓解以及 rec-018 的粪钙卫蛋白 48。随后它重复读取 rec-014 至 017，将 rec-018 挤出 recent results，又因未使用 Working Context 而丢失 48 这一数值。Final 错称“本轮未能读取具体数值”，并把一个已经解决的问题写成新的 unresolved Memory。这是本组最清楚的信息保留失败。

### GI-DEMO-005 — 通过

- E01 准确区分胆绞痛、间歇期正常实验室、已完成超声报告和仅处于计划状态的择期胆囊切除；没有把排期当作已完成手术。
- E02 正确识别轻症胆源性胰腺炎、MRCP 未见残留胆总管结石、胆红素/ALT 下降、不做 ERCP 的依据，以及原择期手术被同次住院手术计划取代；成功保存 5 个 Working Context findings。
- E03 首次试图直接用 Memory 中的四条记录作为本轮 Final evidence，被 runtime 拒绝；随后实际读取 rec-015 至 018，确认胆囊切除完成、病理良性和术后恢复，并用 transition 关闭手术待办。四条记录各重复读取一次，但最终临床结论正确。

## 跨病例评价

### 数据访问与 Runtime

- 数据访问错误：0。所有 inspected record 均在相应 as-of 前可用。
- Runtime 工程错误：未发现新的阻断性缺陷。Prompt Budget Manager、批次原子预算、Final evidence validation、Memory patient scope 和 structured context error 均按设计工作。
- 真实 recoverable context error：GI-DEMO-004 E01 的失败操作被精确定位，合法 Working Context 未受影响，下一轮无需重读即可结束。
- Prompt 容量：所有运行均低于 32,000 bytes；最高为 GI-DEMO-002 E03 的 31,833 bytes。

### 模型决策与信息保留

- Working Context 使用率低：15 次中只有 GI-DEMO-001 E01、GI-DEMO-004 E01、GI-DEMO-005 E02 成功写入。
- 16 次重复读取集中出现在后期 Encounter，表明模型仍倾向以重读替代主动持久化。
- GI-DEMO-003 E01 已读取全部记录后仍超预算请求一批 4 次调用，说明明确预算提示不能保证模型遵守。
- GI-DEMO-001 E02 和 GI-DEMO-004 E03 都出现“已经读取关键正文，却在 Final 中说不知道”的任务完成失败。
- Memory 能够指导后续检索并形成 6 个 transition，但并非每名患者都完成完整生命周期；空 Memory 被如实传递，没有人工注入答案。

### 临床解释

- 绝大多数诊断方向、工作流状态和治疗反应解释与 independent reference 一致。
- 明确错误包括 GI-DEMO-003 E03 对“不规律使用 NSAID”的反向解读。
- 关键遗漏包括 GI-DEMO-001 E02 的乳糜泻具体诊断证据、GI-DEMO-003 E02 的管状腺瘤病理细节，以及 GI-DEMO-004 E03 已读到的粪钙卫蛋白 48。
- 历史 Memory 大多被明确标成检索背景；没有发现因 Memory 直接绕过本轮 evidence validator 而成功提交的 Final。

## 停止判断

已满足本轮停止条件：15 次运行、全部原始 artifacts、统一 manifest、逐患者审查和总体汇总均已完成。结果已经足以进入 Viewer：Viewer 应优先展示每轮正文可见性、重复读取、Working Context 是否使用、Memory transition、Final rejection，以及“已读但最终遗漏”的关键记录。当前不建议继续针对这些单次结果调 Agent 或重跑病例。

## Artifacts

- `runs/gastro_demo_multi_patient/execution_freeze.json`
- `runs/gastro_demo_multi_patient/experiment_manifest.json`
- `runs/gastro_demo_multi_patient/run_metrics.json`
- `runs/gastro_demo_multi_patient/GI-DEMO-001/` 至 `GI-DEMO-005/`
- `scripts/validate_gastro_multi_patient_encounters.py`
- `scripts/analyze_gastro_multi_patient_runs.py`
- `scripts/build_gastro_multi_patient_manifest.py`
