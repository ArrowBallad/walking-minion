#!/usr/bin/env python3
"""Build the self-contained GI multi-patient execution viewer.

The builder is read-only with respect to source artifacts. It loads the selected
experiment manifest, discovers compatible historical runs, joins the five case
timelines, and embeds normalized data in one offline HTML file.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ARTIFACT_FILES = (
    "metadata.json",
    "run_state.json",
    "model_calls.jsonl",
    "tool_calls.jsonl",
    "prompt_snapshots.jsonl",
    "state_events.jsonl",
)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def parse_prompt_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    for message in reversed(snapshot.get("messages") or []):
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        try:
            value = json.loads(message["content"])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def load_case(case_dir: Path, project_root: Path) -> dict[str, Any]:
    records = read_jsonl(case_dir / "clinical_records.jsonl")
    patients = read_jsonl(case_dir / "patients.jsonl")
    encounters_doc = read_json(case_dir / "encounters.json")
    metadata = read_json(case_dir / "record_metadata.json")
    encounters = encounters_doc.get("encounters", encounters_doc) if isinstance(encounters_doc, dict) else encounters_doc
    patient_id = (patients[0].get("patient_id") if patients else None) or (encounters[0].get("patient_id") if encounters else case_dir.name)
    merged_records: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        record.update(metadata.get(record.get("record_id"), {}))
        merged_records.append(record)
    merged_records.sort(key=lambda row: (str(row.get("clinical_time", "")), str(row.get("record_id", ""))))
    return {
        "case_id": case_dir.name,
        "patient_id": patient_id,
        "source_path": safe_relative(case_dir, project_root),
        "patient": patients[0] if patients else {},
        "encounters": encounters,
        "records": merged_records,
    }


def run_collection(run_dir: Path, runs_root: Path) -> str:
    relative = run_dir.resolve().relative_to(runs_root.resolve())
    return relative.parts[0] if relative.parts else "runs"


def infer_encounter_id(metadata: dict[str, Any], run_dir: Path) -> str | None:
    value = metadata.get("encounter_id")
    if value:
        return str(value)
    for part in run_dir.parts:
        if part in {"E01", "E02", "E03"}:
            patient_id = metadata.get("patient_id")
            return f"{patient_id}-{part}" if patient_id else part
    return None


def load_run(
    run_dir: Path,
    project_root: Path,
    runs_root: Path,
    selected: bool,
    metric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing = [name for name in ARTIFACT_FILES if not (run_dir / name).is_file()]
    if missing:
        raise ValueError(f"{run_dir}: missing artifacts: {', '.join(missing)}")
    metadata = read_json(run_dir / "metadata.json")
    state = read_json(run_dir / "run_state.json")
    prompts = read_jsonl(run_dir / "prompt_snapshots.jsonl")
    for snapshot in prompts:
        snapshot["prompt_payload"] = parse_prompt_payload(snapshot)
    final_path = run_dir / "final.md"
    collection = run_collection(run_dir, runs_root)
    return {
        "id": metadata.get("run_id") or run_dir.name,
        "run_id": metadata.get("run_id") or run_dir.name,
        "patient_id": metadata.get("patient_id") or state.get("patient_id"),
        "encounter_id": infer_encounter_id(metadata, run_dir),
        "path": safe_relative(run_dir, project_root),
        "collection": collection,
        "track_label": "当前五患者统一实验" if selected else f"历史运行 · {collection}",
        "selected_track": selected,
        "metadata": metadata,
        "state": state,
        "model_calls": read_jsonl(run_dir / "model_calls.jsonl"),
        "tool_calls": read_jsonl(run_dir / "tool_calls.jsonl"),
        "prompt_snapshots": prompts,
        "state_events": read_jsonl(run_dir / "state_events.jsonl"),
        "final_markdown": final_path.read_text(encoding="utf-8") if final_path.is_file() else "",
        "metric": metric or {},
        "memory_loaded": [],
        "memory_written": [],
    }


def attach_memory(
    run: dict[str, Any],
    patient_ledger: list[dict[str, Any]],
    ledger_sources: list[str] | None = None,
) -> None:
    started = next((event for event in run["state_events"] if event.get("event_type") == "run_started"), {})
    view = started.get("memory_view") or {}
    run["memory_loaded"] = view.get("entries") or view.get("active_entries") or []
    run["memory_written"] = [entry for entry in patient_ledger if entry.get("source_run_id") == run["run_id"]]
    run["memory_ledger_sources"] = ledger_sources or []


def discover_history(runs_root: Path, project_root: Path, selected_paths: set[Path]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for metadata_path in sorted(runs_root.glob("gastro_demo*/**/metadata.json")):
        run_dir = metadata_path.parent.resolve()
        if run_dir in selected_paths:
            continue
        try:
            metadata = read_json(metadata_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        patient_id = str(metadata.get("patient_id", ""))
        encounter_id = infer_encounter_id(metadata, run_dir)
        if not patient_id.startswith("GI-DEMO-") or not encounter_id:
            continue
        if not all((run_dir / name).is_file() for name in ARTIFACT_FILES):
            continue
        history.append(load_run(run_dir, project_root, runs_root, selected=False))
    return history


def validate_annotations(annotations: dict[str, Any], runs: list[dict[str, Any]]) -> None:
    by_id = {run["run_id"]: run for run in runs}
    for item in annotations.get("annotations", []):
        run_id = item.get("run_id")
        if run_id not in by_id:
            raise ValueError(f"annotation {item.get('id')}: unknown run_id {run_id}")
        run = by_id[run_id]
        step = item.get("step")
        steps = {row.get("step") for row in run["model_calls"]}
        if step is not None and step not in steps:
            raise ValueError(f"annotation {item.get('id')}: unknown step {step} in {run_id}")
        call_id = item.get("call_id")
        if call_id and call_id not in {row.get("call_id") for row in run["tool_calls"]}:
            raise ValueError(f"annotation {item.get('id')}: unknown call_id {call_id}")
        record_id = item.get("record_id")
        if record_id and not any(row.get("input", {}).get("record_id") == record_id for row in run["tool_calls"]):
            raise ValueError(f"annotation {item.get('id')}: record {record_id} was not read in {run_id}")
    for highlight in annotations.get("highlights", []):
        for target in highlight.get("targets", []):
            if target.get("run_id") not in by_id:
                raise ValueError(f"highlight {highlight.get('id')}: unknown run {target.get('run_id')}")


def build_payload(project_root: Path, manifest_path: Path, annotations_path: Path) -> dict[str, Any]:
    runs_root = project_root / "runs"
    manifest = read_json(manifest_path)
    metrics_doc = read_json(project_root / manifest["run_metrics"])
    metric_rows = metrics_doc.get("runs", metrics_doc) if isinstance(metrics_doc, dict) else metrics_doc
    metrics = {row["run_id"]: row for row in metric_rows}

    cases_root = project_root / "cases" / "gastro_demo"
    cases = [load_case(path, project_root) for path in sorted(cases_root.glob("gi_demo_*")) if path.is_dir()]
    if len(cases) != 5:
        raise ValueError(f"expected five gastro demo cases, found {len(cases)}")

    selected_runs: list[dict[str, Any]] = []
    selected_paths: set[Path] = set()
    for item in manifest.get("runs", []):
        run_dir = (project_root / item["run_path"]).resolve()
        selected_paths.add(run_dir)
        selected_runs.append(load_run(run_dir, project_root, runs_root, selected=True, metric=metrics.get(item["run_id"], item)))
    if len(selected_runs) != 15:
        raise ValueError(f"expected fifteen selected runs, found {len(selected_runs)}")

    ledgers: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        ledger_path = project_root / "work" / "gastro_demo_multi_patient" / f"{case['case_id'].replace('_', '-')}-multi-v1" / "memory" / "patient_memory.jsonl"
        ledgers[case["patient_id"]] = read_jsonl(ledger_path)
    history = discover_history(runs_root, project_root, selected_paths)
    memory_by_run: dict[str, list[dict[str, Any]]] = {}
    memory_sources_by_run: dict[str, list[str]] = {}
    for ledger_path in sorted((project_root / "work").glob("**/memory/patient_memory.jsonl")):
        if "gastro_demo" not in ledger_path.as_posix():
            continue
        source = safe_relative(ledger_path, project_root)
        for entry in read_jsonl(ledger_path):
            source_run_id = entry.get("source_run_id")
            if not source_run_id:
                continue
            memory_by_run.setdefault(source_run_id, []).append(entry)
            memory_sources_by_run.setdefault(source_run_id, []).append(source)
    for run in selected_runs + history:
        ledger = ledgers.get(run["patient_id"], []) if run["selected_track"] else memory_by_run.get(run["run_id"], [])
        sources = memory_sources_by_run.get(run["run_id"], [])
        attach_memory(run, ledger, sorted(set(sources)))

    annotations = read_json(annotations_path)
    all_runs = selected_runs + history
    validate_annotations(annotations, all_runs)
    summary = sorted([run["metric"] for run in selected_runs], key=lambda row: (row.get("patient_id", ""), row.get("encounter_id", "")))
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": {key: manifest.get(key) for key in ("experiment_id", "completed_at", "git_commit", "runtime_version", "model", "limits_per_encounter", "audits")},
        "source_manifest": safe_relative(manifest_path, project_root),
        "annotations_source": safe_relative(annotations_path, project_root),
        "cases": cases,
        "runs": all_runs,
        "summary": summary,
        "highlights": annotations.get("highlights", []),
        "annotations": annotations.get("annotations", []),
        "memory_ledgers": ledgers,
        "known_limitations": annotations.get("known_limitations", []),
    }


def serialize_for_html(payload: dict[str, Any]) -> str:
    """Serialize data without allowing a source string to close the script tag."""
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build(project_root: Path, manifest_path: Path, annotations_path: Path, template_path: Path, output: Path) -> dict[str, Any]:
    payload = build_payload(project_root, manifest_path, annotations_path)
    template = template_path.read_text(encoding="utf-8")
    marker = "__TRACE_DATA__"
    if marker not in template:
        raise ValueError(f"{template_path}: missing {marker} marker")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace(marker, serialize_for_html(payload)), encoding="utf-8")
    print(f"Built {output} with {len(payload['cases'])} patients, {len(payload['summary'])} selected encounters, and {len(payload['runs']) - len(payload['summary'])} historical runs.")
    return payload


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=project_root / "runs" / "gastro_demo_multi_patient" / "experiment_manifest.json")
    parser.add_argument("--annotations", type=Path, default=project_root / "viewer" / "gastro_demo_annotations.json")
    parser.add_argument("--template", type=Path, default=project_root / "viewer" / "trace_viewer_template.html")
    parser.add_argument("--output", type=Path, default=project_root / "trace_viewer.html")
    args = parser.parse_args()
    build(project_root, args.manifest.resolve(), args.annotations.resolve(), args.template.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
