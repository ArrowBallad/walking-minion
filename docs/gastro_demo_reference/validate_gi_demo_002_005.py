from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = ROOT / "cases" / "gastro_demo"
REFERENCE_ROOT = ROOT / "docs" / "gastro_demo_reference"
CASE_IDS = ("gi_demo_002", "gi_demo_003", "gi_demo_004", "gi_demo_005")
PATIENT_IDS = {
    "gi_demo_002": "GI-DEMO-002",
    "gi_demo_003": "GI-DEMO-003",
    "gi_demo_004": "GI-DEMO-004",
    "gi_demo_005": "GI-DEMO-005",
}
TASK_LEAK_TERMS = {
    "gi_demo_002": ("嗜酸性粒细胞性食管炎", "gi-demo-002-rec-"),
    "gi_demo_003": ("憩室炎", "gi-demo-003-rec-"),
    "gi_demo_004": ("溃疡性直肠炎", "gi-demo-004-rec-"),
    "gi_demo_005": ("胆源性急性胰腺炎", "gi-demo-005-rec-"),
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        assert isinstance(row, dict), f"{path}:{line_number}: row must be an object"
        rows.append(row)
    return rows


def parse_time(value: Any, record_id: str) -> datetime:
    assert isinstance(value, str), f"{record_id}: clinical_time must be a string"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssertionError(f"{record_id}: invalid clinical_time: {value}") from exc
    assert parsed.tzinfo is not None, f"{record_id}: clinical_time must include timezone"
    return parsed


def collect_references(value: Any, location: str = "data") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key.endswith("_reference") and child is not None:
                assert isinstance(child, str), f"{child_location}: reference must be a string"
                found.append((child_location, child))
            elif key.endswith("_references"):
                assert isinstance(child, list), f"{child_location}: references must be a list"
                for index, item in enumerate(child):
                    assert isinstance(item, str), f"{child_location}[{index}]: must be a string"
                    found.append((f"{child_location}[{index}]", item))
            else:
                found.extend(collect_references(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(collect_references(child, f"{location}[{index}]"))
    return found


def validate_case(case_id: str, global_ids: set[str]) -> int:
    case_dir = CASE_ROOT / case_id
    for filename in ("patients.jsonl", "clinical_records.jsonl", "orders.jsonl", "task.md"):
        assert (case_dir / filename).is_file(), f"{case_id}: missing {filename}"
    assert (REFERENCE_ROOT / f"{case_id}_sources.md").is_file(), f"{case_id}: missing sources"
    assert (REFERENCE_ROOT / f"{case_id}_reference.md").is_file(), f"{case_id}: missing reference"

    patients = load_jsonl(case_dir / "patients.jsonl")
    records = load_jsonl(case_dir / "clinical_records.jsonl")
    orders = load_jsonl(case_dir / "orders.jsonl")
    assert len(patients) == 1, f"{case_id}: expected exactly one patient"
    assert 12 <= len(records) <= 20, f"{case_id}: expected 12-20 records, got {len(records)}"
    assert orders == [], f"{case_id}: mutable orders.jsonl must be empty"

    patient = patients[0]
    patient_id = PATIENT_IDS[case_id]
    assert patient.get("patient_id") == patient_id, f"{case_id}: wrong patient_id"
    assert patient.get("source") == "synthetic_fixture", f"{case_id}: patient is not synthetic"
    assert isinstance(patient.get("demographics"), dict), f"{case_id}: demographics missing"

    required = {
        "record_id",
        "patient_id",
        "record_type",
        "clinical_time",
        "title",
        "preview",
        "status",
        "source",
        "data",
    }
    local_ids: set[str] = set()
    times: list[datetime] = []
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = record.get("record_id")
        assert isinstance(record_id, str) and record_id, f"{case_id}: record_id missing"
        assert required <= set(record), f"{record_id}: required field missing"
        assert record_id not in local_ids, f"{case_id}: duplicate record_id {record_id}"
        assert record_id not in global_ids, f"cross-case duplicate record_id {record_id}"
        local_ids.add(record_id)
        global_ids.add(record_id)
        by_id[record_id] = record
        assert record.get("patient_id") == patient_id, f"{record_id}: patient ownership mismatch"
        assert record.get("source") == "synthetic_fixture", f"{record_id}: non-synthetic source"
        assert isinstance(record.get("data"), dict), f"{record_id}: data must be an object"
        for field in ("record_type", "title", "preview", "status"):
            assert isinstance(record.get(field), str) and record[field].strip(), (
                f"{record_id}: {field} missing"
            )
        times.append(parse_time(record.get("clinical_time"), record_id))
    assert times == sorted(times), f"{case_id}: clinical records are not chronological"

    for record in records:
        for location, reference in collect_references(record["data"]):
            assert reference in by_id, f"{record['record_id']}:{location}: unknown {reference}"

    task = (case_dir / "task.md").read_text(encoding="utf-8")
    assert patient_id in task, f"{case_id}: task does not identify the patient"
    for leaked_term in TASK_LEAK_TERMS[case_id]:
        assert leaked_term not in task, f"{case_id}: task leaks {leaked_term}"
    for marker in ("Underlying case truth", "Acceptable Agent conclusions", "gastro_demo_reference"):
        for path in case_dir.iterdir():
            if path.is_file():
                assert marker not in path.read_text(encoding="utf-8"), (
                    f"{case_id}:{path.name}: evaluator-only marker leaked"
                )
    return len(records)


def validate() -> dict[str, int]:
    global_ids: set[str] = set()
    counts = {case_id: validate_case(case_id, global_ids) for case_id in CASE_IDS}
    assert len(set(PATIENT_IDS.values())) == len(PATIENT_IDS), "patient IDs must be unique"
    return counts


if __name__ == "__main__":
    counts = validate()
    detail = ", ".join(f"{case_id}={count}" for case_id, count in counts.items())
    print(f"Validation passed: {detail}; total={sum(counts.values())}; mutable orders=0.")
