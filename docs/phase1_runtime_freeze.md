# Phase 1 runtime freeze

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
