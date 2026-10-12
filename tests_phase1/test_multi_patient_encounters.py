from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate_gastro_multi_patient_encounters.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("multi_patient_validator", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load multi-patient validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MultiPatientEncounterTests(unittest.TestCase):
    def test_all_five_cases_share_one_encounter_contract(self) -> None:
        summary = load_validator().validate_all()
        self.assertEqual(
            {
                "gi_demo_001": [5, 13, 18],
                "gi_demo_002": [5, 12, 18],
                "gi_demo_003": [7, 13, 15],
                "gi_demo_004": [4, 13, 18],
                "gi_demo_005": [7, 15, 18],
            },
            {case_id: item["encounter_counts"] for case_id, item in summary.items()},
        )
        self.assertEqual(87, sum(item["record_count"] for item in summary.values()))


if __name__ == "__main__":
    unittest.main()
