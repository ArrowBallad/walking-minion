# Clinical Agent Explorer（临床 Agent 检查器）

这是一个小型本地演示系统，用于检查合成患者数据如何依次经过工具、提示上下文、运行状态、患者记忆和 trace，最终形成输出。

Phase 1 验证的是基础设施不变量，而不是预设的临床结论。`cases/development/` 下的公开案例用于调试数据链路，不是带标准答案的 benchmark。

## 运行

项目只使用 Python 标准库。默认调用 DeepSeek Chat Completions 接口，使用当前环境中的 API key：

```text
DEEPSEEK_API_KEY=...
```

可选覆盖项：

```text
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
```

在仓库根目录运行：

```text
python -m pip install -e .
clinical-agent-explorer --reset-instance
```

可变 mock 数据和患者记忆位于 `work/`。每次运行的完整模型响应、prompt snapshot、原始工具结果、状态事件、运行状态和最终输出位于 `runs/`。这两个目录均被 Git 忽略。

Challenge 案例运行示例：

```text
clinical-agent-explorer --case-dir cases/challenge/gi_challenge_001 --case-id gi_challenge_001 --patient-id GI-CH-001 --instance gi-challenge-001-demo --reset-instance
```

## 测试

```text
python -m unittest discover -s tests -v
```

测试只使用 scripted client 验证确定性的基础设施行为，不断言正确的临床工具顺序、结论、订单或记忆内容。

所有面向模型和演示者的说明、任务与病例叙述均使用中文；字段名和状态机枚举保留稳定的机器可读英文标识。
