from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .model_client import DeepSeekClient
from .runtime import ClinicalAgentRuntime
from .storage import MemoryStore, MockDatabase, initialize_case_instance
from .tools import ToolRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 Phase 1 纵向临床 Agent 演示")
    parser.add_argument(
        "--case-dir", type=Path, default=Path("cases/phase1_development/gi_longitudinal_001")
    )
    parser.add_argument("--case-id", default="gi_longitudinal_001")
    parser.add_argument("--patient-id", default="GI-LONG-001")
    parser.add_argument("--instance", default="gi-longitudinal-001-local")
    parser.add_argument("--work-root", type=Path, default=Path("work/phase1"))
    parser.add_argument("--runs-root", type=Path, default=Path("runs/phase1"))
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-tool-calls", type=int, default=30)
    parser.add_argument("--encounters-file", type=Path)
    parser.add_argument("--encounter-id")
    parser.add_argument("--as-of")
    parser.add_argument("--reset-instance", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if bool(args.encounters_file) != bool(args.encounter_id):
        raise SystemExit("--encounters-file 与 --encounter-id 必须同时提供")
    encounter_id = args.encounter_id
    as_of = args.as_of
    patient_id = args.patient_id
    if args.encounters_file:
        payload = json.loads(args.encounters_file.read_text(encoding="utf-8"))
        encounters = payload.get("encounters") if isinstance(payload, dict) else payload
        if not isinstance(encounters, list):
            raise SystemExit("encounters 配置必须是列表或包含 encounters 列表的对象")
        encounter = next(
            (
                item
                for item in encounters
                if isinstance(item, dict) and item.get("encounter_id") == encounter_id
            ),
            None,
        )
        if encounter is None:
            raise SystemExit(f"找不到 encounter：{encounter_id}")
        patient_id = str(encounter["patient_id"])
        as_of = str(encounter["as_of"])
        task = str(encounter["task"]).strip()
    else:
        task_path = args.case_dir / "task.md"
        if not task_path.is_file():
            raise SystemExit(f"找不到任务文件：{task_path}")
        task = task_path.read_text(encoding="utf-8").strip()
    paths = initialize_case_instance(
        args.case_dir, args.work_root, args.instance, reset=args.reset_instance
    )
    runtime = ClinicalAgentRuntime(
        client=DeepSeekClient(),
        tools=ToolRegistry(MockDatabase(paths.database, as_of=as_of)),
        memory=MemoryStore(paths.memory),
        runs_root=args.runs_root,
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
    )
    state = runtime.run(
        patient_id=patient_id,
        task=task,
        case_id=args.case_id,
        model_metadata={
            "adapter": "deepseek-chat-completions",
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
            "model": os.environ.get("LLM_MODEL", "deepseek-chat"),
            "thinking": "disabled",
            "parallel_tool_calls": True,
            "encounter_id": encounter_id,
            "as_of": as_of,
        },
        encounter_id=encounter_id,
        as_of=as_of,
    )
    print(
        f"运行 ID={state.run_id} encounter={encounter_id or '-'} "
        f"状态={state.status} 原因={state.stop_reason}"
    )


if __name__ == "__main__":
    main()
