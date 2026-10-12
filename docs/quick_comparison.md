# LLM、简单 RAG 与 V4 Agent 的快速对比

## 结论先行

在这 3 个案例、每种方案各运行 1 次的探索性比较中，**V4 Agent 的额外复杂度没有表现出足以抵消其可靠性和成本问题的任务完成优势**。

- Full-context LLM 完成了 3/3 个任务，三个回答的核心事实和工作流状态均与原始记录一致。
- BM25 Top-8 + LLM 完成了 3/3 个任务；咳嗽和短病例回答良好，但头晕病例因检索遗漏而缺少体位生命征、电解质变化、当前心电图和同日用药状态冲突，并错误地概括为“未发现记录间直接矛盾”。
- V4 只完成了 1/3 个任务。它在咳嗽病例中选择了少量关键记录并给出完整答案；头晕病例读取了多条关键证据却最终达到 25 步上限，没有可用 final；只有 9 条记录的短病例则因反复返回批量工具调用而被 runtime 停止。
- V4 合计使用 352,152 tokens、56 次模型调用和 45 次实际工具调用；Full-context 为 15,915 tokens、3 次模型调用，RAG 为 6,044 tokens、3 次模型调用。V4 的 token 总量约为 Full-context 的 22.1 倍、RAG 的 58.3 倍。

因此，本结果支持保留 V4 作为可审计的 Agent/runtime 实验对象，但**不支持以任务效果或成本为理由宣称它优于这两个简单 baseline**。

## 实现与实验设置

运行日期为 2026-10-10。三个方案均请求同一 `deepseek-chat` endpoint、关闭 thinking、使用相同任务和患者数据；API 在全部响应中自报实际模型为 `deepseek-flash`。没有保存 API key。

- **Full-context**：把 patient record 和该患者全部 clinical records 的全文放入一次 prompt，要求输出 `final_text` 和 `evidence_refs`。
- **简单 RAG**：以原始任务文本为唯一 query；固定分词为小写拉丁/数字 token 加中文字符 unigram 和相邻 bigram；在 `title + preview + keywords + data 全文` 上使用 BM25（`k1=1.5`、`b=0.75`），固定 Top-8；把命中全文放入一次 prompt。没有 query rewriting、reranking 或第二轮检索。
- **V4 Agent**：直接使用未修改的 `phase1-v4-retrieval-awareness` runtime、tools 和 system prompt；`max_steps=25`、`max_tool_calls=40`，其他 context/catalog 参数使用默认值。每个案例都从 case seed 创建新的 mock DB 和空 memory。

比较脚本为 `scripts/quick_comparison.py`。新增短病例位于 `cases/quick_comparison/workflow_status_001/`，共 9 条记录，明确包含：胸片申请→执行→最终报告；尿培养申请→采集→标本拒收且无结果；超声心动图仅申请、待排期。

全部任务均为只读。三个 V4 mutable DB 的 `orders.jsonl` 均为空，trace 中也没有 `create_order`。本次只运行固定的 9 个组合，没有因结果不佳而改 prompt 或补跑。

原始 artifacts 位于 `runs/quick_comparison/quick-comparison-v1/`：baseline 保存完整 request、API response、normalized output、final、usage 和 RAG 排名；V4 保存 runtime 原生 prompt snapshots、model/tool calls、state events、run state 和 final。汇总数据见同目录的 `results.json`。

现有咳嗽和头晕病例已经参与开发或回归；新增短病例也按本次目标显式设计。因此这些结果只能称为 **exploratory/regression comparison**，不是 unseen benchmark。

## 真实运行数据

`records` 对 Full-context/RAG 表示提供给模型的全文数，对 V4 表示唯一 `get_record` 全文数。Token 列为 `prompt / completion / total`；V4 prompt token 包含 provider 报告的 cache hit 和 miss。

| 案例 | 方案 | 运行结果 | records | 模型调用 | 工具调用 | tokens（P/C/T） |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| cough | Full-context | complete | 35/35 | 1 | 0 | 4,947 / 380 / 5,327 |
| cough | BM25 Top-8 | complete | 8/35 | 1 | 0 | 1,459 / 478 / 1,937 |
| cough | V4 | complete，step 20 final | 8/35（另发现 20） | 20 | 16 | 117,989 / 7,141 / 125,130 |
| dizziness | Full-context | complete | 40/40 | 1 | 0 | 6,401 / 2,126 / 8,527 |
| dizziness | BM25 Top-8 | complete，但关键遗漏 | 8/40 | 1 | 0 | 1,567 / 662 / 2,229 |
| dizziness | V4 | stopped：`iteration_limit` | 10/40（另发现 20） | 25 | 22 | 161,372 / 13,850 / 175,222 |
| workflow | Full-context | complete | 9/9 | 1 | 0 | 1,613 / 448 / 2,061 |
| workflow | BM25 Top-8 | complete | 8/9 | 1 | 0 | 1,471 / 407 / 1,878 |
| workflow | V4 | error：`invalid_model_response` | 5/9（发现 9） | 11 | 7 | 48,036 / 3,764 / 51,800 |
| **合计** | **Full-context** | **3/3 complete** | **84** | **3** | **0** | **12,961 / 2,954 / 15,915** |
| **合计** | **BM25 Top-8** | **3/3 complete** | **24** | **3** | **0** | **4,497 / 1,547 / 6,044** |
| **合计** | **V4** | **1/3 complete** | **23** | **56** | **45** | **327,397 / 24,755 / 352,152** |

## 病例级人工核对

### 1. `cough_stopping_001`

原始记录的核心链条是：2026-01-05 开始依那普利；约三周后出现持续干咳；病毒检测阴性、胸片最终报告未见急性异常；2026-02-01 停依那普利并改氯沙坦；2026-02-15 干咳基本消失，仅清晨偶尔轻咳。

**Full-context** 完整呈现了药物、症状、检查和随访时间线，并把药物因果关系保留为有支持但未正式验证的判断。没有发现实质性事实错误。它也读取了很多无关记录，但该病例全部输入仍只有 4,947 prompt tokens。

**RAG** 的 Top-8 命中了症状、停药、随访、病毒结果和胸片报告，因而核心回答正确，也明确写出了“基本消失，仅清晨偶尔轻咳”。它没有检索到依那普利起始处方、氯沙坦起始处方和换药后代谢面板，所以没有给出依那普利开始日期/剂量，药物时间线略弱于另外两种方案；但没有为缺失内容虚构具体值。

**V4** 本次与此前 V4 replay 不同：它读取了 `cs-rec-031`，并在 final 中实际写出了“干咳已基本消失，仅清晨偶尔轻咳”，没有再次出现“已读关键随访但 final 只说有变化”的遗漏。它还完整读取了用药、病毒、影像报告和复查实验室，共 8 条全文。

V4 同时有两类语义边界问题：

- 它没有读取 `cs-rec-027` 全文，却在 final 中根据 catalog 的标题、状态和 preview 描述该操作记录“仅记录检查完成”。该 ID 没进入 final evidence refs，所以引用校验通过了，但自然语言仍使用了 preview-only 信息。这正说明合法 evidence refs 不等于整篇回答语义上都合法。
- 它把一次正常代谢面板进一步解释为“氯沙坦替代在短期内安全性可接受”，并把依那普利相关干咳称为“最可能解释”。后者有时间线支持，前者则超出任务需要，且仅凭一条合成实验室记录作安全性判断偏强。Full-context 的因果措辞更克制。

总体上，V4 在该病例的答案质量不差，并展示了选择性全文读取；但 RAG 同样只提供了 8 条记录，Full-context 也已正确回答，而 V4 分别用了约 64.6 倍于 RAG、23.5 倍于 Full-context 的 tokens。

### 2. `dizziness_longitudinal_001`

原始记录的核心包括：站立时眼前发黑和近乎跌倒；卧位到站立收缩压下降并复现症状；钠 128、钾 3.2；同一时间存在药房“在用”与临床“停用氢氯噻嗪”记录；停药后头晕减少且复查钠/钾恢复；头颅 CT 无急性发现；动态监测只有申请/佩戴/进行中状态而无最终报告；复查超声仅申请待排期。

**Full-context** 覆盖了上述全部核心内容，正确区分了动态监测申请、设备佩戴、待归还和最终报告缺失，也区分了 CT 操作与实际报告。它明确呈现了同日药物状态冲突，并用之后的停药随访解释患者实际执行情况。回答偏长，包含不少无关历史记录，但未见影响核心结论的事实错误。

**RAG** 命中了当前门诊、临床停药、停药随访、CT 操作/报告、超声申请和动态监测进行中状态，因此能回答部分纵向经过与“检查尚无结果”。但 Top-8 没有命中：

- `dz-rec-015` 卧立位生命体征；
- `dz-rec-017` 初始低钠/低钾和 `dz-rec-025` 复查纠正；
- `dz-rec-026` 当前心电图；
- `dz-rec-019` 同日药房“在用”记录；
- `dz-rec-021` 监测申请和 `dz-rec-022` 设备佩戴。

因此 RAG 漏掉了本病例最强的客观时间链和明确冲突。它在 final 中写“未发现记录间直接矛盾”，对其已检索子集成立，但对完整病例不成立，是检索遗漏导致的错误总体概括。

**V4** 实际读取了 10 条唯一全文，其中包括卧立位生命征、初始与复查电解质、停药随访、CT 报告、两侧用药记录、当前心电图和设备佩戴。也就是说，它比固定 Top-8 RAG 找到了更多决定性的分散证据，这是本轮对 V4 检索能力最有利的观察。

但它没有交付答案：step 21 已生成详细 final 草稿，却因 memory update 引用了未读取全文的 `dz-rec-021` 而被 provenance validator 拒绝；随后又读取心电图和设备佩戴、更新 context、继续搜索，在 step 25 达到 iteration limit。此前还有两次 context update 因引用未读的冲突记录失败、两次批量工具调用被拒绝，以及一次重复读取 `dz-rec-015`。被拒绝的草稿还把未读取的 `dz-rec-021/023/027/028` preview 当成工作流事实，并给出较强的药物因果诊断；这些没有成为 accepted final，但解释了 runtime 拒绝与继续检索的成本。

这是“已经读到关键证据但没有在最终答案中产生价值”的最直接案例：175,222 tokens、25 次模型调用和 22 次工具调用后，用户只得到 `iteration_limit`。

### 3. `workflow_status_001`

本例只有 9 条记录，核心事实没有临床推断难度：胸片完整经历 ordered→performed→resulted，最终报告为未见急性心肺异常；尿培养虽已申请和采集，但标本被拒收、检验取消，没有可解释结果；超声心动图仅申请、待排期，未执行也无报告。

**Full-context** 和 **RAG** 都正确回答了三项检查的每个阶段，没有把申请、采集或操作记录升级成结果。RAG 的 Top-8 恰好排除了唯一无关的 CBC；Full-context 在 `evidence_refs` 中多列了无关的 CBC，但正文没有使用它支持错误结论。

**V4** 首次搜索即发现全部 9 条记录，context 大小显然不是瓶颈。模型随后四次返回批量 `get_record` 请求：前三次被原子调用契约拒绝后仍再次批量请求，step 11 超过三次修正机会，以 `invalid_model_response` 停止。停止前它读取了门诊、胸片申请/操作/报告和尿培养申请共 5 条，但尚未读取尿培养拒收通知和超声申请，也没有 final。

这个失败主要来自模型与 V4 “每轮一个工具”的交互不稳定，而不是检索难度、记录长度或证据不足。它也说明在短记录上，Agent loop 的固定开销和失败面可以远大于任务本身。

## V4 的实际收益与额外成本

本轮能观察到的收益有两点：

1. V4 的 trace 能精确区分 discovered 与 inspected，并保留每次搜索、全文读取、context 更新、拒绝和停止原因；审计性显著强于两个一次性 baseline。
2. 在头晕病例，迭代检索确实找到固定 Top-8 漏掉的卧立位生命征、电解质变化、复查和用药冲突两侧，显示了多步检索在分散证据上的潜力。

但这些收益没有转化为更高的实际任务完成率。V4 的主要代价是：

- 只有 1/3 final，另外两例分别因 iteration limit 和重复批量调用失败；
- prompt 中反复携带 system/tool schema、working context 和 catalog，导致 327,397 prompt tokens；
- 频繁 context snapshot、无效批量响应、recoverable error、final revision 和重复读取增加了模型/工具调用；
- provenance validator 只能检查引用资格，不能阻止 accepted final 在正文中使用 preview-only 信息或作过强语义推断。

在本轮可放入 1,613–6,401 prompt tokens 的病例规模下，Full-context 没有受到 context 限制；简单 RAG 在两个病例已足够，在一个病例出现可解释的召回失败。V4 的理论优势更可能出现在无法放入 full context、且单次 Top-k 明显不足的更长记录上，但这次实验没有提供该结论的证据。

## 不能从本实验得出的结论

- 不能声称任一方案具有一般临床正确性、安全性或真实患者适用性；这里只核对合成记录中的显式事实与有限推断。
- 不能称这些结果为 unseen 或正式 benchmark，也不能从 3 个病例、每格 1 次运行估计稳定胜率。
- 不能把 provider 本次自报的 `deepseek-flash` 行为外推到其他模型、版本、温度或日期。
- 不能据此断言 RAG 一定优于 Agent；固定 Top-8 已在头晕病例发生关键召回遗漏。
- 不能断言 V4 在更长、超出模型 context 的病例上仍无价值；本次 Full-context 最大 prompt 仅 6,401 tokens。
- 不能用 citation 合法、final 生成或 token 更少单独等同于正确；本分析逐条核对了原始记录、实际回答和 V4 trace。

## 最终回答

**在当前测试规模下，没有。** V4 展示了更细的审计轨迹和在头晕病例中跨记录找到分散关键证据的能力，但它未把这一检索优势稳定转化为最终答案：完成率低于两个 baseline，且成本高出一个到两个数量级。若项目目标是研究可审计的 Agent 行为，V4 仍有保留价值；若目标是用最小系统可靠完成当前规模的纵向合成病历总结，当前证据更支持 Full-context LLM，简单 RAG 可作为更便宜但有召回风险的方案。
