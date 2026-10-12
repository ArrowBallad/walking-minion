from __future__ import annotations

from pathlib import Path

from clinical_agent_explorer_phase1.storage import (
    append_jsonl,
    initialize_case_instance,
    read_jsonl,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "cases" / "gastro_demo" / "gi_demo_001"
WORK_ROOT = ROOT / "work" / "gastro_demo"
SOURCE_MEMORY = (
    WORK_ROOT
    / "gi-demo-001-encounters-v2"
    / "memory"
    / "patient_memory.jsonl"
)
INSTANCE_NAME = "gi-demo-001-e03-context-retry-v1"
SOURCE_RUN_IDS = {
    "p1-run-2ed3c307f21d",
    "p1-run-7b4ee7428483",
}


def main() -> None:
    target_root = WORK_ROOT / INSTANCE_NAME
    if target_root.exists():
        raise SystemExit(f"目标实例已存在，拒绝覆盖：{target_root}")
    paths = initialize_case_instance(CASE, WORK_ROOT, INSTANCE_NAME)
    rows = [
        row
        for row in read_jsonl(SOURCE_MEMORY)
        if row.get("source_run_id") in SOURCE_RUN_IDS
    ]
    counts = {
        source_run_id: sum(row.get("source_run_id") == source_run_id for row in rows)
        for source_run_id in SOURCE_RUN_IDS
    }
    if set(counts.values()) != {4} or len(rows) != 8:
        raise SystemExit(f"E01/E02 memory checkpoint 不符合预期：{counts}")
    for row in rows:
        append_jsonl(paths.memory, row)
    write_json(
        target_root / "checkpoint_provenance.json",
        {
            "source_memory": str(SOURCE_MEMORY.relative_to(ROOT)),
            "included_source_run_ids": sorted(SOURCE_RUN_IDS),
            "included_memory_count": len(rows),
            "excluded_later_runs": True,
        },
    )
    print(f"prepared={target_root} memory_entries={len(rows)}")


if __name__ == "__main__":
    main()
