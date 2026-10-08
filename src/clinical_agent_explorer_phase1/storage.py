from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import RunState, utc_now


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} 不是 JSON 对象")
        rows.append(value)
    return rows


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


@dataclass(frozen=True)
class InstancePaths:
    root: Path
    database: Path
    memory: Path


def initialize_case_instance(
    case_seed: Path, work_root: Path, instance_name: str, *, reset: bool = False
) -> InstancePaths:
    required = ("patients.jsonl", "clinical_records.jsonl", "orders.jsonl")
    missing = [name for name in required if not (case_seed / name).is_file()]
    if missing:
        raise ValueError(f"案例种子缺少文件：{', '.join(missing)}")
    root = work_root / instance_name
    database = root / "mock_db"
    memory = root / "memory" / "patient_memory.jsonl"
    if reset and root.exists():
        shutil.rmtree(root)
    database.mkdir(parents=True, exist_ok=True)
    memory.parent.mkdir(parents=True, exist_ok=True)
    for name in required:
        target = database / name
        if not target.exists():
            shutil.copyfile(case_seed / name, target)
    if not memory.exists():
        memory.touch()
    return InstancePaths(root=root, database=database, memory=memory)


class MockDatabase:
    INDEX_FIELDS = (
        "record_id",
        "patient_id",
        "record_type",
        "clinical_time",
        "title",
        "preview",
        "status",
    )

    def __init__(self, root: Path) -> None:
        self.root = root

    def get_patient(self, patient_id: str) -> dict[str, Any] | None:
        for patient in read_jsonl(self.root / "patients.jsonl"):
            if patient.get("patient_id") == patient_id:
                return patient
        return None

    def search_records(
        self,
        patient_id: str,
        *,
        record_type: str | None = None,
        keyword: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        matches = []
        normalized_keyword = keyword.casefold().strip() if keyword else None
        for record in read_jsonl(self.root / "clinical_records.jsonl"):
            if record.get("patient_id") != patient_id:
                continue
            if record_type is not None and record.get("record_type") != record_type:
                continue
            clinical_time = record.get("clinical_time")
            if from_time is not None and (clinical_time is None or clinical_time < from_time):
                continue
            if to_time is not None and (clinical_time is None or clinical_time > to_time):
                continue
            if normalized_keyword:
                searchable = " ".join(
                    str(record.get(field, "")) for field in ("title", "preview", "keywords")
                ).casefold()
                if normalized_keyword not in searchable:
                    continue
            matches.append(record)
        matches.sort(
            key=lambda item: (item.get("clinical_time") or "", item["record_id"]),
            reverse=True,
        )
        offset = int(cursor or "0")
        page = matches[offset : offset + limit]
        next_offset = offset + len(page)
        indexes = [
            {field: record.get(field) for field in self.INDEX_FIELDS if field in record}
            for record in page
        ]
        return {
            "records": indexes,
            "total_matches": len(matches),
            "returned_count": len(indexes),
            "has_more": next_offset < len(matches),
            "next_cursor": str(next_offset) if next_offset < len(matches) else None,
        }

    def get_record(self, patient_id: str, record_id: str) -> dict[str, Any] | None:
        for record in read_jsonl(self.root / "clinical_records.jsonl"):
            if record.get("patient_id") == patient_id and record.get("record_id") == record_id:
                return record
        return None

    def create_order(
        self,
        *,
        patient_id: str,
        order_type: str,
        reason: str,
        evidence_refs: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        order_id = f"ord-{uuid.uuid4().hex[:12]}"
        stored_at = utc_now()
        order = {
            "order_id": order_id,
            "patient_id": patient_id,
            "order_type": order_type,
            "reason": reason,
            "evidence_refs": list(evidence_refs),
            "status": "mock-created",
            "created_at": stored_at,
        }
        append_jsonl(self.root / "orders.jsonl", order)
        receipt = {
            "receipt_id": f"rcpt-{uuid.uuid4().hex[:12]}",
            "order_id": order_id,
            "patient_id": patient_id,
            "stored_at": stored_at,
        }
        return receipt, order

    def get_order(self, patient_id: str, order_id: str) -> dict[str, Any] | None:
        for order in read_jsonl(self.root / "orders.jsonl"):
            if order.get("patient_id") == patient_id and order.get("order_id") == order_id:
                return order
        return None


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def for_patient(self, patient_id: str) -> list[dict[str, Any]]:
        return [row for row in read_jsonl(self.path) if row.get("patient_id") == patient_id]

    def append(
        self,
        *,
        patient_id: str,
        source_run_id: str,
        kind: str,
        text: str,
        evidence_refs: list[str],
        status: str = "active",
        related_memory_id: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "memory_id": f"mem-{uuid.uuid4().hex[:12]}",
            "patient_id": patient_id,
            "created_at": utc_now(),
            "source_run_id": source_run_id,
            "kind": kind,
            "text": text,
            "evidence_refs": list(evidence_refs),
            "status": status,
        }
        if related_memory_id is not None:
            entry["related_memory_id"] = related_memory_id
        append_jsonl(self.path, entry)
        return entry


class RunArtifacts:
    def __init__(self, runs_root: Path, run_id: str, metadata: dict[str, Any]) -> None:
        self.root = runs_root / run_id
        self.root.mkdir(parents=True, exist_ok=False)
        self._event_number = 0
        write_json(
            self.root / "metadata.json",
            {"run_id": run_id, "created_at": utc_now(), **metadata},
        )

    def save_state(self, state: RunState) -> None:
        state.updated_at = utc_now()
        write_json(self.root / "run_state.json", state.to_dict())

    def append(self, filename: str, value: dict[str, Any]) -> None:
        append_jsonl(self.root / filename, value)

    def event(
        self,
        *,
        run_id: str,
        step: int,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        self._event_number += 1
        self.append(
            "state_events.jsonl",
            {
                "event_id": f"evt-{self._event_number:04d}",
                "run_id": run_id,
                "timestamp": utc_now(),
                "event_type": event_type,
                "step": step,
                **details,
            },
        )

    def final(self, text: str) -> None:
        (self.root / "final.md").write_text(text.rstrip() + "\n", encoding="utf-8")

