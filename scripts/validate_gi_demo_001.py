from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "cases" / "gastro_demo" / "gi_demo_001"
REFERENCE_DIR = ROOT / "docs" / "gastro_demo_reference"
REQUIRED_CASE_FILES = (
    "patients.jsonl",
    "clinical_records.jsonl",
    "orders.jsonl",
    "task.md",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        assert isinstance(value, dict), f"{path}:{line_number}: row must be an object"
        rows.append(value)
    return rows


def parse_clinical_time(value: Any, record_id: str) -> datetime:
    assert isinstance(value, str), f"{record_id}: clinical_time must be a string"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssertionError(f"{record_id}: invalid clinical_time {value!r}") from exc
    assert parsed.tzinfo is not None, f"{record_id}: clinical_time must include a timezone"
    return parsed


def iter_references(value: Any, path: str = "data") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.endswith("_reference") and child is not None:
                assert isinstance(child, str), f"{child_path} must be a string or null"
                found.append((child_path, child))
            elif key.endswith("_references"):
                assert isinstance(child, list), f"{child_path} must be a list"
                for index, item in enumerate(child):
                    assert isinstance(item, str), f"{child_path}[{index}] must be a string"
                    found.append((f"{child_path}[{index}]", item))
            else:
                found.extend(iter_references(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(iter_references(child, f"{path}[{index}]"))
    return found


def validate() -> None:
    for filename in REQUIRED_CASE_FILES:
        assert (CASE_DIR / filename).is_file(), f"missing required case file: {filename}"
    assert (REFERENCE_DIR / "gi_demo_001_sources.md").is_file(), "missing source document"
    assert (REFERENCE_DIR / "gi_demo_001_reference.md").is_file(), "missing evaluator reference"

    patients = load_jsonl(CASE_DIR / "patients.jsonl")
    records = load_jsonl(CASE_DIR / "clinical_records.jsonl")
    orders = load_jsonl(CASE_DIR / "orders.jsonl")

    assert len(patients) == 1, "case must contain exactly one patient"
    patient = patients[0]
    patient_id = patient.get("patient_id")
    assert patient_id == "GI-DEMO-001", "unexpected patient_id"
    assert patient.get("record_id") == "patient-GI-DEMO-001", "unexpected patient record_id"
    assert isinstance(patient.get("demographics"), dict), "patient demographics are required"
    assert patient.get("source") == "synthetic_fixture", "patient source must be synthetic"

    assert 12 <= len(records) <= 18, f"expected 12-18 clinical records, found {len(records)}"
    assert len(records) <= 20, "clinical record count exceeds absolute maximum"
    required_fields = {
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
    seen_ids: set[str] = set()
    times: list[datetime] = []
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = record.get("record_id")
        assert isinstance(record_id, str) and record_id, "record_id is required"
        assert required_fields <= set(record), f"{record_id}: missing required fields"
        assert record_id not in seen_ids, f"duplicate record_id: {record_id}"
        seen_ids.add(record_id)
        by_id[record_id] = record
        assert record.get("patient_id") == patient_id, f"{record_id}: patient ownership mismatch"
        assert isinstance(record.get("record_type"), str) and record["record_type"], (
            f"{record_id}: record_type is required"
        )
        assert record.get("source") == "synthetic_fixture", f"{record_id}: non-synthetic source"
        assert isinstance(record.get("data"), dict), f"{record_id}: data must be an object"
        for field in ("title", "preview", "status"):
            assert isinstance(record.get(field), str) and record[field].strip(), (
                f"{record_id}: {field} is required"
            )
        times.append(parse_clinical_time(record.get("clinical_time"), record_id))

    assert times == sorted(times), "clinical_records.jsonl must be in chronological order"

    for record in records:
        for location, reference in iter_references(record.get("data")):
            assert reference in by_id, f"{record['record_id']}:{location}: unknown reference {reference}"

    request = by_id["gi-demo-001-rec-009"]
    procedure = by_id["gi-demo-001-rec-010"]
    report = by_id["gi-demo-001-rec-011"]
    pathology = by_id["gi-demo-001-rec-012"]
    assert request["record_type"] == "service_request" and request["status"] == "已排期"
    assert procedure["record_type"] == "procedure" and procedure["status"] == "已完成"
    assert procedure["data"]["request_reference"] == request["record_id"]
    assert procedure["data"]["endoscopy_report_reference"] == report["record_id"]
    assert procedure["data"]["pathology_reference"] == pathology["record_id"]
    assert report["data"]["procedure_reference"] == procedure["record_id"]
    assert pathology["data"]["procedure_reference"] == procedure["record_id"]
    assert report["clinical_time"] >= procedure["clinical_time"], "report predates procedure"
    assert pathology["clinical_time"] >= procedure["clinical_time"], "pathology predates procedure"

    assert orders == [], "historical clinical workflow must not prepopulate mutable orders.jsonl"

    task_text = (CASE_DIR / "task.md").read_text(encoding="utf-8")
    for leaked_term in ("乳糜泻", "Marsh", "gi-demo-001-rec-", "Underlying case truth"):
        assert leaked_term not in task_text, f"task.md leaks case conclusion or record target: {leaked_term}"

    evaluator_markers = (
        "Underlying case truth",
        "Acceptable Agent conclusions",
        "gastro_demo_reference",
    )
    for path in CASE_DIR.iterdir():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for marker in evaluator_markers:
            assert marker not in text, f"{path.name} exposes evaluator-only marker: {marker}"


if __name__ == "__main__":
    validate()
    print("GI Demo 001 validation passed: 1 patient, 18 clinical records, 0 mutable orders.")
