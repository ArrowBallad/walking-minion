from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clinical_agent_explorer_phase1.runtime import (
    RUNTIME_VERSION,
    STOPPING_GUIDANCE,
    SYSTEM_PROMPT,
)

from support import ScriptedClient, make_runtime
from test_runtime_flow import final


class StoppingContractTests(unittest.TestCase):
    def test_generic_stopping_contract_is_present_in_system_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedClient([final()])
            runtime, _, _ = make_runtime(Path(directory), client)
            state = runtime.run(
                patient_id="P1", task="审阅当前问题。", case_id="test", run_id="stopping"
            )

            self.assertEqual("complete", state.status)
            self.assertEqual("phase1-v4-retrieval-awareness", RUNTIME_VERSION)
            self.assertIn("不需要解决所有 unresolved questions 才能结束", SYSTEM_PROMPT)
            self.assertIn("应优先结束并生成 final", SYSTEM_PROMPT)
            self.assertIn("可能实质改变当前 assessment", SYSTEM_PROMPT)
            self.assertIn("index 中存在未读记录，不等于必须全部读取", SYSTEM_PROMPT)
            self.assertIn("has_more=true 不意味着必须翻完所有页面", SYSTEM_PROMPT)
            self.assertIn("准备 final 前检查 persistent retrieval catalog", SYSTEM_PROMPT)
            self.assertIn("不会影响 task completion", SYSTEM_PROMPT)
            self.assertIn("runtime 不计算 clinical sufficiency", SYSTEM_PROMPT)

            payload = json.loads(client.contexts[0][1]["content"])
            self.assertEqual(STOPPING_GUIDANCE, payload["stopping_guidance"])
            self.assertEqual(
                {
                    "finish_when_core_answer_supported",
                    "unresolved_questions_may_remain",
                    "continue_only_if_material",
                    "unread_index_is_not_obligation",
                    "pagination_is_need_driven",
                    "catalog_check_before_final",
                    "decision_owner",
                },
                set(payload["stopping_guidance"]),
            )
            self.assertTrue(
                all(isinstance(value, str) and value for value in STOPPING_GUIDANCE.values())
            )


if __name__ == "__main__":
    unittest.main()
