from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clinical_agent_explorer_phase1.model_client import DeepSeekClient  # noqa: E402
from clinical_agent_explorer_phase1.runtime import (  # noqa: E402
    MAX_TOOL_CALLS_PER_BATCH,
    RUNTIME_VERSION,
    ClinicalAgentRuntime,
)
from clinical_agent_explorer_phase1.storage import (  # noqa: E402
    MemoryStore,
    MockDatabase,
    initialize_case_instance,
    read_jsonl,
)
from clinical_agent_explorer_phase1.tools import ToolRegistry  # noqa: E402


EXPERIMENT_VERSION = "quick-comparison-v2"
BASELINE_SYSTEM = """你正在审阅完全合成的纵向临床记录。只根据本次 prompt 中提供的全文记录回答，不得虚构事实。
明确区分历史与当前状态，以及检查的申请、执行、采集、取消和最终结果。索引或标题不能替代全文内容。
所有任务只读，不创建订单或提出写入动作。使用中文。

只返回一个 JSON 对象：
{
  "final_text": "简洁但完整的中文回答",
  "evidence_refs": ["支持回答的记录 ID"]
}
"""


@dataclass(frozen=True)
class Case:
    case_id: str
    patient_id: str
    case_dir: Path


CASES = (
    Case(
        "cough_stopping_001",
        "COUGH-STOP-001",
        ROOT / "cases" / "phase1_challenge" / "cough_stopping_001",
    ),
    Case(
        "dizziness_longitudinal_001",
        "DZ-LONG-001",
        ROOT / "cases" / "phase1_challenge" / "dizziness_longitudinal_001",
    ),
    Case(
        "workflow_status_001",
        "WF-SHORT-001",
        ROOT / "cases" / "quick_comparison" / "workflow_status_001",
    ),
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def tokenize(text: str) -> list[str]:
    """Fixed tokenizer: lowercase alphanumeric words plus CJK unigrams and bigrams."""
    lowered = text.casefold()
    tokens = re.findall(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", lowered)
    for span in re.findall(r"[\u3400-\u9fff]+", lowered):
        tokens.extend(span)
        tokens.extend(span[index : index + 2] for index in range(len(span) - 1))
    return tokens


def record_text(record: dict[str, Any]) -> str:
    return " ".join(
        [
            str(record.get("title", "")),
            str(record.get("preview", "")),
            " ".join(str(item) for item in record.get("keywords", [])),
            json.dumps(record.get("data", {}), ensure_ascii=False, sort_keys=True),
        ]
    )


def bm25(task: str, records: list[dict[str, Any]], *, top_k: int = 8) -> list[dict[str, Any]]:
    query = Counter(tokenize(task))
    documents = [Counter(tokenize(record_text(record))) for record in records]
    lengths = [sum(document.values()) for document in documents]
    average_length = sum(lengths) / max(1, len(lengths))
    document_frequency = Counter()
    for document in documents:
        document_frequency.update(document.keys())

    scored = []
    count = len(documents)
    for record, document, length in zip(records, documents, lengths):
        score = 0.0
        for term, query_frequency in query.items():
            frequency = document.get(term, 0)
            if not frequency:
                continue
            frequency_in_docs = document_frequency[term]
            inverse_frequency = math.log(
                1 + (count - frequency_in_docs + 0.5) / (frequency_in_docs + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * length / max(1.0, average_length)
            )
            score += query_frequency * inverse_frequency * frequency * 2.5 / denominator
        scored.append({"record": record, "score": score})
    scored.sort(key=lambda item: (-item["score"], item["record"]["record_id"]))
    return scored[:top_k]


def call_chat(messages: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    request_body = {
        "model": model,
        "messages": messages,
        "thinking": {"type": "disabled"},
    }
    encoded = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=encoded,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("model response is not a JSON object")
                return request_body, body
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
        if attempt < 3:
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"model request failed after 3 attempts: {last_error}") from last_error


def serialize_context(
    patient: dict[str, Any] | None, records: list[dict[str, Any]], task: str
) -> str:
    return json.dumps(
        {"task": task, "patient": patient, "full_records": records},
        ensure_ascii=False,
        sort_keys=True,
    )


def normalize_response(body: dict[str, Any]) -> dict[str, Any]:
    choice = body["choices"][0]
    content = choice["message"].get("content") or ""
    parsed = None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        pass
    return {
        "content": content,
        "parsed": parsed,
        "response_id": body.get("id"),
        "model": body.get("model"),
        "finish_reason": choice.get("finish_reason"),
        "usage": body.get("usage") or {},
    }


def run_baseline(case: Case, method: str, output_root: Path) -> dict[str, Any]:
    task = (case.case_dir / "task.md").read_text(encoding="utf-8").strip()
    patients = read_jsonl(case.case_dir / "patients.jsonl")
    records = [
        record
        for record in read_jsonl(case.case_dir / "clinical_records.jsonl")
        if record.get("patient_id") == case.patient_id
    ]
    patient = next(
        (item for item in patients if item.get("patient_id") == case.patient_id), None
    )
    retrieval = None
    if method == "full_context":
        selected = records
    elif method == "rag_bm25":
        retrieval = bm25(task, records, top_k=8)
        selected = [item["record"] for item in retrieval]
    else:
        raise ValueError(f"unknown baseline: {method}")

    messages = [
        {"role": "system", "content": BASELINE_SYSTEM},
        {"role": "user", "content": serialize_context(patient, selected, task)},
    ]
    run_dir = output_root / method / case.case_id
    run_dir.mkdir(parents=True, exist_ok=False)
    if retrieval is not None:
        write_json(
            run_dir / "retrieval.json",
            {
                "method": "BM25",
                "top_k": 8,
                "tokenizer": "lowercase alphanumeric words plus CJK unigrams and bigrams",
                "records": [
                    {"record_id": item["record"]["record_id"], "score": item["score"]}
                    for item in retrieval
                ],
            },
        )
    write_json(run_dir / "request.json", {"messages": messages})
    request_body, response_body = call_chat(messages)
    write_json(run_dir / "request_payload.json", request_body)
    write_json(run_dir / "response.json", response_body)
    normalized = normalize_response(response_body)
    write_json(run_dir / "normalized.json", normalized)
    (run_dir / "final.md").write_text(normalized["content"].rstrip() + "\n", encoding="utf-8")
    summary = {
        "case_id": case.case_id,
        "method": method,
        "status": "complete",
        "model_calls": 1,
        "tool_calls": 0,
        "records_total": len(records),
        "records_exposed": len(selected),
        "record_ids_exposed": [record["record_id"] for record in selected],
        "usage": normalized["usage"],
        "model": normalized["model"],
        "artifact_dir": str(run_dir.relative_to(ROOT)),
    }
    write_json(run_dir / "summary.json", summary)
    return summary


def sum_usage(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    )
    return {
        key: sum(
            int((row.get("response_metadata", {}).get("usage") or {}).get(key, 0) or 0)
            for row in rows
        )
        for key in keys
    }


def run_v4(case: Case, output_root: Path, work_root: Path) -> dict[str, Any]:
    task = (case.case_dir / "task.md").read_text(encoding="utf-8").strip()
    instance = initialize_case_instance(
        case.case_dir,
        work_root,
        f"{case.case_id}-v4",
        reset=True,
    )
    runs_root = output_root / "v4"
    client = DeepSeekClient(timeout_seconds=90)
    runtime = ClinicalAgentRuntime(
        client=client,
        tools=ToolRegistry(MockDatabase(instance.database)),
        memory=MemoryStore(instance.memory),
        runs_root=runs_root,
        max_steps=25,
        max_tool_calls=40,
    )
    run_id = case.case_id
    state = runtime.run(
        patient_id=case.patient_id,
        task=task,
        case_id=f"{case.case_id}_exploratory_quick_comparison",
        run_id=run_id,
        model_metadata={
            "adapter": "deepseek-chat-completions",
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
            "model": os.environ.get("LLM_MODEL", "deepseek-chat"),
            "thinking": "disabled",
            "parallel_tool_calls": True,
        },
    )
    run_dir = runs_root / run_id
    model_rows = read_jsonl(run_dir / "model_calls.jsonl")
    tool_rows = read_jsonl(run_dir / "tool_calls.jsonl")
    records = read_jsonl(case.case_dir / "clinical_records.jsonl")
    summary = {
        "case_id": case.case_id,
        "method": "v4",
        "status": state.status,
        "stop_reason": state.stop_reason,
        "model_calls": len(model_rows),
        "tool_calls": len(tool_rows),
        "tool_calls_by_name": dict(Counter(row.get("tool_name") for row in tool_rows)),
        "records_total": len(records),
        "records_exposed": len(state.inspected_record_ids),
        "record_ids_exposed": state.inspected_record_ids,
        "records_discovered": len(state.discovered_record_ids),
        "usage": sum_usage(model_rows),
        "model": sorted(
            {
                row.get("response_metadata", {}).get("model")
                for row in model_rows
                if row.get("response_metadata", {}).get("model")
            }
        ),
        "artifact_dir": str(run_dir.relative_to(ROOT)),
    }
    write_json(run_dir / "comparison_summary.json", summary)
    return summary


def validate_cases() -> None:
    for case in CASES:
        for filename in ("task.md", "patients.jsonl", "clinical_records.jsonl", "orders.jsonl"):
            if not (case.case_dir / filename).is_file():
                raise FileNotFoundError(case.case_dir / filename)
        records = read_jsonl(case.case_dir / "clinical_records.jsonl")
        if not records or any(record.get("patient_id") != case.patient_id for record in records):
            raise ValueError(f"invalid records for {case.case_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fixed 3x3 exploratory comparison")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "runs" / "quick_comparison" / EXPERIMENT_VERSION,
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=ROOT / "work" / "quick_comparison" / EXPERIMENT_VERSION,
    )
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    validate_cases()
    if args.validate_only:
        print("case validation passed")
        return
    if args.output_root.exists():
        raise SystemExit(f"output already exists: {args.output_root}")
    args.output_root.mkdir(parents=True)
    manifest = {
        "experiment_version": EXPERIMENT_VERSION,
        "classification": "exploratory/regression comparison; not unseen",
        "model_requested": os.environ.get("LLM_MODEL", "deepseek-chat"),
        "base_url": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
        "cases": [case.case_id for case in CASES],
        "methods": ["full_context", "rag_bm25", "v4"],
        "v4_runtime_version": RUNTIME_VERSION,
        "v4_max_steps": 25,
        "v4_max_tool_calls": 40,
        "v4_max_tool_calls_per_batch": MAX_TOOL_CALLS_PER_BATCH,
    }
    write_json(args.output_root / "manifest.json", manifest)
    summaries = []
    for case in CASES:
        for method in ("full_context", "rag_bm25"):
            try:
                summaries.append(run_baseline(case, method, args.output_root))
            except Exception as exc:
                failure = {
                    "case_id": case.case_id,
                    "method": method,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                summaries.append(failure)
                write_json(args.output_root / method / case.case_id / "failure.json", failure)
        try:
            summaries.append(run_v4(case, args.output_root, args.work_root))
        except Exception as exc:
            failure = {
                "case_id": case.case_id,
                "method": "v4",
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
            summaries.append(failure)
            failure_dir = args.output_root / "v4" / case.case_id
            failure_dir.mkdir(parents=True, exist_ok=True)
            write_json(failure_dir / "comparison_failure.json", failure)
    write_json(args.output_root / "results.json", summaries)
    completed = sum(item.get("status") == "complete" for item in summaries)
    print(f"completed {completed}/9 runs; results: {args.output_root}")


if __name__ == "__main__":
    main()
