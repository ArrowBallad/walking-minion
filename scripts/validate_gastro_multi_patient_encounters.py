from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from clinical_agent_explorer_phase1.storage import MemoryStore, MockDatabase


ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = ROOT / "cases" / "gastro_demo"
REFERENCE_ROOT = ROOT / "docs" / "gastro_demo_reference"
CASE_IDS = tuple(f"gi_demo_{index:03d}" for index in range(1, 6))
DIAGNOSIS_LEAK_TERMS = {
    "gi_demo_001": ("乳糜泻", "Marsh"),
    "gi_demo_002": ("嗜酸性粒细胞性食管炎",),
    "gi_demo_003": ("憩室炎", "管状腺瘤"),
    "gi_demo_004": ("溃疡性直肠炎",),
    "gi_demo_005": ("胆源性急性胰腺炎",),
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        assert isinstance(value, dict), f"{path}:{line_number}: row must be an object"
        rows.append(value)
    return rows


def parse_time(value: Any, location: str) -> datetime:
    assert isinstance(value, str), f"{location}: time must be a string"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, f"{location}: timezone is required"
    return parsed


def collect_references(value: Any, location: str = "data") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key.endswith("_reference") and child is not None:
                assert isinstance(child, str), f"{child_location}: expected string"
                found.append((child_location, child))
            elif key.endswith("_references"):
                assert isinstance(child, list), f"{child_location}: expected list"
                for index, item in enumerate(child):
                    assert isinstance(item, str), f"{child_location}[{index}]: expected string"
                    found.append((f"{child_location}[{index}]", item))
            else:
                found.extend(collect_references(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(collect_references(child, f"{location}[{index}]"))
    return found


def validate_case(case_id: str, global_record_ids: set[str]) -> dict[str, Any]:
    case_dir = CASE_ROOT / case_id
    patient_id = case_id.replace("gi_demo_", "GI-DEMO-")
    for filename in (
        "patients.jsonl",
        "clinical_records.jsonl",
        "orders.jsonl",
        "task.md",
        "record_metadata.json",
        "encounters.json",
    ):
        assert (case_dir / filename).is_file(), f"{case_id}: missing {filename}"
    assert (REFERENCE_ROOT / f"{case_id}_reference.md").is_file()
    assert (REFERENCE_ROOT / f"{case_id}_sources.md").is_file()

    patients = load_jsonl(case_dir / "patients.jsonl")
    records = load_jsonl(case_dir / "clinical_records.jsonl")
    orders = load_jsonl(case_dir / "orders.jsonl")
    metadata = json.loads((case_dir / "record_metadata.json").read_text(encoding="utf-8"))
    encounter_doc = json.loads((case_dir / "encounters.json").read_text(encoding="utf-8"))
    encounters = encounter_doc["encounters"]

    assert len(patients) == 1 and patients[0]["patient_id"] == patient_id
    assert orders == [], f"{case_id}: mutable orders must start empty"
    assert len(encounters) == 3, f"{case_id}: expected three encounters"
    assert encounter_doc["availability_source"] == "record_metadata.json"

    by_id = {str(record["record_id"]): record for record in records}
    assert len(by_id) == len(records), f"{case_id}: duplicate record ID"
    assert not (set(by_id) & global_record_ids), f"{case_id}: cross-patient record collision"
    global_record_ids.update(by_id)
    assert set(metadata) == set(by_id), f"{case_id}: metadata keys do not match records"

    record_times: list[datetime] = []
    for record_id, record in by_id.items():
        assert record["patient_id"] == patient_id
        record_time = parse_time(record["clinical_time"], record_id)
        available_time = parse_time(metadata[record_id]["available_at"], record_id)
        assert available_time >= record_time, f"{record_id}: available before clinical time"
        record_times.append(record_time)
        for location, reference in collect_references(record["data"]):
            assert reference in by_id, f"{record_id}:{location}: unknown {reference}"
    assert record_times == sorted(record_times), f"{case_id}: records not chronological"

    prior_count = 0
    prior_as_of: datetime | None = None
    for index, encounter in enumerate(encounters, 1):
        assert encounter["encounter_id"] == f"{patient_id}-E{index:02d}"
        assert encounter["patient_id"] == patient_id
        as_of = parse_time(encounter["as_of"], encounter["encounter_id"])
        assert prior_as_of is None or as_of > prior_as_of
        prior_as_of = as_of
        available_ids = {
            record_id
            for record_id, item in metadata.items()
            if parse_time(item["available_at"], record_id) <= as_of
        }
        expected_count = int(encounter["expected_available_record_count"])
        assert len(available_ids) == expected_count
        assert expected_count > prior_count, f"{encounter['encounter_id']}: no new evidence"
        prior_count = expected_count

        database = MockDatabase(case_dir, as_of=encounter["as_of"])
        search = database.search_records(patient_id, limit=20)
        search_ids = {row["record_id"] for row in search["records"]}
        assert search_ids == available_ids
        assert all("data" not in row for row in search["records"])

        future_ids = set(by_id) - available_ids
        for future_id in future_ids:
            assert database.get_record(patient_id, future_id) is None
        for available_id in available_ids:
            projected = database.get_record(patient_id, available_id)
            assert projected is not None
            projected_refs = {reference for _, reference in collect_references(projected)}
            assert not (projected_refs & future_ids), (
                f"{encounter['encounter_id']}:{available_id}: future reference leaked"
            )

    task_texts = [(case_dir / "task.md").read_text(encoding="utf-8")]
    task_texts.extend(str(encounter["task"]) for encounter in encounters)
    for task_text in task_texts:
        assert f"gi-demo-{case_id[-3:]}-rec-" not in task_text
        assert "gastro_demo_reference" not in task_text
        assert "Underlying case truth" not in task_text
        for term in DIAGNOSIS_LEAK_TERMS[case_id]:
            assert term not in task_text, f"{case_id}: task leaks {term}"

    return {
        "patient_id": patient_id,
        "record_count": len(records),
        "encounter_counts": [item["expected_available_record_count"] for item in encounters],
    }


def validate_memory_isolation() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        store = MemoryStore(Path(temporary_directory) / "patient_memory.jsonl")
        for index in range(1, 6):
            patient_id = f"GI-DEMO-{index:03d}"
            store.append(
                patient_id=patient_id,
                source_run_id=f"test-{index}",
                kind="prior_work",
                text=f"isolated-{index}",
                evidence_refs=[],
            )
        for index in range(1, 6):
            patient_id = f"GI-DEMO-{index:03d}"
            rows = store.for_patient(patient_id)
            assert len(rows) == 1
            assert rows[0]["text"] == f"isolated-{index}"


def validate_all() -> dict[str, dict[str, Any]]:
    global_record_ids: set[str] = set()
    summary = {
        case_id: validate_case(case_id, global_record_ids) for case_id in CASE_IDS
    }
    validate_memory_isolation()
    assert len({item["patient_id"] for item in summary.values()}) == len(CASE_IDS)
    return summary


if __name__ == "__main__":
    result = validate_all()
    print(json.dumps(result, ensure_ascii=False, indent=2))
