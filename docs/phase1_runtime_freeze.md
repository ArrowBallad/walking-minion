# Phase 1 runtime versions

## V1：`phase1-v1-frozen-challenge`

冻结时间：2026-10-08（Asia/Hong_Kong）

此冻结发生在 unseen challenge case 创建之前。冻结依据是公开 development case 的 plumbing 运行与 17 项确定性 Phase 1 测试；不依据预设临床答案。

## 冻结边界

Challenge 运行使用 `src/clinical_agent_explorer_phase1/` 下列文件。SHA-256：

```text
471AB690223807ED2CA2C4E8D6C3EFCF5CB57978B39DC42675EEBBA9C7D9B83F  __init__.py
EB11266F97F7E1BDA306A7E878E343B8F91503DD3DE41478032B434217AEA3FC  cli.py
E6192A4DAEEF2B50285845360079849037D0D090302A74E18D19DEBA16AD8643  model_client.py
536F5D69906A40433E7289CBAA9495F3E58D417CE321B83B2F24A3E72CC8BF77  models.py
06E7A95C3A5321F99DE2877545B3E53B462C9EA62689A7B4F339A3E7623A92F9  runtime.py
CC8FFD0073DAEBCD3B5FAA8E544C96DC639326346B98BE1BEACAFC529D8CCA37  storage.py
A12E7F18858F068943BDFA166BABEFFCCEBD4D49C084CDE08F4A2DC6D427155E  tools.py
```

冻结时测试结果：

- Phase 1：17/17 通过。
- Phase 0 回归：16/16 通过。

## 冻结前 development observations

- `p1-run-8b5ee8c2e99a` 暴露了批量全文读取与只保留两个最近结果之间的机械循环。
- `p1-run-0b9a56437f7d` 证明 final evidence 校验会拒绝未读全文记录被写入 memory evidence。
- `p1-run-05a4e429be67` 证明 provider 会忽略 `parallel_tool_calls=false`，因而需要 runtime 原子调用契约。
- `p1-run-811863434356` 在原子调用下形成了实际 working context，但在 20 轮上限停止。
- `p1-run-4d845f2ec884` 因 conflict 只有一条已读证据而被参数校验干净停止。

上述运行用于修复或验证通用 plumbing。Challenge 创建及运行后，不再修改这些冻结文件；如有修改，后续结果只能标为探索性运行。

## V2：`phase1-v2-recoverable-tool-errors`

记录时间：2026-10-08（Asia/Hong_Kong）

V2 是首次 unseen challenge `p1-run-8e583d9e9e0c` 之后产生的新 Phase 1 runtime version。该 run 暴露出一个通用问题：模型可根据公开错误修正的工具参数/证据错误仍会立即终止整个运行。

V2 只增加 bounded recoverable tool-error feedback：

- 不修改任何工具校验规则。
- 错误工具调用的原始 input/output、code 与 message 继续完整写入 artifacts。
- Allowlist 内的错误在确认 RunState 未改变后进入下一轮最近 tool result。
- Runtime 不修改参数、不自动检索、不替模型选择下一步。
- 连续错误上限为 2；第三次以 `recoverable_tool_error_limit` 停止。
- 任一次成功工具调用重置连续错误计数。
- `patient_mismatch`、`unknown_tool`、unexpected/internal failure、错误时 state mutation 和未分类错误仍 hard stop。

Recoverable allowlist 为：`invalid_arguments`、`uninspected_context_ref`、`unknown_context_ref`、`context_limit`、`uninspected_evidence_refs`。分类依据详见 `docs/phase1_plan.md`。

### V2 SHA-256

```text
471AB690223807ED2CA2C4E8D6C3EFCF5CB57978B39DC42675EEBBA9C7D9B83F  __init__.py
EB11266F97F7E1BDA306A7E878E343B8F91503DD3DE41478032B434217AEA3FC  cli.py
E6192A4DAEEF2B50285845360079849037D0D090302A74E18D19DEBA16AD8643  model_client.py
536F5D69906A40433E7289CBAA9495F3E58D417CE321B83B2F24A3E72CC8BF77  models.py
CE70F2F023FD84DBA60290E7A4A8EBD452B57BF7B46735FC4E09F22B0A72F188  runtime.py
CC8FFD0073DAEBCD3B5FAA8E544C96DC639326346B98BE1BEACAFC529D8CCA37  storage.py
A12E7F18858F068943BDFA166BABEFFCCEBD4D49C084CDE08F4A2DC6D427155E  tools.py
```

记录时测试结果：

- Phase 1：21/21 通过。
- Phase 0 回归：16/16 通过。

新增 scripted coverage：一次可恢复错误后成功纠正、连续第三次可恢复错误停止、`patient_mismatch` 立即停止、unexpected tool failure 立即停止，以及错误调用不污染 RunState。

对 `dizziness_longitudinal_001` 的 V2 运行属于 development/regression replay，不是新的 unseen challenge，也不会据该病例增加专属规则。

## V3：`phase1-v3-stopping-contract`

记录时间：2026-10-08（Asia/Hong_Kong）

V3 针对 V2 regression replay `p1-run-f79dd2cbe3f0` 暴露的通用停止问题：Agent 已能分页、读取全文和更新 working context，但把未解决问题、未读索引和未翻完的信息继续视为补查义务，在 30 轮内没有主动 final。

V3 只修改通用 prompt/context guidance：

- 不需要解决所有 unresolved questions 才能结束，它们可以保留到 final。
- 已读全文证据足以回答 task 核心问题时，应优先 final。
- 只有可能实质改变当前 assessment 的缺失信息才值得继续查询。
- 未读 index 不是读取义务。
- `has_more=true` 不是翻完分页的义务，分页仍由当前 information need 驱动。
- 是否足够完全由 Agent 判断；runtime 没有新增 clinical sufficiency rule、state machine、planner、completion tracker、工具或自动停止逻辑。

### V3 SHA-256

```text
471AB690223807ED2CA2C4E8D6C3EFCF5CB57978B39DC42675EEBBA9C7D9B83F  __init__.py
EB11266F97F7E1BDA306A7E878E343B8F91503DD3DE41478032B434217AEA3FC  cli.py
E6192A4DAEEF2B50285845360079849037D0D090302A74E18D19DEBA16AD8643  model_client.py
536F5D69906A40433E7289CBAA9495F3E58D417CE321B83B2F24A3E72CC8BF77  models.py
99E2553F79A6D6107F8F1318FB5FE7384958F8AC6CF4A7355563ABA4BB3C29B6  runtime.py
CC8FFD0073DAEBCD3B5FAA8E544C96DC639326346B98BE1BEACAFC529D8CCA37  storage.py
A12E7F18858F068943BDFA166BABEFFCCEBD4D49C084CDE08F4A2DC6D427155E  tools.py
```

记录时测试结果：

- Phase 1：22/22 通过。
- Phase 0 回归：16/16 通过。

新增测试只验证 stopping contract 的通用文字、结构和 decision ownership 存在，不断言临床答案、检索数量或停止轮次。

### V3 observations

- Dizziness regression replay `p1-run-de4bffa55f0b`：step 14 主动 final，只读取 3/40 条全文，保留 3 个 unresolved questions。
- Freeze 后创建的 unseen stopping case `cough_stopping_001`：35 条记录；run `p1-run-ef4785479f90` 在 step 16 主动 final，只读取 5 条全文，停止时仍有 30 条未读，保留 2 个 unresolved questions。
- 两个 final 的 evidence refs 均只包含 inspected records 或 patient record。
- 上述运行后未修改 V3 runtime 文件；哈希保持不变。

## V4：`phase1-v4-retrieval-awareness`

记录时间：2026-10-08（Asia/Hong_Kong）

V4 针对 V3 cough run 暴露的通用检索认知问题：`search_records` 返回过的索引结果离开有限的 recent tool results 后，runtime 只持续保留 record ID，Agent 会失去候选记录的 title/type/preview 语义。

V4 只增加 persistent compact retrieval catalog：

- catalog 只合并成功 `search_records` 实际返回的 index metadata，以 `record_id` 去重并保留首次发现顺序。
- `discovered_records` 与 `discovered_record_ids` 保持一致；重复搜索更新原位置，不产生重复项。
- prompt 默认最多携带 24 条候选及 omitted count，每条 preview 最多 160 字符；没有 relevance scoring、ranking、embedding、RAG 或自动 `get_record`。
- `get_record` 仍是把记录加入 `inspected_record_ids` 的唯一全文路径；catalog 仅根据该集合显示 `inspected=true/false`。
- V3 stopping contract 保留，并增加 final 前的通用 catalog 检查；Agent 自行判断候选是否影响 task completion。
- Index preview 仍不能支持 known fact、conflict、write evidence 或 final evidence。

### V4 SHA-256

```text
471AB690223807ED2CA2C4E8D6C3EFCF5CB57978B39DC42675EEBBA9C7D9B83F  __init__.py
EB11266F97F7E1BDA306A7E878E343B8F91503DD3DE41478032B434217AEA3FC  cli.py
E6192A4DAEEF2B50285845360079849037D0D090302A74E18D19DEBA16AD8643  model_client.py
D6B71BA4FFD7A49CFD5341E2471A608F8A908D0F99919A84B640B635C4A934F6  models.py
08B92EDDB4D4F2BFC8E82FE5C375B28CFA46AAA79A86C282A248685E99465972  runtime.py
CC8FFD0073DAEBCD3B5FAA8E544C96DC639326346B98BE1BEACAFC529D8CCA37  storage.py
A12E7F18858F068943BDFA166BABEFFCCEBD4D49C084CDE08F4A2DC6D427155E  tools.py
```

冻结时测试结果：

- Phase 1：29/29 通过。
- Phase 0 回归：16/16 通过。

新增 scripted coverage 验证 catalog 在 raw search result 离开 recent results 后仍存在、重复搜索去重、全文读取后 `inspected=true`、catalog 有界且记录 omitted count、首轮不泄露未搜索 metadata，以及 preview-only 记录不能成为 final/fact/conflict evidence。测试不指定任何病例记录必须被读取。

冻结后对 `cough_stopping_001` 的再次运行属于 development/regression replay，不是 unseen challenge。

### V4 replay observation

- Cough regression replay：`p1-run-8b4b4e45accc`，step 15 主动 final，读取 7/35 条全文。
- `cs-rec-028` 在 search 原始结果退出 recent results 后仍持续出现在 catalog，并由 Agent 在 step 12 主动读取。
- 只有一次搜索，无重复全文读取；`has_more=true` 没有触发无需求分页。
- Final evidence refs 全部属于 inspected records；未读的 procedure index 没有被升级为正式事实证据。
- 详细人工审查见 `docs/phase1_v4_retrieval_review.md`；replay 后未修改 V4 runtime 文件。
