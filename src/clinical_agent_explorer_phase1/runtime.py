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


RUNTIME_VERSION = "phase1-v4.3-context-retry"
MAX_TOOL_CALLS_PER_BATCH = 4
PROMPT_RETRIEVAL_CATALOG_LIMIT = 12
PROMPT_ACTIVITY_LIMIT = 8
MAX_WORKING_CONTEXT_REVISIONS = 2
WORKING_CONTEXT_RETRY_TTL_STEPS = 2

RETRIEVAL_CATALOG_INDEX_FIELDS = (
    "record_id",
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

RECOVERABLE_TOOL_ERROR_CODES = frozenset(
    {
        "invalid_arguments",
        "unknown_working_question_id",
        "inactive_working_question_id",
        "id_scope_mismatch",
        "uninspected_context_ref",
        "unknown_context_ref",
        "context_limit",
        "uninspected_evidence_refs",
    }
)

STOPPING_GUIDANCE = {
    "finish_when_core_answer_supported": (
        "如果现有已读全文证据已经足够回答当前 task 的核心问题，应优先结束并生成 final，"
        "而不是为了完整性继续检索。"
    ),
    "unresolved_questions_may_remain": (
        "不需要解决所有 unresolved questions 才能结束；仍然相关的不确定性可以原样保留到 final。"
    ),
    "continue_only_if_material": (
        "只有当某个缺失信息可能实质改变当前 assessment 时，才值得继续查询。"
    ),
    "unread_index_is_not_obligation": "index 中存在未读记录，不等于必须全部读取。",
    "pagination_is_need_driven": (
        "has_more=true 不意味着必须翻完所有页面；只有当前 information need 需要继续搜索时才分页。"
    ),
    "catalog_check_before_final": (
        "准备 final 前检查 persistent retrieval catalog：若已发现但未读候选的 title、type 或 preview "
        "明显可能直接回答 task 的明确要求或仍保留的 unresolved question，应自行决定读取该记录，"
        "或判断它对当前 task 不具有实质作用。不会改变 clinical conclusion，不等于不会影响 task completion。"
    ),
    "decision_owner": (
        "是否已有足够证据由 Agent 根据当前 task 判断；runtime 不计算 clinical sufficiency，"
        "不强制工具顺序，也不替 Agent 决定停止。"
    ),
}


SYSTEM_PROMPT = """你正在操作一个完全使用合成数据的纵向临床记录研究系统。

目标不是一次性总结全部病历，而是根据任务逐步发现、读取和组织相关信息。

数据访问规则：
- 患者数据只能通过工具读取，不得虚构记录。
- search_records 只返回索引和短预览。预览只能帮助选择下一步，不能作为临床事实证据。
- 重要事实必须先用 get_record 读取全文；最终证据和写入证据只能引用已读取全文的记录。
- 使用 information_need 简洁说明当前缺口以及本次查询为何能补充它，不要输出隐藏思维链。
- search_records 返回 has_more=true 时，不能把当前页当作完整搜索结果。
- update_working_context 通过稳定 entry_id 增量保存或更新本次任务的有限工作认知；未涉及条目保持不变。它不是患者数据库。
- patient_context.question_states[*].entry_id 仅属于当前 run；只有其中真实存在且 active 的 ID 才能用于 resolve_question。不要凭空构造 question ID。
- patient_memory.entries[*].memory_id 属于跨 run Memory，不能用于 resolve_question；只能在 final.memory_updates 中作为 related_memory_id 写入 resolved/superseded transition。
- 使用 add_finding/update_finding/supersede_finding 保存事实变化，使用 add_question/resolve_question 管理当前 run 的问题状态。为隔离失败风险，先单独提交事实操作；问题状态迁移应在后续调用中提交，不要与新增 findings 混在同一次调用。
- known_facts 与 conflicts 只能引用已读取全文的记录。保留旧事实、被取代事实和冲突，不要静默合并。
- 患者 memory 是之前运行的工作记录，不是当前事实；如需使用，必须重新查询数据库。
- patient_memory.effective view 会区分 active 与 resolved/superseded；解决既往问题时，在 final memory_updates 中用 related_memory_id 关联旧条目，并引用本次已读证据。
- create_order receipt 尚未验证；只有独立 get_order 读回匹配后才算 verified。
- 每轮最多请求 4 个互相独立的工具调用。runtime 会按返回顺序执行，并在下一轮优先提供本批结果。
- 同一批中的后一步不能依赖前一步才产生且调用时未知的值；例如 create_order 的 receipt 应在下一轮收到后再用于 get_order。
- 每轮输入都是由 runtime 重新构建的，不是新的任务起点。先检查 execution_budget、retrieval_state 和 recent_tool_activity，不要无理由重复同一读取或查询。
- context_status 会区分 inspected、当前正文可见和已持久化到 working context 的记录。inspected 不等于正文当前可见。
- execution_budget 给出剩余工具额度；下一批请求不得超过 remaining_tool_calls。额度不足时应缩小批次、保存关键 working context，或直接形成阶段性 final。
- 最近工具结果可能包含可纠正的参数或证据边界错误。请依据公开的 error code 和 message 自行决定修正参数、补查证据或放弃该动作；runtime 不会自动替你修改调用。
- working_context_retry 若存在，表示上一次 update_working_context 提案未应用。优先根据原提案、operation_index、valid_question_ids 和结构化错误删除或修正坏操作并重提；这些 proposed operations 尚不是已确认事实，不需要为了修正结构再次读取病历。
- 不需要解决所有 unresolved questions 才能结束；仍然相关的不确定性可以原样保留到 final。
- 如果现有已读全文证据已经足够回答当前 task 的核心问题，应优先结束并生成 final，而不是为了完整性继续检索。
- 只有当某个缺失信息可能实质改变当前 assessment 时，才值得继续查询。
- index 中存在未读记录，不等于必须全部读取。
- has_more=true 不意味着必须翻完所有页面；只有当前 information need 需要继续搜索时才分页。
- 准备 final 前检查 persistent retrieval catalog。若某个已发现但未读候选的 title、type 或 preview 明显可能直接回答当前 task 的明确要求或 working context 中仍保留的 unresolved question，应自行决定读取该记录，或者判断它对当前 task 不具有实质作用。
- “不会改变 clinical conclusion”不等于“不会影响 task completion”：某条记录即使不改变核心结论，也可能直接回答用户明确要求的一部分。
- 是否已有足够证据由你根据当前 task 判断；runtime 不计算 clinical sufficiency，不强制工具顺序，也不替你决定停止。
- 所有自然语言使用中文。

当前 prompt 只包含 patient working context、最近工具结果和最近完整批次。更早的原始结果仍在 artifacts 中；如 prompt 明确标记某条全文未包含，必须再次 get_record 才能在当前决策中使用其正文。

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
memory_updates 可以省略或为空；它是附属输出，非法条目会被跳过而不会否定合法主 final。不要为了填充字段而创建订单或记忆。
"""


class RuntimeFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _serialized_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


class PromptBudgetManager:
    """Build and trim the model-facing prompt while preserving protected state."""

    def __init__(self, *, max_bytes: int, tools: list[dict[str, Any]]) -> None:
        self.max_bytes = max_bytes
        self.tools = tools

    def messages(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ]

    def measure(
        self, messages: list[dict[str, Any]], payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "limit_bytes": self.max_bytes,
            "total_bytes": _serialized_bytes(
                {"messages": messages, "tools": self.tools}
            ),
            "system_message_bytes": _serialized_bytes(messages[0]),
            "tool_schema_bytes": _serialized_bytes(self.tools),
            "user_payload_bytes": _serialized_bytes(payload),
            "payload_component_bytes": {
                key: _serialized_bytes(value) for key, value in payload.items()
            },
        }


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
        retrieval_catalog_limit: int = 24,
        retrieval_preview_chars: int = 160,
        memory_limit: int = 12,
    ) -> None:
        if min(
            max_steps,
            max_tool_calls,
            recent_result_limit,
            max_prompt_bytes,
            max_final_revisions,
            max_consecutive_recoverable_errors,
            retrieval_catalog_limit,
            retrieval_preview_chars,
            memory_limit,
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
        self.retrieval_catalog_limit = retrieval_catalog_limit
        self.retrieval_preview_chars = retrieval_preview_chars
        self.memory_limit = memory_limit

    def run(
        self,
        *,
        patient_id: str,
        task: str,
        case_id: str,
        run_id: str | None = None,
        model_metadata: dict[str, Any] | None = None,
        encounter_id: str | None = None,
        as_of: str | None = None,
    ) -> RunState:
        actual_run_id = run_id or f"p1-run-{uuid.uuid4().hex[:12]}"
        state = RunState(
            run_id=actual_run_id,
            patient_id=patient_id,
            task=task,
            encounter_id=encounter_id,
            as_of=as_of,
        )
        artifacts = RunArtifacts(
            self.runs_root,
            actual_run_id,
            {
                "phase": "phase1",
                "runtime_version": RUNTIME_VERSION,
                "case_id": case_id,
                "patient_id": patient_id,
                "encounter_id": encounter_id,
                "as_of": as_of,
                "max_steps": self.max_steps,
                "max_tool_calls": self.max_tool_calls,
                "max_tool_calls_per_batch": MAX_TOOL_CALLS_PER_BATCH,
                "recent_result_limit": self.recent_result_limit,
                "max_prompt_bytes": self.max_prompt_bytes,
                "max_final_revisions": self.max_final_revisions,
                "max_consecutive_recoverable_errors": self.max_consecutive_recoverable_errors,
                "max_working_context_revisions": MAX_WORKING_CONTEXT_REVISIONS,
                "working_context_retry_ttl_steps": WORKING_CONTEXT_RETRY_TTL_STEPS,
                "retrieval_catalog_limit": self.retrieval_catalog_limit,
                "retrieval_preview_chars": self.retrieval_preview_chars,
                "memory_limit": self.memory_limit,
                "model": model_metadata or {},
            },
        )
        all_memory = self.memory.for_patient(patient_id)
        memory_view = self.memory.effective_for_patient(
            patient_id, limit=self.memory_limit
        )
        selected_memory = memory_view["entries"]
        state.loaded_memory_ids = [
            str(row["memory_id"])
            for row in selected_memory
            if row.get("memory_id")
        ]
        recent_results: list[dict[str, Any]] = []
        recent_activity: list[dict[str, Any]] = []
        working_context_retry: dict[str, Any] | None = None
        omitted_results = 0
        tool_call_count = 0
        final_revision_count = 0
        consecutive_recoverable_errors = 0
        artifacts.save_state(state)
        artifacts.event(
            run_id=state.run_id,
            step=0,
            event_type="run_started",
            details={
                "loaded_memory_ids": [row.get("memory_id") for row in selected_memory],
                "active_memory_ids": [
                    row.get("memory_id") for row in memory_view["active_entries"]
                ],
                "inactive_memory_ids": [
                    row.get("memory_id") for row in memory_view["inactive_entries"]
                ],
                "omitted_memory_count": (
                    memory_view["omitted_active_count"]
                    + memory_view["omitted_inactive_count"]
                ),
                "memory_view": memory_view,
            },
        )

        for step in range(1, self.max_steps + 1):
            state.step = step
            if (
                working_context_retry is not None
                and step > working_context_retry["expires_after_step"]
            ):
                artifacts.event(
                    run_id=state.run_id,
                    step=step,
                    event_type="working_context_retry_cleared",
                    details={
                        "reason": "ttl_expired",
                        "source_call_id": working_context_retry["source_call_id"],
                    },
                )
                working_context_retry = None
            try:
                messages, prompt_metadata = self._build_prompt(
                    state=state,
                    task=task,
                    memory=memory_view,
                    recent_results=recent_results,
                    recent_activity=recent_activity,
                    omitted_results=omitted_results,
                    tool_call_count=tool_call_count,
                    working_context_retry=working_context_retry,
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
                if len(decision.tool_calls) > MAX_TOOL_CALLS_PER_BATCH:
                    consecutive_recoverable_errors += 1
                    feedback = {
                        "call_id": f"runtime-batch-validation-step-{state.step}",
                        "tool_name": "runtime_validation",
                        "information_need": "将工具批次缩减到允许的大小。",
                        "result": {
                            "ok": False,
                            "error": {
                                "code": "tool_batch_too_large",
                                "message": (
                                    f"本轮返回 {len(decision.tool_calls)} 个工具调用，超过每批上限 "
                                    f"{MAX_TOOL_CALLS_PER_BATCH}；本批均未执行。"
                                ),
                            },
                            "returned_call_ids": [
                                call.call_id for call in decision.tool_calls
                            ],
                            "max_tool_calls_per_batch": MAX_TOOL_CALLS_PER_BATCH,
                        },
                        "raw_result_ref": None,
                    }
                    omitted_results = self._retain_latest_batch_results(
                        recent_results,
                        [feedback],
                        omitted_results,
                    )
                    recent_activity.append(
                        {
                            "step": state.step,
                            "tool_name": "runtime_validation",
                            "ok": False,
                            "error_code": "tool_batch_too_large",
                        }
                    )
                    if len(recent_activity) > 12:
                        recent_activity.pop(0)
                    artifacts.event(
                        run_id=state.run_id,
                        step=state.step,
                        event_type="tool_batch_rejected",
                        details={
                            "reason": "tool_batch_too_large",
                            "call_ids": [call.call_id for call in decision.tool_calls],
                            "returned_tool_call_count": len(decision.tool_calls),
                            "max_tool_calls_per_batch": MAX_TOOL_CALLS_PER_BATCH,
                            "consecutive_count": consecutive_recoverable_errors,
                            "max_consecutive_errors": self.max_consecutive_recoverable_errors,
                            "executed": False,
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
                                "连续超大工具批次超过上限 "
                                f"{self.max_consecutive_recoverable_errors}。"
                            ),
                        )
                        return state
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
                    if working_context_retry is not None:
                        artifacts.event(
                            run_id=state.run_id,
                            step=state.step,
                            event_type="working_context_retry_cleared",
                            details={
                                "reason": "final_without_retry",
                                "source_call_id": working_context_retry[
                                    "source_call_id"
                                ],
                            },
                        )
                        working_context_retry = None
                    return state
                if tool_call_count + len(decision.tool_calls) > self.max_tool_calls:
                    artifacts.event(
                        run_id=state.run_id,
                        step=state.step,
                        event_type="tool_batch_rejected",
                        details={
                            "reason": "tool_call_limit",
                            "call_ids": [call.call_id for call in decision.tool_calls],
                            "returned_tool_call_count": len(decision.tool_calls),
                            "remaining_tool_call_budget": self.max_tool_calls - tool_call_count,
                            "executed": False,
                        },
                    )
                    self._stop(
                        state,
                        artifacts,
                        "stopped",
                        "tool_call_limit",
                        f"工具调用总数将超过上限 {self.max_tool_calls}。",
                    )
                    return state

                batch_id = f"step-{state.step}"
                batch_results: list[dict[str, Any]] = []
                successful_call_ids: list[str] = []
                artifacts.event(
                    run_id=state.run_id,
                    step=state.step,
                    event_type="tool_batch_started",
                    details={
                        "batch_id": batch_id,
                        "call_ids": [call.call_id for call in decision.tool_calls],
                        "tool_names": [call.name for call in decision.tool_calls],
                    },
                )
                batch_interrupted = False
                for call_index, call in enumerate(decision.tool_calls):
                    tool_call_count += 1
                    state_before_call = copy.deepcopy(state)
                    try:
                        result = self._handle_tool_call(state, call, artifacts)
                    except ToolError as exc:
                        remaining_calls = decision.tool_calls[call_index + 1 :]
                        self._record_unexecuted_calls(
                            state=state,
                            artifacts=artifacts,
                            batch_id=batch_id,
                            calls=remaining_calls,
                            failed_call_id=call.call_id,
                            reason_code=exc.code,
                        )
                        if exc.code not in RECOVERABLE_TOOL_ERROR_CODES:
                            artifacts.event(
                                run_id=state.run_id,
                                step=state.step,
                                event_type="tool_batch_interrupted",
                                details={
                                    "batch_id": batch_id,
                                    "successful_call_ids": successful_call_ids,
                                    "failed_call_id": call.call_id,
                                    "failure_code": exc.code,
                                    "unexecuted_call_ids": [
                                        item.call_id for item in remaining_calls
                                    ],
                                    "partial_side_effects_preserved": bool(
                                        successful_call_ids
                                    ),
                                },
                            )
                            artifacts.save_state(state)
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
                        if call.name == "update_working_context":
                            correction_attempts_used = (
                                int(
                                    working_context_retry.get(
                                        "correction_attempts_used", 0
                                    )
                                )
                                + 1
                                if working_context_retry is not None
                                else 0
                            )
                            if correction_attempts_used < MAX_WORKING_CONTEXT_REVISIONS:
                                working_context_retry = {
                                    "status": "unapplied_proposal",
                                    "source_call_id": call.call_id,
                                    "proposed_arguments": {
                                        "information_need": call.arguments.get(
                                            "information_need"
                                        ),
                                        "operations": copy.deepcopy(
                                            call.arguments.get("operations", [])
                                        ),
                                    },
                                    "error": copy.deepcopy(error_result["error"]),
                                    "correction_attempts_used": correction_attempts_used,
                                    "remaining_correction_attempts": (
                                        MAX_WORKING_CONTEXT_REVISIONS
                                        - correction_attempts_used
                                    ),
                                    "expires_after_step": (
                                        state.step + WORKING_CONTEXT_RETRY_TTL_STEPS
                                    ),
                                    "instructions": [
                                        "本提案尚未应用，Working Context 未发生变化。",
                                        "依据 operation_index 删除或修正坏操作；无需重新读取已在提案中的临床事实。",
                                        "先单独重提合法 finding operations；问题状态迁移放到后续独立调用。",
                                        "若目标 ID 属于 Patient Memory，请在 final.memory_updates 中使用 related_memory_id。",
                                    ],
                                }
                                artifacts.event(
                                    run_id=state.run_id,
                                    step=state.step,
                                    event_type="working_context_retry_saved",
                                    details={
                                        "source_call_id": call.call_id,
                                        "operation_count": len(
                                            working_context_retry[
                                                "proposed_arguments"
                                            ]["operations"]
                                        ),
                                        "error": copy.deepcopy(
                                            error_result["error"]
                                        ),
                                        "correction_attempts_used": correction_attempts_used,
                                        "remaining_correction_attempts": working_context_retry[
                                            "remaining_correction_attempts"
                                        ],
                                        "expires_after_step": working_context_retry[
                                            "expires_after_step"
                                        ],
                                    },
                                )
                            else:
                                artifacts.event(
                                    run_id=state.run_id,
                                    step=state.step,
                                    event_type="working_context_retry_cleared",
                                    details={
                                        "reason": "revision_limit",
                                        "source_call_id": call.call_id,
                                    },
                                )
                                working_context_retry = None
                        batch_results.append(
                            self._recent_result_entry(
                                call=call,
                                result=error_result,
                                batch_id=batch_id,
                                batch_position=call_index + 1,
                                batch_size=len(decision.tool_calls),
                            )
                        )
                        for skipped_index, skipped_call in enumerate(
                            remaining_calls, start=call_index + 2
                        ):
                            skipped_result = {
                                "ok": False,
                                "not_executed": True,
                                "error": {
                                    "code": "batch_aborted_after_error",
                                    "message": (
                                        f"前序调用 {call.call_id} 失败；本调用未执行，"
                                        "不会自动重放。"
                                    ),
                                },
                            }
                            batch_results.append(
                                self._recent_result_entry(
                                    call=skipped_call,
                                    result=skipped_result,
                                    batch_id=batch_id,
                                    batch_position=skipped_index,
                                    batch_size=len(decision.tool_calls),
                                    executed=False,
                                )
                            )
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
                        artifacts.event(
                            run_id=state.run_id,
                            step=state.step,
                            event_type="tool_batch_interrupted",
                            details={
                                "batch_id": batch_id,
                                "successful_call_ids": successful_call_ids,
                                "failed_call_id": call.call_id,
                                "failure_code": exc.code,
                                "unexecuted_call_ids": [
                                    item.call_id for item in remaining_calls
                                ],
                                "partial_side_effects_preserved": bool(
                                    successful_call_ids
                                ),
                            },
                        )
                        omitted_results = self._retain_latest_batch_results(
                            recent_results,
                            batch_results,
                            omitted_results,
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
                        batch_interrupted = True
                        break
                    except RuntimeFailure as exc:
                        remaining_calls = decision.tool_calls[call_index + 1 :]
                        self._record_unexecuted_calls(
                            state=state,
                            artifacts=artifacts,
                            batch_id=batch_id,
                            calls=remaining_calls,
                            failed_call_id=call.call_id,
                            reason_code=exc.code,
                        )
                        artifacts.event(
                            run_id=state.run_id,
                            step=state.step,
                            event_type="tool_batch_interrupted",
                            details={
                                "batch_id": batch_id,
                                "successful_call_ids": successful_call_ids,
                                "failed_call_id": call.call_id,
                                "failure_code": exc.code,
                                "unexecuted_call_ids": [
                                    item.call_id for item in remaining_calls
                                ],
                                "partial_side_effects_preserved": bool(
                                    successful_call_ids
                                ),
                            },
                        )
                        artifacts.save_state(state)
                        raise

                    if (
                        call.name == "update_working_context"
                        and working_context_retry is not None
                    ):
                        artifacts.event(
                            run_id=state.run_id,
                            step=state.step,
                            event_type="working_context_retry_cleared",
                            details={
                                "reason": "corrected_submission_applied",
                                "source_call_id": working_context_retry[
                                    "source_call_id"
                                ],
                                "successful_call_id": call.call_id,
                            },
                        )
                        working_context_retry = None
                    consecutive_recoverable_errors = 0
                    successful_call_ids.append(call.call_id)
                    batch_results.append(
                        self._recent_result_entry(
                            call=call,
                            result=result,
                            batch_id=batch_id,
                            batch_position=call_index + 1,
                            batch_size=len(decision.tool_calls),
                        )
                    )
                    activity = self._activity_view(call, result)
                    activity["step"] = state.step
                    recent_activity.append(activity)
                    if len(recent_activity) > 12:
                        recent_activity.pop(0)
                if not batch_interrupted:
                    omitted_results = self._retain_latest_batch_results(
                        recent_results,
                        batch_results,
                        omitted_results,
                    )
                    artifacts.event(
                        run_id=state.run_id,
                        step=state.step,
                        event_type="tool_batch_completed",
                        details={
                            "batch_id": batch_id,
                            "successful_call_ids": successful_call_ids,
                            "call_count": len(successful_call_ids),
                        },
                    )
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

    def _retain_latest_batch_results(
        self,
        recent_results: list[dict[str, Any]],
        batch_results: list[dict[str, Any]],
        omitted_results: int,
    ) -> int:
        """Keep the whole latest batch even when it is larger than the normal window."""
        recent_results.extend(batch_results)
        target_size = max(self.recent_result_limit, len(batch_results))
        while len(recent_results) > target_size:
            recent_results.pop(0)
            omitted_results += 1
        return omitted_results

    @staticmethod
    def _recent_result_entry(
        *,
        call: ToolCall,
        result: dict[str, Any],
        batch_id: str,
        batch_position: int,
        batch_size: int,
        executed: bool = True,
    ) -> dict[str, Any]:
        prompt_result = _prompt_view(result)
        entry: dict[str, Any] = {
            "call_id": call.call_id,
            "tool_name": call.name,
            "information_need": call.arguments.get("information_need"),
            "result": prompt_result,
            "raw_result_ref": f"tool_calls.jsonl#{call.call_id}" if executed else None,
            "batch_id": batch_id,
            "batch_position": batch_position,
            "batch_size": batch_size,
        }
        if prompt_result.get("truncated_for_prompt"):
            entry["prompt_omission"] = {
                "call_id": call.call_id,
                "record_id": call.arguments.get("record_id"),
                "reason": "individual_tool_result_truncated",
                "full_record_body_included": False,
                "instruction": "完整结果仅在 artifacts；如需正文请重新调用工具。",
            }
        return entry

    @staticmethod
    def _record_unexecuted_calls(
        *,
        state: RunState,
        artifacts: RunArtifacts,
        batch_id: str,
        calls: list[ToolCall],
        failed_call_id: str,
        reason_code: str,
    ) -> None:
        for call in calls:
            artifacts.event(
                run_id=state.run_id,
                step=state.step,
                event_type="tool_call_not_executed",
                details={
                    "batch_id": batch_id,
                    "call_id": call.call_id,
                    "tool_name": call.name,
                    "input": call.arguments,
                    "reason": "batch_aborted_after_error",
                    "failed_call_id": failed_call_id,
                    "failure_code": reason_code,
                    "automatic_retry": False,
                },
            )

    def _build_prompt(
        self,
        *,
        state: RunState,
        task: str,
        memory: dict[str, Any],
        recent_results: list[dict[str, Any]],
        recent_activity: list[dict[str, Any]],
        omitted_results: int,
        tool_call_count: int = 0,
        working_context_retry: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        full_retrieval_catalog = self._retrieval_catalog_view(state)
        selected_catalog = copy.deepcopy(
            full_retrieval_catalog["records"][:PROMPT_RETRIEVAL_CATALOG_LIMIT]
        )
        selected_results = copy.deepcopy(recent_results)
        selected_activity = [
            self._prompt_activity_view(item)
            for item in recent_activity[-PROMPT_ACTIVITY_LIMIT:]
        ]
        trimmed_result_count = 0
        prompt_trimmed_results: list[dict[str, Any]] = []
        prompt_trims: list[dict[str, Any]] = []
        if len(recent_activity) > len(selected_activity):
            prompt_trims.append(
                {
                    "section": "recent_tool_activity",
                    "reason": "bounded_model_window",
                    "removed_count": len(recent_activity) - len(selected_activity),
                }
            )
        budget = PromptBudgetManager(
            max_bytes=self.max_prompt_bytes, tools=self.tools.schemas
        )

        while True:
            explicit_omissions = [
                copy.deepcopy(item["prompt_omission"])
                for item in selected_results
                if isinstance(item.get("prompt_omission"), dict)
            ] + copy.deepcopy(prompt_trimmed_results)
            visible_record_ids = [
                str(item["result"]["record"]["record_id"])
                for item in selected_results
                if item.get("tool_name") == "get_record"
                and isinstance(item.get("result"), dict)
                and isinstance(item["result"].get("record"), dict)
                and item["result"]["record"].get("record_id")
            ]
            explicit_by_record = {
                item.get("record_id"): item
                for item in explicit_omissions
                if item.get("record_id")
            }
            body_omissions: list[dict[str, Any]] = []
            for record_id in state.inspected_record_ids:
                if record_id in visible_record_ids:
                    continue
                if record_id in explicit_by_record:
                    body_omissions.append(copy.deepcopy(explicit_by_record[record_id]))
                else:
                    body_omissions.append(
                        {
                            "record_id": record_id,
                            "reason": "recent_window_evicted",
                            "full_record_body_included": False,
                            "instruction": (
                                "若事实已持久化到 working context 可使用该摘要；"
                                "否则如需正文请重新 get_record。"
                            ),
                        }
                    )
            record_visibility = self._record_visibility_view(
                state, set(visible_record_ids)
            )
            persisted_record_ids = [
                row["record_id"]
                for row in record_visibility
                if row["finding_persisted"]
            ]
            compact_memory = {
                "entries": copy.deepcopy(memory.get("entries", [])),
                "total_entry_count": memory.get("total_entry_count", 0),
                "total_active_count": memory.get("total_active_count", 0),
                "total_inactive_count": memory.get("total_inactive_count", 0),
                "omitted_active_count": memory.get("omitted_active_count", 0),
                "omitted_inactive_count": memory.get("omitted_inactive_count", 0),
                "policy": memory.get("policy"),
            }
            model_catalog = {
                "records": selected_catalog,
                "included_count": len(selected_catalog),
                "total_discovered_count": full_retrieval_catalog[
                    "total_discovered_count"
                ],
                "omitted_count": (
                    full_retrieval_catalog["total_discovered_count"]
                    - len(selected_catalog)
                ),
                "limit": min(
                    full_retrieval_catalog["limit"],
                    PROMPT_RETRIEVAL_CATALOG_LIMIT,
                ),
                "policy": (
                    "这是有界检索索引；short_preview 仅用于选择下一步，不是临床证据。"
                ),
            }
            payload = {
                "task": task,
                "patient_id": state.patient_id,
                "encounter": {
                    "encounter_id": state.encounter_id,
                    "as_of": state.as_of,
                    "availability_policy": (
                        "search_records 与 get_record 只能访问 available_at <= as_of 的记录；"
                        "found=false 不透露该 ID 是否在未来存在。"
                    ),
                },
                "execution_budget": {
                    "current_step": state.step,
                    "max_steps": self.max_steps,
                    "remaining_model_steps_after_this_decision": max(
                        0, self.max_steps - state.step
                    ),
                    "executed_tool_calls": tool_call_count,
                    "max_tool_calls": self.max_tool_calls,
                    "remaining_tool_calls": max(
                        0, self.max_tool_calls - tool_call_count
                    ),
                    "max_tool_calls_per_batch": MAX_TOOL_CALLS_PER_BATCH,
                    "batch_execution_policy": (
                        "若请求数超过 remaining_tool_calls，整批不执行并停止。"
                    ),
                },
                "patient_memory": compact_memory,
                "memory_warning": (
                    "memory 是既往 Agent 工作，不是当前数据库事实；必须读取当前可用记录后"
                    "才能把 remembered statement 当作本次已建立事实。"
                ),
                "id_scopes": {
                    "working_context_active_question_ids": [
                        str(item["entry_id"])
                        for item in state.patient_context.question_states
                        if item.get("status") == "active" and item.get("entry_id")
                    ],
                    "patient_memory_ids": state.loaded_memory_ids,
                    "rules": {
                        "resolve_question": (
                            "只能使用 working_context_active_question_ids；"
                            "不能使用或仿造 memory_id。"
                        ),
                        "resolve_or_supersede_memory": (
                            "只能在 final.memory_updates 中用 related_memory_id 关联 patient_memory_ids。"
                        ),
                    },
                },
                "retrieval_state": {
                    "discovered_record_count": len(state.discovered_record_ids),
                    "inspected_record_ids": state.inspected_record_ids,
                    "patient_record_ids": state.patient_record_ids,
                },
                "retrieval_catalog": model_catalog,
                "patient_context": state.patient_context.to_dict(),
                "context_status": {
                    "fresh_full_record_ids_in_recent_results": visible_record_ids,
                    "inspected_without_visible_body": [
                        record_id
                        for record_id in state.inspected_record_ids
                        if record_id not in visible_record_ids
                    ],
                    "persisted_record_ids": persisted_record_ids,
                    "guidance": (
                        "正文不可见但已 persisted 的事实可使用 working context 摘要；"
                        "未 persisted 的正文若确有必要才重读。可保留未解决问题并形成阶段性 final。"
                    ),
                },
                "stopping_guidance": copy.deepcopy(STOPPING_GUIDANCE),
                "actions": {
                    "proposed": state.proposed_actions,
                    "verified": state.verified_actions,
                },
                "recent_tool_results": selected_results,
                "recent_tool_activity": selected_activity,
                "omitted_prior_tool_result_count": (
                    omitted_results + trimmed_result_count
                ),
                "prompt_policy": (
                    "模型仅接收有界工作集；完整 catalog、visibility、omission、activity 与构造审计"
                    "保存在 artifacts，不会每轮回灌。memory 与 working context 不做静默裁剪。"
                ),
            }
            if working_context_retry is not None:
                payload["working_context_retry"] = copy.deepcopy(
                    working_context_retry
                )
            messages = budget.messages(payload)
            measurement = budget.measure(messages, payload)
            if measurement["total_bytes"] <= self.max_prompt_bytes:
                return messages, {
                    "prompt_bytes": measurement["total_bytes"],
                    "included_recent_result_count": len(selected_results),
                    "omitted_prior_tool_result_count": (
                        omitted_results + trimmed_result_count
                    ),
                    "prompt_omitted_tool_results": body_omissions,
                    "record_visibility": record_visibility,
                    "retrieval_catalog": full_retrieval_catalog,
                    "recent_tool_activity": copy.deepcopy(recent_activity),
                    "patient_memory": copy.deepcopy(memory),
                    "prompt_budget": {
                        **measurement,
                        "protected_sections": [
                            "task",
                            "patient_memory",
                            "patient_context",
                            "execution_budget",
                            "working_context_retry",
                        ],
                        "trims": prompt_trims,
                    },
                }

            if len(selected_activity) > 4:
                removed = selected_activity.pop(0)
                prompt_trims.append(
                    {
                        "section": "recent_tool_activity",
                        "reason": "prompt_size_limit",
                        "tool_name": removed.get("tool_name"),
                        "step": removed.get("step"),
                    }
                )
                continue
            if len(selected_catalog) > 6:
                removed = selected_catalog.pop()
                prompt_trims.append(
                    {
                        "section": "retrieval_catalog",
                        "reason": "prompt_size_limit",
                        "record_id": removed.get("record_id"),
                    }
                )
                continue
            if selected_results:
                removed = selected_results.pop(0)
                result = removed.get("result")
                record_id = None
                if isinstance(removed.get("prompt_omission"), dict):
                    record_id = removed["prompt_omission"].get("record_id")
                if record_id is None and isinstance(result, dict):
                    record = result.get("record")
                    if isinstance(record, dict):
                        record_id = record.get("record_id")
                prompt_trimmed_results.append(
                    {
                        "call_id": removed.get("call_id"),
                        "tool_name": removed.get("tool_name"),
                        "record_id": record_id,
                        "reason": "prompt_size_limit",
                        "full_record_body_included": False,
                        "instruction": "该工具结果当前未包含；如需正文请重新调用工具。",
                    }
                )
                prompt_trims.append(
                    {
                        "section": "recent_tool_results",
                        "reason": "prompt_size_limit",
                        "call_id": removed.get("call_id"),
                        "tool_name": removed.get("tool_name"),
                        "record_id": record_id,
                    }
                )
                trimmed_result_count += 1
                continue
            if selected_activity:
                removed = selected_activity.pop(0)
                prompt_trims.append(
                    {
                        "section": "recent_tool_activity",
                        "reason": "prompt_size_limit",
                        "tool_name": removed.get("tool_name"),
                        "step": removed.get("step"),
                    }
                )
                continue
            if selected_catalog:
                removed = selected_catalog.pop()
                prompt_trims.append(
                    {
                        "section": "retrieval_catalog",
                        "reason": "prompt_size_limit",
                        "record_id": removed.get("record_id"),
                    }
                )
                continue
            raise RuntimeFailure(
                "prompt_context_too_large",
                (
                    "受保护的 task、memory、working context 与 execution budget 加上固定协议"
                    f"仍超过 {self.max_prompt_bytes} bytes；未静默删除受保护内容。"
                ),
            )

    @staticmethod
    def _prompt_activity_view(activity: dict[str, Any]) -> dict[str, Any]:
        compact = copy.deepcopy(activity)
        compact.pop("information_need", None)
        compact.pop("returned_record_ids", None)
        return compact

    @staticmethod
    def _record_visibility_view(
        state: RunState, visible_record_ids: set[str]
    ) -> list[dict[str, Any]]:
        persisted_refs = {
            str(record_id)
            for fact in state.patient_context.known_facts
            for record_id in fact.get("evidence_refs", [])
        }
        ordered_ids = list(
            dict.fromkeys(state.discovered_record_ids + state.inspected_record_ids)
        )
        discovered = set(state.discovered_record_ids)
        inspected = set(state.inspected_record_ids)
        return [
            {
                "record_id": record_id,
                "discovered_through_search": record_id in discovered,
                "inspected_in_full": record_id in inspected,
                "full_content_currently_visible": record_id in visible_record_ids,
                "finding_persisted": record_id in persisted_refs,
                "read_count": state.record_read_counts.get(record_id, 0),
                "last_read_step": state.record_last_read_steps.get(record_id),
            }
            for record_id in ordered_ids
        ]

    def _retrieval_catalog_view(self, state: RunState) -> dict[str, Any]:
        catalog_ids = [row.get("record_id") for row in state.discovered_records]
        if (
            catalog_ids != state.discovered_record_ids
            or len(catalog_ids) != len(set(catalog_ids))
            or not all(isinstance(record_id, str) and record_id for record_id in catalog_ids)
        ):
            raise RuntimeFailure(
                "retrieval_catalog_inconsistent",
                "discovered_records 与 discovered_record_ids 不一致。",
            )

        selected = state.discovered_records[: self.retrieval_catalog_limit]
        inspected = set(state.inspected_record_ids)
        prompt_records: list[dict[str, Any]] = []
        for record in selected:
            preview = record.get("preview")
            preview_text = preview if isinstance(preview, str) else ""
            if len(preview_text) > self.retrieval_preview_chars:
                preview_text = preview_text[: self.retrieval_preview_chars - 1] + "…"
            prompt_records.append(
                {
                    "record_id": record.get("record_id"),
                    "record_type": record.get("record_type"),
                    "clinical_time": record.get("clinical_time"),
                    "available_at": record.get("available_at"),
                    "title": record.get("title"),
                    "status": record.get("status"),
                    "exam_type": record.get("exam_type"),
                    "workflow_id": record.get("workflow_id"),
                    "workflow_stage": record.get("workflow_stage"),
                    "short_preview": preview_text,
                    "inspected": record.get("record_id") in inspected,
                }
            )
        return {
            "records": prompt_records,
            "included_count": len(prompt_records),
            "total_discovered_count": len(state.discovered_records),
            "omitted_count": len(state.discovered_records) - len(prompt_records),
            "limit": self.retrieval_catalog_limit,
            "policy": (
                "仅包含 search_records 已实际返回的紧凑索引元数据；short_preview 不是临床证据，"
                "只有 get_record 后 inspected=true 的记录才可支持正式事实。"
            ),
        }

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
                        for key in (
                            "record_type",
                            "keyword",
                            "exam_type",
                            "workflow_id",
                            "from_time",
                            "to_time",
                            "limit",
                            "cursor",
                        )
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
            activity["update_mode"] = result.get("update_mode")
            activity["applied_operations"] = copy.deepcopy(
                result.get("applied_operations", [])
            )
            activity["context_counts"] = {
                "known_facts": len(context.get("known_facts", [])),
                "unresolved_questions": len(context.get("unresolved_questions", [])),
                "question_states": len(context.get("question_states", [])),
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
                if not isinstance(record_id, str) or not record_id:
                    continue
                compact_record = {
                    field: copy.deepcopy(record.get(field))
                    for field in RETRIEVAL_CATALOG_INDEX_FIELDS
                }
                if record_id in state.discovered_record_ids:
                    index = state.discovered_record_ids.index(record_id)
                    state.discovered_records[index] = compact_record
                else:
                    state.discovered_record_ids.append(record_id)
                    state.discovered_records.append(compact_record)
        elif call.name == "get_record" and result.get("record"):
            record_id = result["record"].get("record_id")
            if record_id:
                if record_id not in state.inspected_record_ids:
                    state.inspected_record_ids.append(record_id)
                state.record_read_counts[record_id] = (
                    state.record_read_counts.get(record_id, 0) + 1
                )
                state.record_last_read_steps[record_id] = state.step
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
        required = {"final_text", "run_summary", "evidence_refs"}
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
        updates = value.get("memory_updates", [])
        prior_ids = {row.get("memory_id") for row in prior_memory}
        validated_updates = []
        rejected_update_count = 0
        if not isinstance(updates, list):
            rejected_update_count += 1
            artifacts.event(
                run_id=state.run_id,
                step=state.step,
                event_type="memory_update_rejected",
                details={
                    "index": None,
                    "reason": "invalid_final",
                    "message": "memory_updates 必须是列表；主 final 保留，附属 memory 全部跳过。",
                },
            )
            updates = []
        for index, update in enumerate(updates):
            try:
                if not isinstance(update, dict):
                    raise RuntimeFailure(
                        "invalid_final", f"memory_updates[{index}] 必须是对象"
                    )
                kind = update.get("kind")
                text = update.get("text")
                status = update.get("status", "active")
                refs = _string_list(
                    update.get("evidence_refs"),
                    f"memory_updates[{index}].evidence_refs",
                )
                if kind not in {"prior_work", "unresolved", "next_step"}:
                    raise RuntimeFailure(
                        "invalid_final", f"memory_updates[{index}].kind 无效"
                    )
                if status not in {"active", "resolved", "superseded"}:
                    raise RuntimeFailure(
                        "invalid_final", f"memory_updates[{index}].status 无效"
                    )
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeFailure(
                        "invalid_final", f"memory_updates[{index}].text 不能为空"
                    )
                unavailable_refs = sorted(set(refs) - allowed)
                if unavailable_refs:
                    raise RuntimeFailure(
                        "invalid_final_evidence",
                        "memory update 引用了未读取全文或未验证的 ID："
                        f"{', '.join(unavailable_refs)}",
                    )
                related = update.get("related_memory_id")
                if status in {"resolved", "superseded"} and not related:
                    raise RuntimeFailure(
                        "invalid_final",
                        f"memory_updates[{index}] 状态为 {status} 时必须提供 related_memory_id",
                    )
                if related is not None and related not in prior_ids:
                    raise RuntimeFailure(
                        "invalid_final",
                        f"memory_updates[{index}].related_memory_id 不在已加载 memory 中",
                    )
            except RuntimeFailure as exc:
                rejected_update_count += 1
                artifacts.event(
                    run_id=state.run_id,
                    step=state.step,
                    event_type="memory_update_rejected",
                    details={
                        "index": index,
                        "reason": exc.code,
                        "message": exc.message,
                    },
                )
                continue
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
                "memory_updates_rejected": rejected_update_count,
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
