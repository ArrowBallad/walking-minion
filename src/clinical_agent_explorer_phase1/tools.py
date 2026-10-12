from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from .models import PatientContext, RunState
from .storage import MockDatabase


class ToolError(Exception):
    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = copy.deepcopy(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                **copy.deepcopy(self.details),
                "message": self.message,
            },
        }


def _context_properties() -> dict[str, Any]:
    return {
        "known_facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "clinical_time": {"type": ["string", "null"]},
                    "temporal_status": {
                        "type": "string",
                        "enum": ["current", "historical", "superseded", "unknown"],
                    },
                },
                "required": [
                    "statement",
                    "evidence_refs",
                    "clinical_time",
                    "temporal_status",
                ],
                "additionalProperties": False,
            },
        },
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["statement", "evidence_refs"],
                "additionalProperties": False,
            },
        },
        "relevant_record_refs": {"type": "array", "items": {"type": "string"}},
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_patient",
            "description": "读取合成患者基本信息。information_need 用一句中文说明本次读取要补充什么。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "information_need": {"type": "string"},
                },
                "required": ["patient_id", "information_need"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_records",
            "description": (
                "检索记录索引，只返回 ID、类型、临床时间、标题、短预览和状态，不返回全文。"
                "可按类型、关键词和时间过滤，并使用 cursor 继续分页。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "information_need": {"type": "string"},
                    "record_type": {"type": "string"},
                    "keyword": {"type": "string"},
                    "exam_type": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "from_time": {"type": "string"},
                    "to_time": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "cursor": {"type": "string"},
                },
                "required": ["patient_id", "information_need"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_record",
            "description": "按 record_id 读取一条完整临床记录。重要临床事实必须先读取全文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "record_id": {"type": "string"},
                    "information_need": {"type": "string"},
                },
                "required": ["patient_id", "record_id", "information_need"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_working_context",
            "description": (
                "对本次任务的有限 working context 做增量更新。add/update/supersede finding "
                "以及 add/resolve question 都使用稳定 entry_id；未涉及的条目保持不变。"
                "resolve_question 只能使用当前 patient_context.question_states 中真实存在的 "
                "working-context entry_id，不能使用 patient memory 的 memory_id。"
                "为隔离失败风险，应先单独提交 add_finding，再在后续调用中提交问题状态迁移；"
                "不要在同一次提交中混合新事实与 resolve_question。"
                "事实和问题解决证据只能引用本次运行已 get_record 的记录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "information_need": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": [
                                        "add_finding",
                                        "update_finding",
                                        "supersede_finding",
                                        "add_question",
                                        "resolve_question",
                                    ],
                                },
                                "entry_id": {"type": "string"},
                                "replacement_entry_id": {"type": "string"},
                                "statement": {"type": "string"},
                                "text": {"type": "string"},
                                "resolution": {"type": "string"},
                                "evidence_refs": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "clinical_time": {"type": ["string", "null"]},
                                "temporal_status": {
                                    "type": "string",
                                    "enum": ["current", "historical", "superseded", "unknown"],
                                },
                            },
                            "required": ["op", "entry_id"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["patient_id", "information_need", "operations"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "创建可重置的合成订单；receipt 不等于验证成功。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "information_need": {"type": "string"},
                    "order_type": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "patient_id",
                    "information_need",
                    "order_type",
                    "reason",
                    "evidence_refs",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "按订单 ID 独立读回一条合成订单。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "information_need": {"type": "string"},
                    "order_id": {"type": "string"},
                },
                "required": ["patient_id", "order_id", "information_need"],
                "additionalProperties": False,
            },
        },
    },
]


_ALLOWED_FIELDS = {
    "get_patient": {"patient_id", "information_need"},
    "search_records": {
        "patient_id",
        "information_need",
        "record_type",
        "keyword",
        "exam_type",
        "workflow_id",
        "from_time",
        "to_time",
        "limit",
        "cursor",
    },
    "get_record": {"patient_id", "record_id", "information_need"},
    "update_working_context": {
        "patient_id",
        "information_need",
        "known_facts",
        "unresolved_questions",
        "conflicts",
        "relevant_record_refs",
        "operations",
    },
    "create_order": {
        "patient_id",
        "information_need",
        "order_type",
        "reason",
        "evidence_refs",
    },
    "get_order": {"patient_id", "order_id", "information_need"},
}


def _parse_time(value: str, field: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError("invalid_arguments", f"{field} 必须是 ISO 8601 时间戳") from exc


def _string_list(value: Any, field: str, *, maximum: int) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolError("invalid_arguments", f"{field} 必须是字符串列表")
    if len(value) > maximum:
        raise ToolError("context_limit", f"{field} 最多允许 {maximum} 项")
    if any(not item.strip() or len(item) > 500 for item in value):
        raise ToolError("context_limit", f"{field} 含空字符串或超过 500 字符的条目")
    return value


def _validate_context(arguments: dict[str, Any], state: RunState) -> PatientContext:
    facts = arguments["known_facts"]
    conflicts = arguments["conflicts"]
    questions = _string_list(arguments["unresolved_questions"], "unresolved_questions", maximum=20)
    relevant = _string_list(arguments["relevant_record_refs"], "relevant_record_refs", maximum=30)
    if not isinstance(facts, list) or len(facts) > 20:
        raise ToolError("context_limit", "known_facts 必须是最多 20 项的列表")
    if not isinstance(conflicts, list) or len(conflicts) > 10:
        raise ToolError("context_limit", "conflicts 必须是最多 10 项的列表")

    inspected = set(state.inspected_record_ids)
    discovered = set(state.discovered_record_ids) | inspected
    validated_facts = []
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            raise ToolError("invalid_arguments", f"known_facts[{index}] 必须是对象")
        if set(fact) != {"statement", "evidence_refs", "clinical_time", "temporal_status"}:
            raise ToolError("invalid_arguments", f"known_facts[{index}] 字段不完整或含未知字段")
        statement = fact["statement"]
        refs = fact["evidence_refs"]
        clinical_time = fact["clinical_time"]
        temporal_status = fact["temporal_status"]
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 500:
            raise ToolError("context_limit", f"known_facts[{index}].statement 无效")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
            raise ToolError("invalid_arguments", f"known_facts[{index}].evidence_refs 无效")
        unavailable = sorted(set(refs) - inspected)
        if unavailable:
            raise ToolError(
                "uninspected_context_ref",
                f"known_facts[{index}] 引用了未读取全文的记录：{', '.join(unavailable)}",
            )
        if clinical_time is not None and not isinstance(clinical_time, str):
            raise ToolError("invalid_arguments", f"known_facts[{index}].clinical_time 无效")
        if temporal_status not in {"current", "historical", "superseded", "unknown"}:
            raise ToolError("invalid_arguments", f"known_facts[{index}].temporal_status 无效")
        validated_facts.append({"entry_id": f"legacy-finding-{index + 1:03d}", **fact})

    validated_conflicts = []
    for index, conflict in enumerate(conflicts):
        if not isinstance(conflict, dict) or set(conflict) != {"statement", "evidence_refs"}:
            raise ToolError("invalid_arguments", f"conflicts[{index}] 字段无效")
        statement = conflict["statement"]
        refs = conflict["evidence_refs"]
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 500:
            raise ToolError("context_limit", f"conflicts[{index}].statement 无效")
        if not isinstance(refs, list) or len(refs) < 2 or not all(isinstance(ref, str) for ref in refs):
            raise ToolError("invalid_arguments", f"conflicts[{index}] 至少需要两个 evidence_refs")
        unavailable = sorted(set(refs) - inspected)
        if unavailable:
            raise ToolError(
                "uninspected_context_ref",
                f"conflicts[{index}] 引用了未读取全文的记录：{', '.join(unavailable)}",
            )
        validated_conflicts.append(dict(conflict))

    unknown_relevant = sorted(set(relevant) - discovered)
    if unknown_relevant:
        raise ToolError(
            "unknown_context_ref",
            f"relevant_record_refs 含未发现的记录：{', '.join(unknown_relevant)}",
        )
    return PatientContext(
        known_facts=validated_facts,
        unresolved_questions=questions,
        question_states=[
            {
                "entry_id": f"legacy-question-{index + 1:03d}",
                "text": question,
                "status": "active",
                "evidence_refs": [],
            }
            for index, question in enumerate(questions)
        ],
        conflicts=validated_conflicts,
        relevant_record_refs=list(dict.fromkeys(relevant)),
    )


def _validate_entry_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ToolError("invalid_arguments", f"{field} 必须是 1–100 字符的非空字符串")
    return value


def _validate_incremental_refs(
    value: Any,
    *,
    field: str,
    inspected: set[str],
    allow_empty: bool,
) -> list[str]:
    refs = _string_list(value, field, maximum=12)
    if not allow_empty and not refs:
        raise ToolError("invalid_arguments", f"{field} 不能为空")
    unavailable = sorted(set(refs) - inspected)
    if unavailable:
        raise ToolError(
            "uninspected_context_ref",
            f"{field} 引用了未读取全文的记录：{', '.join(unavailable)}",
        )
    return list(dict.fromkeys(refs))


def _validate_finding_fields(
    operation: dict[str, Any], *, inspected: set[str], prefix: str
) -> dict[str, Any]:
    statement = operation.get("statement")
    if not isinstance(statement, str) or not statement.strip() or len(statement) > 500:
        raise ToolError("context_limit", f"{prefix}.statement 无效")
    refs = _validate_incremental_refs(
        operation.get("evidence_refs"),
        field=f"{prefix}.evidence_refs",
        inspected=inspected,
        allow_empty=False,
    )
    clinical_time = operation.get("clinical_time")
    if clinical_time is not None and not isinstance(clinical_time, str):
        raise ToolError("invalid_arguments", f"{prefix}.clinical_time 无效")
    temporal_status = operation.get("temporal_status")
    if temporal_status not in {"current", "historical", "superseded", "unknown"}:
        raise ToolError("invalid_arguments", f"{prefix}.temporal_status 无效")
    return {
        "statement": statement,
        "evidence_refs": refs,
        "clinical_time": clinical_time,
        "temporal_status": temporal_status,
    }


def _apply_incremental_context_operation(
    *,
    context: PatientContext,
    operation: Any,
    index: int,
    inspected: set[str],
    resolvable_question_ids: set[str],
) -> dict[str, Any]:
    prefix = f"operations[{index}]"
    if not isinstance(operation, dict):
        raise ToolError("invalid_arguments", f"{prefix} 必须是对象")
    op = operation.get("op")
    entry_id = _validate_entry_id(operation.get("entry_id"), f"{prefix}.entry_id")
    fact_index = next(
        (
            item_index
            for item_index, item in enumerate(context.known_facts)
            if item.get("entry_id") == entry_id
        ),
        None,
    )
    question_index = next(
        (
            item_index
            for item_index, item in enumerate(context.question_states)
            if item.get("entry_id") == entry_id
        ),
        None,
    )

    if op == "add_finding":
        if fact_index is not None or question_index is not None:
            raise ToolError("invalid_arguments", f"entry_id 已存在：{entry_id}")
        fields = _validate_finding_fields(
            operation, inspected=inspected, prefix=prefix
        )
        context.known_facts.append({"entry_id": entry_id, **fields})
    elif op == "update_finding":
        if fact_index is None:
            raise ToolError("invalid_arguments", f"未知 finding entry_id：{entry_id}")
        fields = _validate_finding_fields(
            operation, inspected=inspected, prefix=prefix
        )
        context.known_facts[fact_index] = {"entry_id": entry_id, **fields}
    elif op == "supersede_finding":
        if fact_index is None:
            raise ToolError("invalid_arguments", f"未知 finding entry_id：{entry_id}")
        replacement_id = _validate_entry_id(
            operation.get("replacement_entry_id"),
            f"{prefix}.replacement_entry_id",
        )
        if any(
            item.get("entry_id") == replacement_id for item in context.known_facts
        ):
            raise ToolError(
                "invalid_arguments",
                f"replacement_entry_id 已存在：{replacement_id}",
            )
        fields = _validate_finding_fields(
            operation, inspected=inspected, prefix=prefix
        )
        context.known_facts[fact_index]["temporal_status"] = "superseded"
        context.known_facts.append(
            {"entry_id": replacement_id, "supersedes": entry_id, **fields}
        )
    elif op == "add_question":
        if fact_index is not None or question_index is not None:
            raise ToolError("invalid_arguments", f"entry_id 已存在：{entry_id}")
        text = operation.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ToolError("context_limit", f"{prefix}.text 无效")
        refs = _validate_incremental_refs(
            operation.get("evidence_refs", []),
            field=f"{prefix}.evidence_refs",
            inspected=inspected,
            allow_empty=True,
        )
        context.question_states.append(
            {
                "entry_id": entry_id,
                "text": text,
                "status": "active",
                "evidence_refs": refs,
            }
        )
    elif op == "resolve_question":
        if entry_id not in resolvable_question_ids or question_index is None:
            raise ToolError(
                "unknown_working_question_id",
                f"当前 working context 中不存在可解决的 question：{entry_id}",
            )
        if context.question_states[question_index].get("status") != "active":
            raise ToolError(
                "inactive_working_question_id",
                f"working context question 已非 active：{entry_id}",
            )
        resolution = operation.get("resolution")
        if not isinstance(resolution, str) or not resolution.strip() or len(resolution) > 500:
            raise ToolError("context_limit", f"{prefix}.resolution 无效")
        refs = _validate_incremental_refs(
            operation.get("evidence_refs"),
            field=f"{prefix}.evidence_refs",
            inspected=inspected,
            allow_empty=False,
        )
        context.question_states[question_index].update(
            {
                "status": "resolved",
                "resolution": resolution,
                "resolution_evidence_refs": refs,
            }
        )
    else:
        raise ToolError("invalid_arguments", f"{prefix}.op 无效")
    return {"op": op, "entry_id": entry_id}


def _validate_incremental_context(
    arguments: dict[str, Any], state: RunState
) -> tuple[PatientContext, list[dict[str, Any]]]:
    operations = arguments.get("operations")
    if not isinstance(operations, list) or not operations or len(operations) > 12:
        raise ToolError("context_limit", "operations 必须是 1–12 项的列表")
    context = copy.deepcopy(state.patient_context)
    inspected = set(state.inspected_record_ids)
    valid_question_ids = [
        str(item["entry_id"])
        for item in state.patient_context.question_states
        if item.get("status") == "active" and item.get("entry_id")
    ]
    applied: list[dict[str, Any]] = []

    for index, operation in enumerate(operations):
        try:
            applied.append(
                _apply_incremental_context_operation(
                    context=context,
                    operation=operation,
                    index=index,
                    inspected=inspected,
                    resolvable_question_ids=set(valid_question_ids),
                )
            )
        except ToolError as exc:
            target_id = (
                operation.get("entry_id") if isinstance(operation, dict) else None
            )
            code = exc.code
            message = exc.message
            scope_guidance = (
                "resolve_question 只接受当前 working context 的 active question entry_id。"
            )
            if (
                isinstance(operation, dict)
                and operation.get("op") == "resolve_question"
                and target_id in state.loaded_memory_ids
            ):
                code = "id_scope_mismatch"
                message = (
                    f"{target_id} 是 patient memory_id，不是 working context question_id；"
                    "请在 final.memory_updates 中用 related_memory_id 关联它。"
                )
                scope_guidance = (
                    "Patient Memory 只能通过 final.memory_updates.related_memory_id "
                    "写入 resolved/superseded transition。"
                )
            raise ToolError(
                code,
                message,
                details={
                    **exc.details,
                    "operation_index": index,
                    "target_id": target_id,
                    "valid_question_ids": valid_question_ids,
                    "applied": False,
                    "validated_prior_operation_count": len(applied),
                    "scope_guidance": scope_guidance,
                },
            ) from exc

    if len(context.known_facts) > 20:
        raise ToolError("context_limit", "known_facts 最多允许 20 项")
    if len(context.question_states) > 20:
        raise ToolError("context_limit", "question_states 最多允许 20 项")
    context.unresolved_questions = [
        str(item["text"])
        for item in context.question_states
        if item.get("status") == "active"
    ]
    refs: list[str] = []
    for fact in context.known_facts:
        refs.extend(fact.get("evidence_refs", []))
    for conflict in context.conflicts:
        refs.extend(conflict.get("evidence_refs", []))
    for question in context.question_states:
        refs.extend(question.get("evidence_refs", []))
        refs.extend(question.get("resolution_evidence_refs", []))
    context.relevant_record_refs = list(dict.fromkeys(refs))
    if len(context.relevant_record_refs) > 30:
        raise ToolError("context_limit", "relevant_record_refs 最多允许 30 项")
    return context, applied


class ToolRegistry:
    def __init__(self, database: MockDatabase) -> None:
        self.database = database

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return TOOL_SCHEMAS

    def execute(self, name: str, arguments: dict[str, Any], state: RunState) -> dict[str, Any]:
        if name not in _ALLOWED_FIELDS:
            raise ToolError("unknown_tool", f"未知工具：{name}")
        if not isinstance(arguments, dict):
            raise ToolError("invalid_arguments", "工具参数必须是对象")
        unknown = sorted(set(arguments) - _ALLOWED_FIELDS[name])
        if unknown:
            raise ToolError("invalid_arguments", f"未知字段：{', '.join(unknown)}")
        for required in ("patient_id", "information_need"):
            if required not in arguments:
                raise ToolError("invalid_arguments", f"缺少字段：{required}")
        if arguments["patient_id"] != state.patient_id:
            raise ToolError("patient_mismatch", "patient_id 与当前绑定患者不一致")
        information_need = arguments["information_need"]
        if not isinstance(information_need, str) or not information_need.strip() or len(information_need) > 500:
            raise ToolError("invalid_arguments", "information_need 必须是 1–500 字符的非空字符串")

        if name == "get_patient":
            patient = self.database.get_patient(state.patient_id)
            return {"ok": True, "found": patient is not None, "patient": patient}

        if name == "search_records":
            for field in (
                "record_type",
                "keyword",
                "exam_type",
                "workflow_id",
                "from_time",
                "to_time",
                "cursor",
            ):
                if field in arguments and not isinstance(arguments[field], str):
                    raise ToolError("invalid_arguments", f"{field} 必须是字符串")
            if "from_time" in arguments:
                _parse_time(arguments["from_time"], "from_time")
            if "to_time" in arguments:
                _parse_time(arguments["to_time"], "to_time")
            limit = arguments.get("limit", 10)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
                raise ToolError("invalid_arguments", "limit 必须是 1–20 的整数")
            cursor = arguments.get("cursor")
            if cursor is not None and (not cursor.isdigit() or int(cursor) < 0):
                raise ToolError("invalid_arguments", "cursor 必须是非负整数字符串")
            page = self.database.search_records(
                state.patient_id,
                record_type=arguments.get("record_type"),
                keyword=arguments.get("keyword"),
                exam_type=arguments.get("exam_type"),
                workflow_id=arguments.get("workflow_id"),
                from_time=arguments.get("from_time"),
                to_time=arguments.get("to_time"),
                limit=limit,
                cursor=cursor,
            )
            return {"ok": True, **page}

        if name == "get_record":
            record_id = arguments.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                raise ToolError("invalid_arguments", "record_id 必须是非空字符串")
            record = self.database.get_record(state.patient_id, record_id)
            return {"ok": True, "found": record is not None, "record": record}

        if name == "update_working_context":
            if "operations" in arguments:
                context, applied = _validate_incremental_context(arguments, state)
                return {
                    "ok": True,
                    "update_mode": "incremental",
                    "applied_operations": applied,
                    "patient_context": context.to_dict(),
                }
            legacy_required = {
                "known_facts",
                "unresolved_questions",
                "conflicts",
                "relevant_record_refs",
            }
            missing = sorted(legacy_required - arguments.keys())
            if missing:
                raise ToolError(
                    "invalid_arguments",
                    f"缺少 operations，且 legacy snapshot 缺少字段：{', '.join(missing)}",
                )
            context = _validate_context(arguments, state)
            return {
                "ok": True,
                "update_mode": "legacy_snapshot",
                "applied_operations": [],
                "patient_context": context.to_dict(),
            }

        if name == "create_order":
            for field in ("order_type", "reason"):
                if not isinstance(arguments.get(field), str) or not arguments[field].strip():
                    raise ToolError("invalid_arguments", f"{field} 必须是非空字符串")
            evidence_refs = arguments.get("evidence_refs")
            if not isinstance(evidence_refs, list) or not all(
                isinstance(value, str) for value in evidence_refs
            ):
                raise ToolError("invalid_arguments", "evidence_refs 必须是字符串列表")
            unavailable = sorted(set(evidence_refs) - set(state.inspected_record_ids))
            if unavailable:
                raise ToolError(
                    "uninspected_evidence_refs",
                    f"写入引用了未读取全文的记录：{', '.join(unavailable)}",
                )
            receipt, order = self.database.create_order(
                patient_id=state.patient_id,
                order_type=arguments["order_type"],
                reason=arguments["reason"],
                evidence_refs=evidence_refs,
            )
            return {"ok": True, "receipt": receipt, "order": order}

        order_id = arguments.get("order_id")
        if not isinstance(order_id, str) or not order_id:
            raise ToolError("invalid_arguments", "order_id 必须是非空字符串")
        order = self.database.get_order(state.patient_id, order_id)
        return {"ok": True, "found": order is not None, "order": order}
