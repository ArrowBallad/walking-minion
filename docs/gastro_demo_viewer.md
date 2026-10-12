# Multi-Patient Agent Execution Viewer

`trace_viewer.html` 是一个自包含、离线使用的技术审查页面。它从真实病例文件、15 次统一实验运行 artifacts、GI-DEMO-001 历史运行以及独立人工注释构建，不连接 LLM、不需要 Web 后端，也不执行医学推理。

## 构建与打开

在仓库根目录运行：

```powershell
& 'C:\Users\Crystarium\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\build_trace_viewer.py
```

随后可直接双击 `trace_viewer.html`。页面没有外部字体、脚本或样式依赖，也不发起网络请求。若浏览器限制本地文件，可在仓库根目录运行任意静态文件服务器后访问该文件；服务器不是 Viewer 的组成部分。

可选参数：

```text
--manifest     选定实验的 experiment_manifest.json
--annotations  独立人工注释 JSON
--template     HTML 模板
--output       生成的单文件 HTML
```

## 数据来源

- 病例与时间线：`cases/gastro_demo/gi_demo_001` 至 `gi_demo_005` 中的 `patients.jsonl`、`clinical_records.jsonl`、`record_metadata.json` 和 `encounters.json`。
- 当前演示轨迹：`runs/gastro_demo_multi_patient/experiment_manifest.json` 指向的 15 次运行。
- 运行汇总：`runs/gastro_demo_multi_patient/run_metrics.json`。
- 当前实验 Memory ledger：`work/gastro_demo_multi_patient/*/memory/patient_memory.jsonl`；每个 run 只显示它实际载入和写入的条目。
- 历史运行：构建脚本自动发现 `runs/gastro_demo*` 下带 Encounter metadata 的兼容 artifacts，并明确标记为历史 checkpoint。
- 人工说明：`viewer/gastro_demo_annotations.json`。说明与运行数据分离，且构建时会校验 run、step、call 和 record 引用。

Evaluator-only reference 不进入 Viewer payload；API key 或环境变量值也不会写入页面。

## 页面使用

首页三个入口分别定位到 GI-DEMO-002 完整成功路径、GI-DEMO-005 工作流与状态管理、GI-DEMO-004 E03 已读信息丢失。下方汇总表保留全部 15 次运行，包括未完成的 GI-DEMO-003 E01。

在 Explorer 中依次选择 Patient、Encounter、Run 和 Agent step。历史 run 与当前统一实验不会合并。五个页签分别展示：

- 原始执行顺序、模型输出、工具请求/执行/拒绝、状态事件和 Prompt Snapshot；
- 全部临床记录在所选 step 的访问与可见性状态，以及仅供人工审查的原始全文；
- Working Context 的未调用/成功/拒绝状态与 Patient Memory 的来源、写入和 transition；
- 原始 Final、结构化 evidence refs 与真实 Memory 更新；
- 未改写的原始 artifacts。

## 已知限制

- Viewer 不推测或显示隐藏思维；普通模型文本只标为“保存的模型响应”。
- Viewer 中能打开记录全文，不代表该正文在所选 Prompt 中可见。可见性来自保存的 `record_visibility`。
- Patient Memory 是历史 Agent 工作结果，不是数据库事实。
- 结构化 evidence refs 通过协议校验，不代表 Final 中每个自然语言结论都已经过医学或语义验证。
- 历史运行只使用其自身保存的 Memory snapshot；不同 runtime 版本和 checkpoint 不会被拼成一条虚假的连续实验。
