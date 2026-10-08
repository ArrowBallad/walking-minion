from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import PatientContext, RunState
from .storage import MockDatabase


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


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
                "用完整快照更新本次任务的有限 patient working context。known_facts 和 conflicts "
                "只能引用已经 get_record 读取全文的记录；它不是患者数据库。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "information_need": {"type": "string"},
                    **_context_properties(),
                },
                "required": [
                    "patient_id",
                    "information_need",
                    "known_facts",
                    "unresolved_questions",
                    "conflicts",
                    "relevant_record_refs",
                ],
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
        validated_facts.append(dict(fact))

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
        conflicts=validated_conflicts,
        relevant_record_refs=list(dict.fromkeys(relevant)),
    )


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
            for field in ("record_type", "keyword", "from_time", "to_time", "cursor"):
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
            context = _validate_context(arguments, state)
            return {"ok": True, "patient_context": context.to_dict()}

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

