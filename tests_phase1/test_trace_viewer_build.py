from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_trace_viewer.py"
SPEC = importlib.util.spec_from_file_location("build_trace_viewer", SCRIPT)
assert SPEC and SPEC.loader
VIEWER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VIEWER)


class TraceViewerBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = ROOT / "runs" / "gastro_demo_multi_patient" / "experiment_manifest.json"
        cls.annotations = ROOT / "viewer" / "gastro_demo_annotations.json"
        cls.payload = VIEWER.build_payload(ROOT, cls.manifest, cls.annotations)

    def test_selected_experiment_contains_five_patients_and_all_fifteen_runs(self) -> None:
        selected = [run for run in self.payload["runs"] if run["selected_track"]]
        self.assertEqual(5, len(self.payload["cases"]))
        self.assertEqual(15, len(selected))
        self.assertEqual(15, len(self.payload["summary"]))
        stopped = [run for run in selected if run["state"].get("status") == "stopped"]
        self.assertEqual(["p1-run-230628957c6f"], [run["run_id"] for run in stopped])

    def test_highlights_point_to_real_artifacts_and_preserve_information_loss(self) -> None:
        highlights = {item["id"]: item for item in self.payload["highlights"]}
        self.assertEqual({"gi002-success-path", "gi005-workflow-state", "gi004-information-loss"}, set(highlights))
        run = next(run for run in self.payload["runs"] if run["run_id"] == "p1-run-c64101d39b1f")
        read = next(call for call in run["tool_calls"] if call["call_id"] == "call_00_Cx8O41E9ehdVImVnGcvN5878")
        self.assertEqual("gi-demo-004-rec-018", read["input"]["record_id"])
        self.assertIn("48", json.dumps(read["output"], ensure_ascii=False))
        step_five = next(snapshot for snapshot in run["prompt_snapshots"] if snapshot["step"] == 5)
        rec_018 = next(row for row in step_five["record_visibility"] if row["record_id"] == "gi-demo-004-rec-018")
        self.assertTrue(rec_018["inspected_in_full"])
        self.assertFalse(rec_018["full_content_currently_visible"])
        self.assertNotIn("gi-demo-004-rec-018", run["state"]["final_evidence_refs"])

    def test_history_is_separate_and_html_serialization_is_script_safe(self) -> None:
        history = [run for run in self.payload["runs"] if not run["selected_track"]]
        self.assertGreaterEqual(len(history), 7)
        self.assertTrue(all(run["track_label"].startswith("历史运行") for run in history))
        self.assertTrue(any(run["memory_loaded"] for run in history))
        self.assertTrue(any(run["memory_written"] for run in history))
        encoded = VIEWER.serialize_for_html({"hostile": "</script><script>alert(1)</script>"})
        self.assertNotIn("</script>", encoded)
        self.assertIn("\\u003c", encoded)

    def test_self_contained_build_has_no_placeholder_or_secret_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "viewer.html"
            VIEWER.build(
                ROOT,
                self.manifest,
                self.annotations,
                ROOT / "viewer" / "trace_viewer_template.html",
                output,
            )
            html = output.read_text(encoding="utf-8")
        self.assertNotIn("__TRACE_DATA__", html)
        self.assertIn("p1-run-d451c2d3278c", html)
        self.assertNotIn("DEEPSEEK_API_KEY=", html)
        self.assertNotIn("evaluator_reference.md", html)


if __name__ == "__main__":
    unittest.main()
