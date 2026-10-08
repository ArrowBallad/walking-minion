from __future__ import annotations

import argparse
import os
from pathlib import Path

from .model_client import OpenAICompatibleClient
from .runtime import ClinicalAgentRuntime
from .storage import MemoryStore, MockDatabase, initialize_case_instance
from .tools import ToolRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行合成临床 Agent 演示系统")
    parser.add_argument("--case-dir", type=Path, default=Path("cases/development/gi_dev_001"))
    parser.add_argument("--case-id", default="gi_dev_001")
    parser.add_argument("--patient-id", default="GI-001")
    parser.add_argument("--instance", default="gi_dev_001-local")
    parser.add_argument("--work-root", type=Path, default=Path("work"))
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--reset-instance", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    task_path = args.case_dir / "task.md"
    if not task_path.is_file():
        raise SystemExit(f"找不到任务文件：{task_path}")
    paths = initialize_case_instance(
        args.case_dir, args.work_root, args.instance, reset=args.reset_instance
    )
    client = OpenAICompatibleClient()
    runtime = ClinicalAgentRuntime(
        client=client,
        tools=ToolRegistry(MockDatabase(paths.database)),
        memory=MemoryStore(paths.memory),
        runs_root=args.runs_root,
        max_steps=args.max_steps,
    )
    state = runtime.run(
        patient_id=args.patient_id,
        task=task_path.read_text(encoding="utf-8").strip(),
        case_id=args.case_id,
        model_metadata={
            "adapter": "deepseek-openai-compatible-chat-completions",
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
            "model": os.environ.get("LLM_MODEL", "deepseek-flash"),
            "thinking": "disabled",
        },
    )
    print(f"运行 ID={state.run_id} 状态={state.status} 原因={state.stop_reason}")


if __name__ == "__main__":
    main()
