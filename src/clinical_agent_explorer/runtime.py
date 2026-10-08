from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

from .model_client import AgentClient
from .models import AgentDecision, RunState, ToolCall, utc_now
from .storage import MemoryStore, RunArtifacts
from .tools import ToolError, ToolRegistry


SYSTEM_PROMPT = """你正在操作一个完全使用合成数据的临床记录演示系统。

工程规则：
- 患者数据只能通过提供的工具读取，不得虚构记录。
- 患者记忆只是之前运行的工作记录，不是临床事实。若要据此陈述当前事实，必须重新查询数据库。
- 保留记录 ID，并区分 clinical_time 与运行时间戳。
- 尊重时间顺序，不得用一条记录静默覆盖另一条记录。
- create_order 返回的 receipt 尚未验证；只有单独调用 get_order 并且读回内容匹配，才算验证成功。
- 可以在一次响应中请求一个或多个互不依赖的工具调用；系统会按返回顺序逐个执行并分别记录。
- 所有自然语言内容，包括工具调用时的伴随说明，都必须使用中文。

准备结束时，只返回一个 JSON 对象，不要添加外围说明：
{
  "final_text": "中文、便于阅读的评估",
  "working_summary": "简短的本次运行摘要",
  "unresolved_questions": ["零个或多个未解决问题"],
  "evidence_refs": ["本次运行中工具实际返回的患者/临床记录 ID，或已独立读回验证的 action ID"],
  "memory_updates": [
    {
      "kind": "prior_work|unresolved|next_step",
      "text": "可选的中文既往工作记录",
      "evidence_refs": ["本次运行中工具实际返回的患者/临床记录 ID，或已独立读回验证的 action ID"],
      "status": "active|resolved|superseded",
      "related_memory_id": "可选的既往 memory ID"
    }
  ]
}
这些列表可以为空。不要为了填充字段而创建订单或记忆条目。所有面向人的文本都使用中文。
"""


class RuntimeFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _tool_call_message(calls: list[ToolCall], content: str | None) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": item.call_id,
                "type": "function",
                "function": {
                    "name": item.name,
                    "arguments": json.dumps(item.arguments, ensure_ascii=False, sort_keys=True),
                },
            }
            for item in calls
        ],
    }


def _state_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in after.items() if before.get(key) != value}


def _require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeFailure("invalid_final", f"{field} 必须是字符串列表")
    return value


def _current_run_refs(state: RunState) -> set[str]:
    refs = set(state.retrieved_record_ids)
    refs.update(
        str(action["order_id"])
        for action in state.verified_actions
        if action.get("verified") and action.get("order_id")
    )
    return refs


class ClinicalAgentRuntime:
    def __init__(
        self,
        *,
        client: AgentClient,
        tools: ToolRegistry,
        memory: MemoryStore,
        runs_root: Path,
        max_steps: int = 12,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.client = client
        self.tools = tools
        self.memory = memory
        self.runs_root = runs_root
        self.max_steps = max_steps

    def run(
        self,
        *,
        patient_id: str,
        task: str,
        case_id: str,
        run_id: str | None = None,
        model_metadata: dict[str, Any] | None = None,
    ) -> RunState:
        actual_run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        state = RunState(run_id=actual_run_id, patient_id=patient_id, task=task)
        artifacts = RunArtifacts(
            self.runs_root,
            actual_run_id,
            {
                "case_id": case_id,
                "patient_id": patient_id,
                "max_steps": self.max_steps,
                "model": model_metadata or {},
            },
        )
        prior_memory = self.memory.for_patient(patient_id)
        base_messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "patient_id": patient_id,
                        "patient_memory": prior_memory,
                        "memory_warning": (
                            "患者记忆仅代表之前的工作，不是数据库结果，也不计入本次运行证据。"
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ]
        history: list[dict[str, Any]] = []
        artifacts.save_state(state)
        artifacts.event(
            run_id=state.run_id,
            step=0,
            event_type="run_started",
            details={"loaded_memory_ids": [row.get("memory_id") for row in prior_memory]},
        )
        tool_call_count = 0

        for step in range(1, self.max_steps + 1):
            state.step = step
            context = base_messages + history + [
                {
                    "role": "user",
                    "content": json.dumps(
                        {"current_run_state": state.to_dict()},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            ]
            snapshot = {
                "run_id": state.run_id,
                "step": step,
                "timestamp": utc_now(),
                "messages": context,
                "tools": self.tools.schemas,
            }
            artifacts.prompt(snapshot)
            artifacts.event(
                run_id=state.run_id,
                step=step,
                event_type="prompt",
                details={"snapshot_ref": f"prompt_snapshots.jsonl#step-{step}"},
            )

            try:
                decision = self.client.decide(copy.deepcopy(context), copy.deepcopy(self.tools.schemas))
                artifacts.model_call(
                    {
                        "run_id": state.run_id,
                        "step": step,
                        "timestamp": utc_now(),
                        "content": decision.content,
                        "tool_calls": [
                            {
                                "call_id": call.call_id,
                                "name": call.name,
                                "arguments": call.arguments,
                            }
                            for call in decision.tool_calls
                        ],
                        "response_metadata": decision.response_metadata,
                    }
                )
                artifacts.event(
                    run_id=state.run_id,
                    step=step,
                    event_type="model_response",
                    details={"response_ref": f"model_calls.jsonl#step-{step}"},
                )
                self._validate_decision(decision)
                if decision.tool_calls:
                    if tool_call_count + len(decision.tool_calls) > self.max_steps:
                        self._stop(
                            state,
                            artifacts,
                            status="stopped",
                            reason="iteration_limit",
                            message=f"工具调用总数将超过上限 {self.max_steps}，因此在执行该批次前停止。",
                        )
                        return state
                    history.append(_tool_call_message(decision.tool_calls, decision.content))
                    for call in decision.tool_calls:
                        self._handle_tool_call(
                            state=state,
                            call=call,
                            history=history,
                            artifacts=artifacts,
                        )
                        tool_call_count += 1
                    artifacts.save_state(state)
                    continue
                self._handle_final(
                    state=state,
                    content=decision.content or "",
                    prior_memory=prior_memory,
                    artifacts=artifacts,
                )
                return state
            except ToolError as exc:
                self._stop(
                    state,
                    artifacts,
                    status="error",
                    reason=exc.code,
                    message=exc.message,
                )
                return state
            except RuntimeFailure as exc:
                self._stop(
                    state,
                    artifacts,
                    status="error",
                    reason=exc.code,
                    message=exc.message,
                )
                return state
            except Exception as exc:
                self._stop(
                    state,
                    artifacts,
                    status="error",
                    reason="model_or_internal_error",
                    message=str(exc),
                )
                return state

        self._stop(
            state,
            artifacts,
            status="stopped",
            reason="iteration_limit",
            message=f"达到最多 {self.max_steps} 个工具调用步骤后停止。",
        )
        return state

    @staticmethod
    def _validate_decision(decision: AgentDecision) -> None:
        if not isinstance(decision, AgentDecision):
            raise RuntimeFailure("invalid_model_response", "客户端没有返回 AgentDecision")
        if not decision.tool_calls and not decision.content:
            raise RuntimeFailure("empty_model_response", "模型既未返回工具调用，也未返回最终内容")

    def _handle_tool_call(
        self,
        *,
        state: RunState,
        call: ToolCall,
        history: list[dict[str, Any]],
        artifacts: RunArtifacts,
    ) -> None:
        artifacts.event(
            run_id=state.run_id,
            step=state.step,
            event_type="tool_call",
            details={"tool_name": call.name, "call_id": call.call_id, "input": call.arguments},
        )
        before = state.to_dict()
        try:
            result = self.tools.execute(call.name, call.arguments, state)
        except ToolError as exc:
            result = exc.as_dict()
            artifacts.tool_call(
                {
                    "run_id": state.run_id,
                    "step": state.step,
                    "timestamp": utc_now(),
                    "call_id": call.call_id,
                    "tool_name": call.name,
                    "input": call.arguments,
                    "output": result,
                }
            )
            artifacts.event(
                run_id=state.run_id,
                step=state.step,
                event_type="tool_result",
                details={"call_id": call.call_id, "output": result},
            )
            raise
        except Exception as exc:
            result = {
                "ok": False,
                "error": {"code": "tool_failure", "message": str(exc)},
            }
            artifacts.tool_call(
                {
                    "run_id": state.run_id,
                    "step": state.step,
                    "timestamp": utc_now(),
                    "call_id": call.call_id,
                    "tool_name": call.name,
                    "input": call.arguments,
                    "output": result,
                }
            )
            artifacts.event(
                run_id=state.run_id,
                step=state.step,
                event_type="tool_result",
                details={"call_id": call.call_id, "output": result},
            )
            raise RuntimeFailure("tool_failure", str(exc)) from exc

        self._apply_tool_result(state, call, result)
        artifacts.tool_call(
            {
                "run_id": state.run_id,
                "step": state.step,
                "timestamp": utc_now(),
                "call_id": call.call_id,
                "tool_name": call.name,
                "input": call.arguments,
                "output": result,
            }
        )
        artifacts.event(
            run_id=state.run_id,
            step=state.step,
            event_type="tool_result",
            details={
                "call_id": call.call_id,
                "output_ref": f"tool_calls.jsonl#{call.call_id}",
            },
        )
        delta = _state_delta(before, state.to_dict())
        if delta:
            artifacts.event(
                run_id=state.run_id,
                step=state.step,
                event_type="state_change",
                details={"state_delta": delta},
            )
        history.append(
            {
                "role": "tool",
                "tool_call_id": call.call_id,
                "name": call.name,
                "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
            }
        )

    @staticmethod
    def _apply_tool_result(
        state: RunState, call: ToolCall, result: dict[str, Any]
    ) -> None:
        if call.name == "get_patient" and result.get("patient"):
            record_id = result["patient"].get("record_id")
            if record_id and record_id not in state.retrieved_record_ids:
                state.retrieved_record_ids.append(record_id)
        elif call.name == "search_records":
            for record in result.get("records", []):
                record_id = record.get("record_id")
                if record_id and record_id not in state.retrieved_record_ids:
                    state.retrieved_record_ids.append(record_id)
        elif call.name == "create_order":
            state.proposed_actions.append(
                {
                    "tool_name": "create_order",
                    "arguments": copy.deepcopy(call.arguments),
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
                action["verification"] = {"read_back": copy.deepcopy(order), "matches": matches}
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
        required = {
            "final_text",
            "working_summary",
            "unresolved_questions",
            "evidence_refs",
            "memory_updates",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise RuntimeFailure("invalid_final", f"最终响应缺少字段：{', '.join(missing)}")
        if not isinstance(value["final_text"], str) or not isinstance(value["working_summary"], str):
            raise RuntimeFailure("invalid_final", "final_text 和 working_summary 必须是字符串")
        unresolved = _require_string_list(value["unresolved_questions"], "unresolved_questions")
        evidence_refs = _require_string_list(value["evidence_refs"], "evidence_refs")
        allowed_refs = _current_run_refs(state)
        unavailable = sorted(set(evidence_refs) - allowed_refs)
        if unavailable:
            raise RuntimeFailure(
                "invalid_final_evidence",
                f"最终响应引用了本次运行未检索的记录：{', '.join(unavailable)}",
            )
        updates = value["memory_updates"]
        if not isinstance(updates, list):
            raise RuntimeFailure("invalid_final", "memory_updates 必须是列表")
        prior_ids = {row.get("memory_id") for row in prior_memory}
        validated_updates: list[dict[str, Any]] = []
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                raise RuntimeFailure("invalid_final", f"memory_updates[{index}] 必须是对象")
            kind = update.get("kind")
            text = update.get("text")
            status = update.get("status", "active")
            refs = _require_string_list(update.get("evidence_refs"), f"memory_updates[{index}].evidence_refs")
            if kind not in {"prior_work", "unresolved", "next_step"}:
                raise RuntimeFailure("invalid_final", f"memory_updates[{index}].kind 无效")
            if status not in {"active", "resolved", "superseded"}:
                raise RuntimeFailure("invalid_final", f"memory_updates[{index}].status 无效")
            if not isinstance(text, str) or not text.strip():
                raise RuntimeFailure("invalid_final", f"memory_updates[{index}].text 不能为空")
            unavailable_refs = sorted(set(refs) - allowed_refs)
            if unavailable_refs:
                raise RuntimeFailure(
                    "invalid_final_evidence",
                    f"记忆更新引用了本次运行未检索的记录：{', '.join(unavailable_refs)}",
                )
            related = update.get("related_memory_id")
            if related is not None and related not in prior_ids:
                raise RuntimeFailure(
                    "invalid_final",
                    f"memory_updates[{index}].related_memory_id 不在已加载的既往记忆中",
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
        state.working_summary = value["working_summary"]
        state.unresolved_questions = unresolved
        state.status = "complete"
        state.stop_reason = "final_response"
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
        *,
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
