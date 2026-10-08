# Phase 1 Runtime 基本冻结记录

记录时间：2026-10-08（Asia/Hong_Kong）

最初的基本冻结点建立在公开 development case 和确定性基础设施测试完成之后、unseen challenge case 创建之前。

冻结时该目录不是 Git 仓库，因此使用 SHA-256 记录边界：

```text
src/clinical_agent_explorer/models.py       3F38761152D3619DD91B2FE9D27811F7C7B9938401DEF3D0D75A293ACF05BBCB
src/clinical_agent_explorer/storage.py      0452063EA6C34B54B353883CC2289110E289285852FA6B04D2E918B64974710A
src/clinical_agent_explorer/tools.py        B01B8DC72E43AA6CFDD4FB045F205798BE3544348D78E3CF9260902ACF5B77EB
src/clinical_agent_explorer/runtime.py      A71FCB77F8021E7E038A7EB268FB7B9446D6AED73E76318EA2E0AD20C0B03F68
src/clinical_agent_explorer/model_client.py D1442271D0363DDA7D58018B0A88C524D57A0DF84088A4C4FA9676806DE65B1F
src/clinical_agent_explorer/cli.py          26875C5334D4A8C8B6599103B6EB51669131A8B2F7673D87BE23B530109642A4
```

当时测试结果：15 个确定性基础设施测试通过。

Challenge fixture 加入后、尚未执行前，通用基础设施检查发现：意外工具异常虽然能停止运行，但没有作为原始工具结果保存。因此在没有使用 challenge 行为或 trace 的情况下修正了 runtime 并添加确定性测试。上表记录的是该次修正后的版本；修正前的 runtime hash 为 `19106AAFCECE1DD204B16289C6CE39C16A26D7327CEDA40385146121D2182990`。

该版本没有使用 challenge 运行结果、private gold、rubric 或临床答案 evaluator。若在检查 challenge 行为后修改任何已记录文件，受影响的后续运行必须描述为探索性运行。

## 中文演示版变更

在首次真实 Agent 运行前，按演示要求把 system prompt、tool description、任务说明和病例叙述统一为中文，并将默认 provider 配置改为 `DEEPSEEK_API_KEY`、`https://api.deepseek.com` 和 `deepseek-flash`。同时新增 `model_calls.jsonl`，保存模型可观察响应与 usage，但不保存隐藏思维过程。下面的新哈希和测试结果应作为中文演示运行的实际边界；challenge 临床内容没有用于调节 controller 或 system prompt。

```text
src/clinical_agent_explorer/models.py       0F0DAF4F95F5FE17EEA3F5E7DDCAB02C73610DA020972E73297E54FAA61089BE
src/clinical_agent_explorer/storage.py      47A2072A21EEE8400091859C1FE5EFCEE912CD583EBFED07727CE20AB757E125
src/clinical_agent_explorer/tools.py        280825F58BEEC6C0CDB826AF18792C5C35720E7DBCD7FF7CFDA82649847414A2
src/clinical_agent_explorer/runtime.py      5E5EC51F6B5E939DF46656B5CFD0EF82A301C46188F7D1509CC2A0290EAB8603
src/clinical_agent_explorer/model_client.py 4D884AA38A60E91AAB0D747002302760E8D09D7E71D98FE09DB1DFF124690170
src/clinical_agent_explorer/cli.py          9323D1F2DDBC8C20198D0B71504E4F65BC40E2D4215C11CE0CB5C1D16B72F518
```

中文演示版测试结果将在首次真实运行前后重新记录；真实 development run 已发现 DeepSeek 会在单次响应中返回多个独立工具调用，因此 runtime 改为按返回顺序逐个执行并分别记录，同时用同一总调用上限约束整个运行。该调整不包含病例专属顺序或临床规则。

## 首次 Challenge 运行使用的最终边界

在 development run 中还暴露了两个通用适配问题：瞬时模型网络错误需要对无副作用的模型请求进行有限重试；已由 `get_order` 独立读回验证的 action ID 应允许作为本次运行 provenance 引用。两项修正均在首次 challenge 运行前完成，并有确定性测试覆盖。首次 challenge 运行后没有再修改 runtime。

```text
src/clinical_agent_explorer/models.py       0F0DAF4F95F5FE17EEA3F5E7DDCAB02C73610DA020972E73297E54FAA61089BE
src/clinical_agent_explorer/storage.py      47A2072A21EEE8400091859C1FE5EFCEE912CD583EBFED07727CE20AB757E125
src/clinical_agent_explorer/tools.py        280825F58BEEC6C0CDB826AF18792C5C35720E7DBCD7FF7CFDA82649847414A2
src/clinical_agent_explorer/runtime.py      D54BB6B5333EF04D8DA587EB4024243731605168DAB079365E683DBE45C77A7E
src/clinical_agent_explorer/model_client.py 1617E89A3519C72A637A0CCB18C6222A8B6670C1C614FC65FFF211B248A09B47
src/clinical_agent_explorer/cli.py          9323D1F2DDBC8C20198D0B71504E4F65BC40E2D4215C11CE0CB5C1D16B72F518
```

最终基础设施测试结果：16 个测试全部通过。
