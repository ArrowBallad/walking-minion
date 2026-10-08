# Clinical Agent Explorer（临床 Agent 检查器）

这是一个小型本地演示系统，用于检查合成患者数据如何依次经过工具、提示上下文、运行状态、患者记忆和 trace，最终形成输出。

原有 `clinical_agent_explorer` 包作为 Phase 0 保留，用于验证基础数据链路。`clinical_agent_explorer_phase1` 增加索引检索、按 ID 读取全文和有限 patient working context，用于观察 Agent 在纵向记录中的信息获取行为。两者都不预设临床结论。

## 运行

项目只使用 Python 标准库。默认调用 DeepSeek Chat Completions 接口，使用当前环境中的 API key：

```text
DEEPSEEK_API_KEY=...
```

可选覆盖项：

```text
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

在仓库根目录运行：

```text
python -m pip install -e .
clinical-agent-explorer --reset-instance

# Phase 1 公开 development case
clinical-agent-explorer-phase1 --reset-instance --max-steps 30
```

可变 mock 数据和患者记忆位于被 Git 忽略的 `work/`。每次运行的完整模型响应、prompt snapshot、原始工具结果、状态事件、运行状态和最终输出位于 `runs/`；需要作为演示证据的选定 trace 可以保留在仓库中。

Phase 1 challenge 案例运行示例：

```text
clinical-agent-explorer-phase1 --case-dir cases/phase1_challenge/dizziness_longitudinal_001 --case-id dizziness_longitudinal_001 --patient-id DZ-LONG-001 --instance dizziness-longitudinal-001-challenge --reset-instance --max-steps 30
```

## 测试

```text
python -m unittest discover -s tests -v
python -m unittest discover -s tests_phase1 -v
```

测试只使用 scripted client 验证确定性的基础设施行为，不断言正确的临床工具顺序、结论、订单或记忆内容。

所有面向模型和演示者的说明、任务与病例叙述均使用中文；字段名和状态机枚举保留稳定的机器可读英文标识。
