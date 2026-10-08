from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import RunState
from .storage import MockDatabase


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_patient",
            "description": "按 patient_id 读取一名合成患者的基本信息。",
            "parameters": {
                "type": "object",
                "properties": {"patient_id": {"type": "string"}},
                "required": ["patient_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_records",
            "description": "检索合成临床记录；结果保留记录 ID 和临床发生时间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "record_type": {"type": "string"},
                    "from_time": {"type": "string"},
                    "to_time": {"type": "string"},
                },
                "required": ["patient_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "创建一个可重置的合成订单；返回的 receipt 不代表已经验证。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "order_type": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["patient_id", "order_type", "reason", "evidence_refs"],
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
                    "order_id": {"type": "string"},
                },
                "required": ["patient_id", "order_id"],
                "additionalProperties": False,
            },
        },
    },
]


_FIELDS = {
    "get_patient": ({"patient_id"}, {"patient_id"}),
    "search_records": (
        {"patient_id"},
        {"patient_id", "record_type", "from_time", "to_time"},
    ),
    "create_order": (
        {"patient_id", "order_type", "reason", "evidence_refs"},
        {"patient_id", "order_type", "reason", "evidence_refs"},
    ),
    "get_order": ({"patient_id", "order_id"}, {"patient_id", "order_id"}),
}


def _parse_time(value: str, field: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError("invalid_arguments", f"{field} 必须是 ISO 8601 时间戳") from exc


class ToolRegistry:
    def __init__(self, database: MockDatabase) -> None:
        self.database = database

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return TOOL_SCHEMAS

    def execute(self, name: str, arguments: dict[str, Any], state: RunState) -> dict[str, Any]:
        if name not in _FIELDS:
            raise ToolError("unknown_tool", f"未知工具：{name}")
        required, allowed = _FIELDS[name]
        missing = sorted(required - arguments.keys())
        unknown = sorted(arguments.keys() - allowed)
        if missing:
            raise ToolError("invalid_arguments", f"缺少字段：{', '.join(missing)}")
        if unknown:
            raise ToolError("invalid_arguments", f"未知字段：{', '.join(unknown)}")
        if not all(isinstance(arguments[key], str) for key in arguments if key != "evidence_refs"):
            raise ToolError("invalid_arguments", "所有标量参数都必须是字符串")
        if arguments["patient_id"] != state.patient_id:
            raise ToolError("patient_mismatch", "工具参数中的 patient_id 与当前绑定患者不一致")

        if name == "get_patient":
            patient = self.database.get_patient(state.patient_id)
            return {"ok": True, "found": patient is not None, "patient": patient}

        if name == "search_records":
            if "from_time" in arguments:
                _parse_time(arguments["from_time"], "from_time")
            if "to_time" in arguments:
                _parse_time(arguments["to_time"], "to_time")
            records = self.database.search_records(
                state.patient_id,
                record_type=arguments.get("record_type"),
                from_time=arguments.get("from_time"),
                to_time=arguments.get("to_time"),
            )
            return {"ok": True, "count": len(records), "records": records}

        if name == "create_order":
            evidence_refs = arguments["evidence_refs"]
            if not isinstance(evidence_refs, list) or not all(
                isinstance(value, str) for value in evidence_refs
            ):
                raise ToolError("invalid_arguments", "evidence_refs 必须是字符串列表")
            unavailable = sorted(set(evidence_refs) - set(state.retrieved_record_ids))
            if unavailable:
                raise ToolError(
                    "invalid_evidence_refs",
                    f"以下证据未在本次运行中检索：{', '.join(unavailable)}",
                )
            receipt, order = self.database.create_order(
                patient_id=state.patient_id,
                order_type=arguments["order_type"],
                reason=arguments["reason"],
                evidence_refs=evidence_refs,
            )
            return {"ok": True, "receipt": receipt, "order": order}

        order = self.database.get_order(state.patient_id, arguments["order_id"])
        return {"ok": True, "found": order is not None, "order": order}
