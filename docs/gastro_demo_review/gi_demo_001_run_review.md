# GI Demo 001 首次 Agent 运行审查

## 结论

`GI-DEMO-001` 与本地 V4.1 runtime 完全兼容，DeepSeek 真实运行也正常结束，没有数据库、工具、参数校验、API、预算或 artifact 写入故障。Agent 发现并读取了全部 18 条临床记录，正确重建了大部分病程、治疗和随访趋势。

但这次运行不能算临床任务完全成功：最终答案没有明确说出主要诊断“成人乳糜泻”，没有报告已经读取两次的 Marsh 3b 病理和关键组织学所见，反而把病理分级描述为需要回看全文的不确定项。这是上下文管理与最终证据使用失败，不是检索失败，也不是数据集不兼容。

原始 artifacts 足以支持一个研究用途的离线执行 Viewer，但目前不应把 Viewer 定位为临床可靠性证明或自动评分结果展示。

## 1. 冻结版本与配置

- Git commit：`5491c687075301bf936f7c8628a572a4c49bbd38`（工作树在运行前已有未提交的 V4.1 补丁和病例文件，本次保留，未回退到公开旧版）。
- Runtime：`phase1-v4-execution-protocol-patch`，即本地文档所称 V4.1。
- 包版本：`clinical_agent_explorer_phase1.__version__ = 0.2.0`。
- 模型适配器：`deepseek-chat-completions`；通过环境变量 `DEEPSEEK_API_KEY` 读取密钥，未记录密钥内容。
- 请求配置：`https://api.deepseek.com`、模型别名 `deepseek-chat`、thinking disabled、parallel tool calls enabled。
- Provider 的 9 次响应元数据均报告模型为 `deepseek-flash`。现有 artifact 不能判断这是 provider 的别名映射还是路由行为。
- 预算：30 steps、30 tool calls；实际在 step 9 结束。
- Context 配置：32,000 bytes，recent result limit 2，retrieval catalog limit 24，preview limit 160。
- 隔离实例：`work/gastro_demo/gi-demo-001-deepseek-first/`；运行前 memory 和 mutable orders 均为空。
- Run：`runs/gastro_demo/p1-run-e76579fe5063/`。

完整 SHA-256 清单保存在 `execution_freeze.json`。关键值为：runtime `7C3DFD...7DA6DE`，tools `A12E7F...7155E`，storage `CC8FFD...CCA37`，clinical records `91871F...7B887`，task `A0497A...74DCE0`。

## 2. 兼容性测试

真实 LLM 调用前完成了以下检查：

- `tests/`：16/16 通过。
- `tests_phase1/`：38/38 通过。
- `scripts/validate_gi_demo_001.py`：通过；1 名患者、18 条记录、0 条预置 mutable orders。
- 直接使用 `initialize_case_instance`、`MockDatabase` 和 `ToolRegistry` 对本病例做集成冒烟测试：患者可解析；18 条索引按 7/7/4 稳定分页；索引不含 `data` 全文；`get_record` 与种子 JSON 对应记录完全相同。
- 记录 ID 唯一，跨记录 request/procedure/report/pathology 引用一致。
- 初始 prompt 为 11,331 bytes；不含任何临床记录 ID、标题、preview、临床内容或 evaluator marker。
- 隔离实例初始 patient memory 和 orders 均为空。

分类：未发现数据错误、集成配置问题或可复现 runtime 基础设施缺陷，因此没有修改 runtime、病例或 reference。

## 3. 执行结果与用量

| 项目 | 结果 |
|---|---:|
| 最终状态 | `complete` / `final_response` |
| Model calls | 9 |
| Tool calls requested / executed / rejected | 29 / 29 / 0 |
| Tool 分布 | `get_patient` 2，`search_records` 1，`get_record` 25，`update_working_context` 1 |
| 发现记录 | 18/18 |
| 读取全文 | 18/18 unique；25 次读取，7 次重复 |
| 订单 | 0 requested，0 generated |
| Memory updates | 2 条 unresolved，均在完成后写入 |
| Tokens | prompt 62,619；completion 6,753；total 69,372 |
| Prompt cache | hit 16,896；miss 45,723 |
| 最大 prompt | 29,593 bytes（未超过 32,000） |

全部工具请求成功；没有参数拒绝、batch 中断、未执行调用、final revision 或 runtime error。

## 4. 主要决策轨迹

| Step | 主要动作 | 观察 |
|---|---|---|
| 1 | 读取患者并一次搜索全部索引 | 发现 18/18；索引只用于选取候选。 |
| 2 | 读取 rec-002/003/006/007 | 覆盖症状、缺铁性贫血、专科评估和显著阳性血清学。 |
| 3 | 保存上述 4 条 working facts；读取 rec-012/011/013 | 获得 Marsh 3b 病理、内镜报告和正式乳糜泻诊断。 |
| 4 | 读取 rec-014/015/016/017 | 获得营养缺乏、补充处方、饮食教育和六个月临床随访；没有把 step 3 的确诊事实写入 working context。 |
| 5 | 读取 rec-018/001/004/008 | 获得随访实验室、症状前 CBC、粪便筛查和铁剂处方。 |
| 6 | 读取 rec-009/010/005 | 补齐申请、已完成操作和转诊工作流；至此 18 条均已读取。 |
| 7 | 重读患者、rec-012/011/013 | 因早期正文已退出 recent window，重复取得核心诊断证据。 |
| 8 | 重读 rec-017/018/014/015 | 模型明确意识到正文可见性问题，但再次没有保存 rec-012/011/013；最终轮因此看不到这些核心正文。 |
| 9 | 生成 final | 正确报告多数趋势，但漏掉明确诊断和病理细节。 |

最早可观察失败在 step 4：Agent 刚取得 rec-012/011/013 的完整正文，却没有将核心确诊事实写入 bounded working context，直接开始下一批读取。step 8 重读这些记录后又重复同一选择。下游结果是 step 9 只直接看到 rec-017/018/014/015 的正文，而 rec-012/013 只剩 inspected 状态与索引摘要。

## 5. Agent 的实际最终评估

完整原文见 `runs/gastro_demo/p1-run-e76579fe5063/final.md`。它正确报告了：

- 五个月慢性非血性稀便、腹胀、疲劳和 3.6 kg 体重下降；
- 典型缺铁性小细胞性贫血及阴性的常见感染筛查；
- 显著阳性 tTG-IgA、EMA 阳性、总 IgA 正常；
- 胃镜从申请到操作、报告和病理的不同阶段；
- 叶酸和维生素 D 缺乏及铁、叶酸、D3 补充；
- 无麸质饮食后症状、体重、贫血、铁储备、叶酸、维生素 D 和 tTG-IgA 的改善；
- 共用烤面包机和含小麦酱油的无意交叉接触；
- tTG-IgA 仍阳性、维生素 D 仍略低、补充剂疗程待决定。

它没有在结论中明确写出“记录确诊成人乳糜泻”，也没有呈现 rec-012 的六块活检、绒毛萎缩、隐窝增生、上皮内淋巴细胞增加和 Marsh 3b。

## 6. 与独立参考的比较

### 正确且有证据支持

- rec-002/003：症状、体重下降及新发缺铁性小细胞性贫血。
- rec-004/006/007：常见感染和明显活动性肠道炎症证据不足；在含麸质饮食、总 IgA 正常的条件下特异性血清学显著阳性。
- rec-009 至 rec-012：申请、操作完成、内镜最终报告和病理最终结果是不同工作流节点。
- rec-014/015：叶酸 3.2、维生素 D 14，B12 正常，并有对应补充方案。
- rec-017/018：稀便消失、体重回升至 60.4 kg；Hb 12.7、MCV 86、铁蛋白 24、叶酸 8.5、维生素 D 29、tTG-IgA 19.8。
- 将持续阳性抗体与已识别的无意暴露解释为需要继续饮食评估和随访，符合 reference，且没有误诊为难治性乳糜泻。

### 重要遗漏

1. **核心诊断未明确陈述**：rec-013 已明确诊断乳糜泻，但 final 只写“正式诊断”，未命名诊断。
2. **核心病理被省略**：rec-012 明确记录 Marsh 3b 及典型组织学改变；final 反而称这些细节“需以 rec-012 全文为准”。这不是资料不足，而是已读证据没有进入最终表达。
3. **症状前基线趋势不完整**：final 提到 rec-001 是基线，却没有给出 Hb 13.1、MCV 88；趋势表从 2025-01 开始，漏掉 reference 强调的正常症状前基线。
4. **内镜实际所见过度压缩**：rec-011 的皱襞减少和轻微扇贝样改变没有写出，只列出“内镜所见”。
5. **未提轻度 ALT 38 及其随访不确定性**：这是次要遗漏，不影响主诊断，但 reference 将其列为尚未解决问题。
6. **没有明确指出 12 个月/年度随访尚无完成记录**：final 有一般随访不确定性，但没有清楚划定数据截止后的未发生事项。

### 错误或证据表达问题

- final 的“不确定性 1”把已经读取两次、且在 step 7 刚重新取得的病理细节写成需要回看全文的事项。最准确分类是“已检查记录的关键信息使用失败”，不是 hallucination、retrieval miss 或真正的不确定性。
- 最终 `evidence_refs` 包含全部 18 条临床记录和患者记录，虽然全部合法，但过于宽泛。正文中的简写引用提供了部分映射，仍不利于逐项验证；合法 citation 不等于每个关键结论都已充分呈现。
- `patient-GI-DEMO-001` 被列入最终 evidence，但正文没有使用出生年、性别或过敏信息；这是无害但多余的引用。
- “铁蛋白 24（正常下限）”更准确应写为“已回到参考范围但仍偏低/储备不充裕”；24 并不等于报告下限 15。属于轻微措辞问题。

没有发现把预约当完成、把操作记录当病理、宣称黏膜完全愈合、诊断难治性乳糜泻或指责患者故意不依从等 reference 明确禁止的错误。

## 7. Runtime 与上下文行为

- 所有 prompt 都低于 byte limit；每个已执行 batch 的完整结果都进入紧接的下一轮，因此没有 prompt-size truncation。
- recent window 只保留最新完整 batch。到 step 9，`omitted_prior_tool_result_count` 为 25；核心 rec-012/013 正文已不在 prompt。
- persistent retrieval catalog 成功保留了 18 条 discovered/inspected 状态，但 catalog 只有索引语义，不能替代全文。
- working context 仅在 step 3 更新一次，并永久停留在 rec-002/003/006/007；后来已解决的四个 unresolved questions 也没有被更新为 resolved。
- `full_record_bodies_omitted_from_prompt` 在 step 9 为空，因为该字段只记录 prompt-size trimming，不记录 recent-window 淘汰；与此同时 omitted count 已为 25。这使“哪些已读正文当前不可见”需要模型自行推断，是本次失败的重要 runtime/context 可观察性限制。
- 全部工具参数合法，batch 顺序、call ID、结果和状态变化均有 trace；没有基础设施故障。
- 模型有多轮英文叙述，step 8 的普通 `content` 字段还包含 `<analysis>` 标签文本，尽管 system 要求自然语言使用中文且 thinking disabled。这是模型输出遵循问题；artifact 正确保留了原文，Viewer 不应把它误标为 provider 的隐藏 reasoning。

## 8. 最小改进建议（本次不实施）

1. 先在更多病例复现，再考虑让 runtime 对 recent-window 淘汰也输出 record-level visibility ledger，例如 record ID、最后可见 step、当前正文是否在 prompt；不要加入病例特定提示或强制读取规则。
2. 评估 Agent 是否应在核心证据离开窗口前更新 working context，并让已解决 unresolved questions 显式转态。该行为应以通用回归测试验证，不能只为本病例调 prompt。
3. Viewer 应并列显示 discovered、inspected、当前 prompt 可见、working-context persisted 四种状态；否则“读过”容易被误认为“最终决策时看得见并正确使用”。
4. 增加 claim-to-record 人工核对视图，避免把合法 `evidence_ref` 当作医学正确性的代理指标。
5. 未来运行的原生 metadata 可加入 Git commit、文件 hashes、provider-reported model 和 artifact schema version；本次只用补充 freeze 文件记录，没有改写原始 metadata。

## 9. Viewer 适用性与建议

本次目录保存了 metadata、全部 prompt snapshots、归一化 model responses、原始工具输入/输出、call IDs、timestamps、batch/state events、最终 state、final、memory updates 和 token usage。执行顺序完整，29 个 requested calls 均可与 29 个 executed results 对齐；若有拒绝，runtime 也有相应 event 类型。因此，**适合继续开发研究用途的离线 Viewer**。

仍有三项限制：模型 HTTP 原始 envelope/headers 与延迟没有保存；requested/executed/rejected 需要跨 `model_calls.jsonl`、`tool_calls.jsonl` 和 `state_events.jsonl` 重建；原生 metadata 缺少代码/数据 hashes。Viewer 应明确这些边界，并对 raw `<analysis>` 文本做普通响应内容标记和默认折叠。

建议：可以进入 Viewer 原型阶段，但产品目标应是“忠实回放与人工审查”，不是“证明 Agent 临床可靠”。在更多独立患者完成前，不应基于这一个病例声称临床可靠性或总体任务效果。
