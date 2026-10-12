from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import copy
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
    optional = ("record_metadata.json", "encounters.json")
    for name in (*required, *optional):
        if not (case_seed / name).is_file():
            continue
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
        "available_at",
        "title",
        "preview",
        "status",
        "exam_type",
        "workflow_id",
        "workflow_stage",
    )

    def __init__(self, root: Path, *, as_of: str | None = None) -> None:
        self.root = root
        self.as_of = as_of

    @staticmethod
    def _availability_time(record: dict[str, Any]) -> str:
        value = record.get("available_at") or record.get("clinical_time")
        if not isinstance(value, str) or not value:
            raise ValueError(f"记录 {record.get('record_id')} 缺少 available_at/clinical_time")
        return value

    def _is_available(self, record: dict[str, Any]) -> bool:
        return self.as_of is None or self._availability_time(record) <= self.as_of

    def _project_available_record(
        self,
        record: dict[str, Any],
        *,
        all_record_ids: set[str],
        available_record_ids: set[str],
    ) -> dict[str, Any]:
        """Remove links to known clinical records that are not available yet."""

        def project(value: Any) -> Any:
            if isinstance(value, dict):
                projected: dict[str, Any] = {}
                for key, child in value.items():
                    if (
                        key.endswith("_reference")
                        and isinstance(child, str)
                        and child in all_record_ids
                        and child not in available_record_ids
                    ):
                        continue
                    if key.endswith("_references") and isinstance(child, list):
                        projected[key] = [
                            project(item)
                            for item in child
                            if not (
                                isinstance(item, str)
                                and item in all_record_ids
                                and item not in available_record_ids
                            )
                        ]
                        continue
                    projected[key] = project(child)
                return projected
            if isinstance(value, list):
                return [project(item) for item in value]
            return copy.deepcopy(value)

        return project(record)

    def _clinical_records(self) -> tuple[list[dict[str, Any]], set[str]]:
        records = read_jsonl(self.root / "clinical_records.jsonl")
        metadata_path = self.root / "record_metadata.json"
        metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("record_metadata.json 顶层必须是对象")
            metadata = loaded
        records = [
            {
                **record,
                **copy.deepcopy(metadata.get(str(record.get("record_id")), {})),
            }
            for record in records
        ]
        all_ids = {
            str(record["record_id"])
            for record in records
            if isinstance(record.get("record_id"), str)
        }
        available = [record for record in records if self._is_available(record)]
        available_ids = {str(record["record_id"]) for record in available}
        return (
            [
                self._project_available_record(
                    record,
                    all_record_ids=all_ids,
                    available_record_ids=available_ids,
                )
                for record in available
            ],
            all_ids,
        )

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
        exam_type: str | None = None,
        workflow_id: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        matches = []
        normalized_keyword = keyword.casefold().strip() if keyword else None
        available_records, _ = self._clinical_records()
        for record in available_records:
            if record.get("patient_id") != patient_id:
                continue
            if record_type is not None and record.get("record_type") != record_type:
                continue
            if exam_type is not None and record.get("exam_type") != exam_type:
                continue
            if workflow_id is not None and record.get("workflow_id") != workflow_id:
                continue
            clinical_time = record.get("clinical_time")
            if from_time is not None and (clinical_time is None or clinical_time < from_time):
                continue
            if to_time is not None and (clinical_time is None or clinical_time > to_time):
                continue
            if normalized_keyword:
                searchable = " ".join(
                    str(record.get(field, ""))
                    for field in (
                        "title",
                        "preview",
                        "keywords",
                        "exam_type",
                        "workflow_id",
                        "workflow_stage",
                    )
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
        available_records, _ = self._clinical_records()
        for record in available_records:
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

    def effective_for_patient(
        self, patient_id: str, *, limit: int = 12
    ) -> dict[str, Any]:
        if limit < 1:
            raise ValueError("memory limit 必须为正数")
        rows = self.for_patient(patient_id)
        effective_status = {
            str(row.get("memory_id")): str(row.get("status", "active")) for row in rows
        }
        for row in rows:
            related = row.get("related_memory_id")
            status = row.get("status")
            if related in effective_status and status in {"resolved", "superseded"}:
                effective_status[str(related)] = str(status)

        decorated = [
            {**copy.deepcopy(row), "effective_status": effective_status.get(str(row.get("memory_id")), "active")}
            for row in rows
        ]
        active = [row for row in decorated if row["effective_status"] == "active"]
        inactive = [row for row in decorated if row["effective_status"] != "active"]
        active_unresolved = [row for row in active if row.get("kind") == "unresolved"]
        active_other = [row for row in active if row.get("kind") != "unresolved"]
        prioritized_active = active_unresolved + active_other
        included_active = prioritized_active[:limit]
        remaining = limit - len(included_active)
        included_inactive = inactive[-remaining:] if remaining > 0 else []
        included = included_active + included_inactive
        return {
            "entries": included,
            "active_entries": included_active,
            "inactive_entries": included_inactive,
            "total_entry_count": len(rows),
            "total_active_count": len(active),
            "total_inactive_count": len(inactive),
            "omitted_active_count": max(0, len(active) - len(included_active)),
            "omitted_inactive_count": max(0, len(inactive) - len(included_inactive)),
            "limit": limit,
            "policy": (
                "append-only memory 的有效视图；优先保留所有 active unresolved，再保留其他 active，"
                "剩余空间提供最近的 resolved/superseded 历史。memory 不是当前患者事实。"
            ),
        }

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
