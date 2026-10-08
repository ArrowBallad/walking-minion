from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

from .model_client import AgentClient
from .models import AgentDecision, PatientContext, RunState, ToolCall, utc_now
from .storage import MemoryStore, RunArtifacts
from .tools import ToolError, ToolRegistry


RUNTIME_VERSION = "phase1-v2-recoverable-tool-errors"

RECOVERABLE_TOOL_ERROR_CODES = frozenset(
    {
        "invalid_arguments",
        "uninspected_context_ref",
        "unknown_context_ref",
        "context_limit",
        "uninspected_evidence_refs",
    }
)


SYSTEM_PROMPT = """你正在操作一个完全使用合成数据的纵向临床记录研究系统。

目标不是一次性总结全部病历，而是根据任务逐步发现、读取和组织相关信息。

数据访问规则：
- 患者数据只能通过工具读取，不得虚构记录。
- search_records 只返回索引和短预览。预览只能帮助选择下一步，不能作为临床事实证据。
- 重要事实必须先用 get_record 读取全文；最终证据和写入证据只能引用已读取全文的记录。
- 使用 information_need 简洁说明当前缺口以及本次查询为何能补充它，不要输出隐藏思维链。
- search_records 返回 has_more=true 时，不能把当前页当作完整搜索结果。
- update_working_context 保存本次任务的有限工作认知。它不是患者数据库，也不能覆盖原始记录。
- known_facts 与 conflicts 只能引用已读取全文的记录。保留旧事实、被取代事实和冲突，不要静默合并。
- 患者 memory 是之前运行的工作记录，不是当前事实；如需使用，必须重新查询数据库。
- create_order receipt 尚未验证；只有独立 get_order 读回匹配后才算 verified。
- 每轮只请求一个工具。这样该工具的原始结果会出现在下一轮有限 prompt 中，便于你决定是否更新 working context。
- 每轮输入都是由 runtime 重新构建的，不是新的任务起点。先检查 current_step、retrieval_state 和 recent_tool_activity，不要无理由重复同一读取或查询。
- 最近工具结果可能包含可纠正的参数或证据边界错误。请依据公开的 error code 和 message 自行决定修正参数、补查证据或放弃该动作；runtime 不会自动替你修改调用。
- 所有自然语言使用中文。

当前 prompt 只包含 patient working context 和最近少量工具结果。更早的原始结果仍在 artifacts 中；如需重新查看，应再次查询或 get_record。

准备结束时，只返回一个 JSON 对象：
{
  "final_text": "中文评估",
  "run_summary": "本次检索和决策过程的简短摘要",
  "evidence_refs": ["已读取全文的记录 ID、患者记录 ID或已验证 action ID"],
  "memory_updates": [
    {
      "kind": "prior_work|unresolved|next_step",
      "text": "可选中文记忆",
      "evidence_refs": ["本次运行允许引用的 ID"],
      "status": "active|resolved|superseded",
      "related_memory_id": "可选的既往 memory ID"
    }
  ]
}
memory_updates 可以为空。不要为了填充字段而创建订单或记忆。
"""


class RuntimeFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _tool_call_message(calls: list[ToolCall], content: str | None) -> dict[str, Any]:
    return {
        "content": content,
        "tool_calls": [
            {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}
            for call in calls
        ],
    }


def _state_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in after.items() if before.get(key) != value}


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeFailure("invalid_final", f"{field} 必须是字符串列表")
    return value


def _allowed_refs(state: RunState) -> set[str]:
    refs = set(state.patient_record_ids) | set(state.inspected_record_ids)
    refs.update(
        str(action["order_id"])
        for action in state.verified_actions
        if action.get("verified") and action.get("order_id")
    )
    return refs


def _prompt_view(value: dict[str, Any], *, max_chars: int = 8000) -> dict[str, Any]:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(raw) <= max_chars:
        return value
    return {
        "truncated_for_prompt": True,
        "json_preview": raw[:max_chars],
        "full_result_saved_in_tool_calls": True,
    }


class ClinicalAgentRuntime:
    def __init__(
        self,
        *,
        client: AgentClient,
        tools: ToolRegistry,
        memory: MemoryStore,
        runs_root: Path,
        max_steps: int = 20,
        max_tool_calls: int = 30,
        recent_result_limit: int = 2,
        max_prompt_bytes: int = 32_000,
        max_final_revisions: int = 2,
        max_consecutive_recoverable_errors: int = 2,
    ) -> None:
        if min(
            max_steps,
            max_tool_calls,
            recent_result_limit,
            max_prompt_bytes,
            max_final_revisions,
            max_consecutive_recoverable_errors,
        ) < 1:
            raise ValueError("runtime limits 必须为正数")
        self.client = client
        self.tools = tools
        self.memory = memory
        self.runs_root = runs_root
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.recent_result_limit = recent_result_limit
        self.max_prompt_bytes = max_prompt_bytes
        self.max_final_revisions = max_final_revisions
        self.max_consecutive_recoverable_errors = max_consecutive_recoverable_errors

    def run(
        self,
        *,
        patient_id: str,
        task: str,
        case_id: str,
        run_id: str | None = None,
        model_metadata: dict[str, Any] | None = None,
    ) -> RunState:
        actual_run_id = run_id or f"p1-run-{uuid.uuid4().hex[:12]}"
        state = RunState(run_id=actual_run_id, patient_id=patient_id, task=task)
        artifacts = RunArtifacts(
            self.runs_root,
            actual_run_id,
            {
                "phase": "phase1",
                "runtime_version": RUNTIME_VERSION,
                "case_id": case_id,
                "patient_id": patient_id,
                "max_steps": self.max_steps,
                "max_tool_calls": self.max_tool_calls,
                "recent_result_limit": self.recent_result_limit,
                "max_prompt_bytes": self.max_prompt_bytes,
                "max_final_revisions": self.max_final_revisions,
                "max_consecutive_recoverable_errors": self.max_consecutive_recoverable_errors,
                "model": model_metadata or {},
            },
        )
        all_memory = self.memory.for_patient(patient_id)
        selected_memory = all_memory[-10:]
        recent_results: list[dict[str, Any]] = []
        recent_activity: list[dict[str, Any]] = []
        omitted_results = 0
        tool_call_count = 0
        final_revision_count = 0
        decision_revision_count = 0
        consecutive_recoverable_errors = 0
        artifacts.save_state(state)
        artifacts.event(
            run_id=state.run_id,
            step=0,
            event_type="run_started",
            details={
                "loaded_memory_ids": [row.get("memory_id") for row in selected_memory],
                "omitted_memory_count": max(0, len(all_memory) - len(selected_memory)),
            },
        )

        for step in range(1, self.max_steps + 1):
            state.step = step
            try:
                messages, prompt_metadata = self._build_prompt(
                    state=state,
                    task=task,
                    memory=selected_memory,
                    recent_results=recent_results,
                    recent_activity=recent_activity,
                    omitted_results=omitted_results,
                )
            except RuntimeFailure as exc:
                self._stop(state, artifacts, "error", exc.code, exc.message)
                return state

            artifacts.append(
                "prompt_snapshots.jsonl",
                {
                    "run_id": state.run_id,
                    "step": step,
                    "timestamp": utc_now(),
                    "messages": messages,
                    "tools": self.tools.schemas,
                    **prompt_metadata,
                },
            )
            artifacts.event(
                run_id=state.run_id,
                step=step,
                event_type="prompt",
                details={"snapshot_ref": f"prompt_snapshots.jsonl#step-{step}", **prompt_metadata},
            )

            try:
                decision = self.client.decide(
                    copy.deepcopy(messages), copy.deepcopy(self.tools.schemas)
                )
                artifacts.append(
                    "model_calls.jsonl",
                    {
                        "run_id": state.run_id,
                        "step": step,
                        "timestamp": utc_now(),
                        **_tool_call_message(decision.tool_calls, decision.content),
                        "response_metadata": decision.response_metadata,
                    },
                )
                artifacts.event(
                    run_id=state.run_id,
                    step=step,
                    event_type="model_response",
                    details={"response_ref": f"model_calls.jsonl#step-{step}"},
                )
                self._validate_decision(decision)
                if len(decision.tool_calls) > 1:
                    decision_revision_count += 1
                    if decision_revision_count > 3:
                        raise RuntimeFailure(
                            "invalid_model_response",
                            "模型连续返回多个工具调用，超过 3 次原子调用修正机会。",
                        )
                    feedback = {
                        "call_id": f"runtime-decision-validation-{decision_revision_count}",
                        "tool_name": "runtime_validation",
                        "information_need": "按原子工具调用契约修正模型响应。",
                        "result": {
                            "ok": False,
                            "error": {
                                "code": "multiple_tool_calls_not_allowed",
                                "message": "本轮返回了多个工具调用；这些调用均未执行。每轮只能选择一个工具。",
                            },
                        },
                        "raw_result_ref": None,
                    }
                    recent_results.append(feedback)
                    while len(recent_results) > self.recent_result_limit:
                        recent_results.pop(0)
                        omitted_results += 1
                    recent_activity.append(
                        {
                            "step": state.step,
                            "tool_name": "runtime_validation",
                            "ok": False,
                            "error_code": "multiple_tool_calls_not_allowed",
                        }
                    )
                    if len(recent_activity) > 12:
                        recent_activity.pop(0)
                    artifacts.event(
                        run_id=state.run_id,
                        step=state.step,
                        event_type="model_response_rejected",
                        details={
                            "reason": "multiple_tool_calls_not_allowed",
                            "returned_tool_call_count": len(decision.tool_calls),
                            "revision": decision_revision_count,
                            "max_revisions": 3,
                        },
                    )
                    continue
                if not decision.tool_calls:
                    try:
                        self._handle_final(
                            state=state,
                            content=decision.content or "",
                            prior_memory=selected_memory,
                            artifacts=artifacts,
                        )
                    except RuntimeFailure as exc:
                        if (
                            exc.code in {"invalid_final", "invalid_final_evidence"}
                            and final_revision_count < self.max_final_revisions
                        ):
                            final_revision_count += 1
                            feedback = {
                                "call_id": f"runtime-final-validation-{final_revision_count}",
                                "tool_name": "runtime_validation",
                                "information_need": "修正最终 JSON，使其满足已公开的证据与格式边界。",
                                "result": {
                                    "ok": False,
                                    "error": {"code": exc.code, "message": exc.message},
                                    "instruction": "请依据当前 state 修正最终 JSON；不要把未读全文记录当作 evidence_refs。",
                                },
                                "raw_result_ref": None,
                            }
                            recent_results.append(feedback)
                            while len(recent_results) > self.recent_result_limit:
                                recent_results.pop(0)
                                omitted_results += 1
                            recent_activity.append(
                                {
                                    "step": state.step,
                                    "tool_name": "runtime_validation",
                                    "ok": False,
                                    "error_code": exc.code,
                                }
                            )
                            if len(recent_activity) > 12:
                                recent_activity.pop(0)
                            artifacts.event(
                                run_id=state.run_id,
                                step=state.step,
                                event_type="final_rejected",
                                details={
                                    "reason": exc.code,
                                    "message": exc.message,
                                    "revision": final_revision_count,
                                    "max_revisions": self.max_final_revisions,
                                },
                            )
                            continue
                        raise
                    return state
                if tool_call_count + len(decision.tool_calls) > self.max_tool_calls:
                    self._stop(
                        state,
                        artifacts,
                        "stopped",
                        "tool_call_limit",
                        f"工具调用总数将超过上限 {self.max_tool_calls}。",
                    )
                    return state
                for call in decision.tool_calls:
                    tool_call_count += 1
                    state_before_call = copy.deepcopy(state)
                    try:
                        result = self._handle_tool_call(state, call, artifacts)
                    except ToolError as exc:
                        if exc.code not in RECOVERABLE_TOOL_ERROR_CODES:
                            raise
                        if state.to_dict() != state_before_call.to_dict():
                            state.__dict__.clear()
                            state.__dict__.update(copy.deepcopy(state_before_call.__dict__))
                            raise RuntimeFailure(
                                "tool_error_state_mutation",
                                "可恢复工具错误发生时 RunState 被修改；已恢复快照并停止。",
                            ) from exc

                        consecutive_recoverable_errors += 1
                        error_result = exc.as_dict()
                        recent_results.append(
                            {
                                "call_id": call.call_id,
                                "tool_name": call.name,
                                "information_need": call.arguments.get("information_need"),
                                "result": error_result,
                                "raw_result_ref": f"tool_calls.jsonl#{call.call_id}",
                            }
                        )
                        while len(recent_results) > self.recent_result_limit:
                            recent_results.pop(0)
                            omitted_results += 1
                        activity = self._activity_view(call, error_result)
                        activity["step"] = state.step
                        recent_activity.append(activity)
                        if len(recent_activity) > 12:
                            recent_activity.pop(0)
                        artifacts.event(
                            run_id=state.run_id,
                            step=state.step,
                            event_type="recoverable_tool_error",
                            details={
                                "call_id": call.call_id,
                                "tool_name": call.name,
                                "error": error_result["error"],
                                "consecutive_count": consecutive_recoverable_errors,
                                "max_consecutive_errors": self.max_consecutive_recoverable_errors,
                                "state_unchanged": True,
                            },
                        )
                        if (
                            consecutive_recoverable_errors
                            > self.max_consecutive_recoverable_errors
                        ):
                            self._stop(
                                state,
                                artifacts,
                                "stopped",
                                "recoverable_tool_error_limit",
                                (
                                    "连续可恢复工具错误超过上限 "
                                    f"{self.max_consecutive_recoverable_errors}；"
                                    f"最后错误为 {exc.code}: {exc.message}"
                                ),
                            )
                            return state
                        artifacts.save_state(state)
                        break

                    consecutive_recoverable_errors = 0
                    recent_results.append(
                        {
                            "call_id": call.call_id,
                            "tool_name": call.name,
                            "information_need": call.arguments.get("information_need"),
                            "result": _prompt_view(result),
                            "raw_result_ref": f"tool_calls.jsonl#{call.call_id}",
                        }
                    )
                    while len(recent_results) > self.recent_result_limit:
                        recent_results.pop(0)
                        omitted_results += 1
                    activity = self._activity_view(call, result)
                    activity["step"] = state.step
                    recent_activity.append(activity)
                    if len(recent_activity) > 12:
                        recent_activity.pop(0)
                artifacts.save_state(state)
            except ToolError as exc:
                self._stop(state, artifacts, "error", exc.code, exc.message)
                return state
            except RuntimeFailure as exc:
                self._stop(state, artifacts, "error", exc.code, exc.message)
                return state
            except Exception as exc:
                self._stop(
                    state,
                    artifacts,
                    "error",
                    "model_or_internal_error",
                    str(exc),
                )
                return state

        self._stop(
            state,
            artifacts,
            "stopped",
            "iteration_limit",
            f"达到最多 {self.max_steps} 个模型步骤后停止。",
        )
        return state

    def _build_prompt(
        self,
        *,
        state: RunState,
        task: str,
        memory: list[dict[str, Any]],
        recent_results: list[dict[str, Any]],
        recent_activity: list[dict[str, Any]],
        omitted_results: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected_results = copy.deepcopy(recent_results)
        trimmed_here = 0
        while True:
            payload = {
                "task": task,
                "patient_id": state.patient_id,
                "current_step": state.step,
                "patient_memory": memory,
                "memory_warning": "memory 是既往工作，不是当前数据库事实。",
                "retrieval_state": {
                    "discovered_record_ids": state.discovered_record_ids,
                    "inspected_record_ids": state.inspected_record_ids,
                    "patient_record_ids": state.patient_record_ids,
                },
                "patient_context": state.patient_context.to_dict(),
                "context_status": {
                    "fresh_full_record_ids_in_recent_results": [
                        item["result"].get("record", {}).get("record_id")
                        for item in selected_results
                        if item.get("tool_name") == "get_record"
                        and isinstance(item.get("result"), dict)
                        and item["result"].get("record")
                    ],
                    "inspected_but_not_in_relevant_record_refs": [
                        record_id
                        for record_id in state.inspected_record_ids
                        if record_id not in state.patient_context.relevant_record_refs
                    ],
                    "guidance": (
                        "若最近全文与任务相关，可在继续检索前用 update_working_context 保存有限事实；"
                        "不相关记录无需纳入。该提示不指定应保存的内容。"
                    ),
                },
                "actions": {
                    "proposed": state.proposed_actions,
                    "verified": state.verified_actions,
                },
                "recent_tool_results": selected_results,
                "recent_tool_activity": recent_activity,
                "omitted_prior_tool_result_count": omitted_results + trimmed_here,
                "prompt_policy": "仅提供有限 working context 和最近工具结果；原始结果保存在 artifacts。",
            }
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ]
            byte_size = len(
                json.dumps(
                    {"messages": messages, "tools": self.tools.schemas},
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            )
            if byte_size <= self.max_prompt_bytes:
                return messages, {
                    "prompt_bytes": byte_size,
                    "included_recent_result_count": len(selected_results),
                    "omitted_prior_tool_result_count": omitted_results + trimmed_here,
                }
            if selected_results:
                selected_results.pop(0)
                trimmed_here += 1
                continue
            raise RuntimeFailure(
                "prompt_context_too_large",
                f"即使移除最近工具结果，prompt 仍超过 {self.max_prompt_bytes} bytes。",
            )

    @staticmethod
    def _activity_view(call: ToolCall, result: dict[str, Any]) -> dict[str, Any]:
        """保留去重所需的调用账本，不复制临床记录正文。"""
        activity: dict[str, Any] = {
            "tool_name": call.name,
            "information_need": call.arguments.get("information_need"),
            "ok": result.get("ok"),
        }
        if result.get("ok") is False and result.get("error"):
            activity["error"] = copy.deepcopy(result["error"])
            return activity
        if call.name == "search_records":
            activity.update(
                {
                    "query": {
                        key: call.arguments[key]
                        for key in ("record_type", "keyword", "from_time", "to_time", "limit", "cursor")
                        if key in call.arguments
                    },
                    "returned_record_ids": [
                        row.get("record_id") for row in result.get("records", [])
                    ],
                    "has_more": result.get("has_more"),
                    "next_cursor": result.get("next_cursor"),
                }
            )
        elif call.name == "get_record":
            activity.update(
                {
                    "record_id": call.arguments.get("record_id"),
                    "found": result.get("found"),
                }
            )
        elif call.name == "get_patient":
            activity["found"] = result.get("found")
        elif call.name == "update_working_context":
            context = result.get("patient_context") or {}
            activity["context_counts"] = {
                "known_facts": len(context.get("known_facts", [])),
                "unresolved_questions": len(context.get("unresolved_questions", [])),
                "conflicts": len(context.get("conflicts", [])),
                "relevant_record_refs": len(context.get("relevant_record_refs", [])),
            }
        elif call.name == "create_order":
            activity["order_id"] = (result.get("receipt") or {}).get("order_id")
        elif call.name == "get_order":
            activity.update(
                {
                    "order_id": call.arguments.get("order_id"),
                    "found": result.get("found"),
                }
            )
        return activity

    @staticmethod
    def _validate_decision(decision: AgentDecision) -> None:
        if not isinstance(decision, AgentDecision):
            raise RuntimeFailure("invalid_model_response", "客户端没有返回 AgentDecision")
        if not decision.tool_calls and not decision.content:
            raise RuntimeFailure("empty_model_response", "模型未返回工具调用或最终内容")

    def _handle_tool_call(
        self, state: RunState, call: ToolCall, artifacts: RunArtifacts
    ) -> dict[str, Any]:
        artifacts.event(
            run_id=state.run_id,
            step=state.step,
            event_type="tool_call",
            details={"call_id": call.call_id, "tool_name": call.name, "input": call.arguments},
        )
        before = state.to_dict()
        try:
            result = self.tools.execute(call.name, call.arguments, state)
        except ToolError as exc:
            result = exc.as_dict()
            self._save_tool_result(state, call, result, artifacts, inline_error=True)
            raise
        except Exception as exc:
            result = {"ok": False, "error": {"code": "tool_failure", "message": str(exc)}}
            self._save_tool_result(state, call, result, artifacts, inline_error=True)
            raise RuntimeFailure("tool_failure", str(exc)) from exc

        self._apply_tool_result(state, call, result)
        self._save_tool_result(state, call, result, artifacts, inline_error=False)
        delta = _state_delta(before, state.to_dict())
        if delta:
            artifacts.event(
                run_id=state.run_id,
                step=state.step,
                event_type="state_change",
                details={"call_id": call.call_id, "state_delta": delta},
            )
        return result

    @staticmethod
    def _save_tool_result(
        state: RunState,
        call: ToolCall,
        result: dict[str, Any],
        artifacts: RunArtifacts,
        *,
        inline_error: bool,
    ) -> None:
        artifacts.append(
            "tool_calls.jsonl",
            {
                "run_id": state.run_id,
                "step": state.step,
                "timestamp": utc_now(),
                "call_id": call.call_id,
                "tool_name": call.name,
                "input": call.arguments,
                "output": result,
            },
        )
        details: dict[str, Any] = {
            "call_id": call.call_id,
            "output_ref": f"tool_calls.jsonl#{call.call_id}",
        }
        if inline_error:
            details["error"] = result.get("error")
        artifacts.event(
            run_id=state.run_id,
            step=state.step,
            event_type="tool_result",
            details=details,
        )

    @staticmethod
    def _apply_tool_result(
        state: RunState, call: ToolCall, result: dict[str, Any]
    ) -> None:
        if call.name == "get_patient" and result.get("patient"):
            record_id = result["patient"].get("record_id")
            if record_id and record_id not in state.patient_record_ids:
                state.patient_record_ids.append(record_id)
        elif call.name == "search_records":
            for record in result.get("records", []):
                record_id = record.get("record_id")
                if record_id and record_id not in state.discovered_record_ids:
                    state.discovered_record_ids.append(record_id)
        elif call.name == "get_record" and result.get("record"):
            record_id = result["record"].get("record_id")
            if record_id and record_id not in state.discovered_record_ids:
                state.discovered_record_ids.append(record_id)
            if record_id and record_id not in state.inspected_record_ids:
                state.inspected_record_ids.append(record_id)
        elif call.name == "update_working_context":
            state.patient_context = PatientContext(**result["patient_context"])
        elif call.name == "create_order":
            stored_arguments = {
                key: copy.deepcopy(call.arguments[key])
                for key in ("patient_id", "order_type", "reason", "evidence_refs")
            }
            state.proposed_actions.append(
                {
                    "tool_name": "create_order",
                    "arguments": stored_arguments,
                    "information_need": call.arguments["information_need"],
                    "receipt": copy.deepcopy(result["receipt"]),
                    "status": "unverified",
                }
            )
        elif call.name == "get_order":
            order_id = call.arguments["order_id"]
            for action in state.proposed_actions:
                if action.get("receipt", {}).get("order_id") != order_id:
                    continue
                expected = action["arguments"]
                order = result.get("order")
                matches = bool(
                    result.get("found")
                    and order
                    and order.get("order_id") == order_id
                    and order.get("patient_id") == expected["patient_id"]
                    and order.get("order_type") == expected["order_type"]
                    and order.get("reason") == expected["reason"]
                    and order.get("evidence_refs") == expected["evidence_refs"]
                )
                action["status"] = "verified" if matches else "verification_failed"
                action["verification"] = {"matches": matches, "read_back": copy.deepcopy(order)}
                if matches and not any(
                    item.get("order_id") == order_id for item in state.verified_actions
                ):
                    state.verified_actions.append(
                        {"order_id": order_id, "tool_name": "create_order", "verified": True}
                    )
                break

    def _handle_final(
        self,
        *,
        state: RunState,
        content: str,
        prior_memory: list[dict[str, Any]],
        artifacts: RunArtifacts,
    ) -> None:
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeFailure("invalid_final", f"最终响应不是有效 JSON：{exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeFailure("invalid_final", "最终响应必须是 JSON 对象")
        required = {"final_text", "run_summary", "evidence_refs", "memory_updates"}
        missing = sorted(required - value.keys())
        if missing:
            raise RuntimeFailure("invalid_final", f"最终响应缺少字段：{', '.join(missing)}")
        if not isinstance(value["final_text"], str) or not isinstance(value["run_summary"], str):
            raise RuntimeFailure("invalid_final", "final_text 和 run_summary 必须是字符串")
        evidence_refs = _string_list(value["evidence_refs"], "evidence_refs")
        allowed = _allowed_refs(state)
        unavailable = sorted(set(evidence_refs) - allowed)
        if unavailable:
            raise RuntimeFailure(
                "invalid_final_evidence",
                f"最终响应引用了未读取全文或未验证的 ID：{', '.join(unavailable)}",
            )
        updates = value["memory_updates"]
        if not isinstance(updates, list):
            raise RuntimeFailure("invalid_final", "memory_updates 必须是列表")
        prior_ids = {row.get("memory_id") for row in prior_memory}
        validated_updates = []
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                raise RuntimeFailure("invalid_final", f"memory_updates[{index}] 必须是对象")
            kind = update.get("kind")
            text = update.get("text")
            status = update.get("status", "active")
            refs = _string_list(update.get("evidence_refs"), f"memory_updates[{index}].evidence_refs")
            if kind not in {"prior_work", "unresolved", "next_step"}:
                raise RuntimeFailure("invalid_final", f"memory_updates[{index}].kind 无效")
            if status not in {"active", "resolved", "superseded"}:
                raise RuntimeFailure("invalid_final", f"memory_updates[{index}].status 无效")
            if not isinstance(text, str) or not text.strip():
                raise RuntimeFailure("invalid_final", f"memory_updates[{index}].text 不能为空")
            unavailable_refs = sorted(set(refs) - allowed)
            if unavailable_refs:
                raise RuntimeFailure(
                    "invalid_final_evidence",
                    f"memory update 引用了未读取全文或未验证的 ID：{', '.join(unavailable_refs)}",
                )
            related = update.get("related_memory_id")
            if related is not None and related not in prior_ids:
                raise RuntimeFailure(
                    "invalid_final",
                    f"memory_updates[{index}].related_memory_id 不在已加载 memory 中",
                )
            validated_updates.append(
                {
                    "kind": kind,
                    "text": text,
                    "status": status,
                    "evidence_refs": refs,
                    "related_memory_id": related,
                }
            )

        before = state.to_dict()
        state.status = "complete"
        state.stop_reason = "final_response"
        state.final_summary = value["run_summary"]
        state.final_evidence_refs = evidence_refs
        for update in validated_updates:
            self.memory.append(
                patient_id=state.patient_id,
                source_run_id=state.run_id,
                **update,
            )
        artifacts.event(
            run_id=state.run_id,
            step=state.step,
            event_type="final",
            details={
                "evidence_refs": evidence_refs,
                "memory_updates_written": len(validated_updates),
                "state_delta": _state_delta(before, state.to_dict()),
            },
        )
        references = ", ".join(evidence_refs) if evidence_refs else "无"
        artifacts.final(f"# 最终结果\n\n{value['final_text']}\n\n证据引用：{references}")
        artifacts.save_state(state)

    @staticmethod
    def _stop(
        state: RunState,
        artifacts: RunArtifacts,
        status: str,
        reason: str,
        message: str,
    ) -> None:
        before = state.to_dict()
        state.status = status
        state.stop_reason = reason
        artifacts.event(
            run_id=state.run_id,
            step=state.step,
            event_type="stopped",
            details={"reason": reason, "message": message, "state_delta": _state_delta(before, state.to_dict())},
        )
        artifacts.final(f"# 运行已停止\n\n原因：`{reason}`\n\n{message}")
        artifacts.save_state(state)
