import json
from pathlib import Path
import tempfile
import unittest

from pai_inspection_core import PlanValidationError
from recorded_plan_provider import RecordedPlanProvider, INSTRUCTION, validated_response

EVIDENCE = Path(__file__).resolve().parents[3] / 'docs/evidence/vertex-second-authorized-probe.json'
if not EVIDENCE.exists():  # Docker installs docs under /opt/pai, sources under /workspace.
    EVIDENCE = Path('/opt/pai/docs/evidence/vertex-second-authorized-probe.json')


class RecordedProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.receipt = Path(self.temp.name) / 'receipt.json'
        self.report = json.loads(EVIDENCE.read_text())

    def test_returns_exact_real_plan_and_rejects_reuse_and_restart(self):
        provider = RecordedPlanProvider(EVIDENCE, self.receipt)
        self.assertEqual(provider.parse(INSTRUCTION).as_dict(), self.report['validated_plan'])
        self.assertTrue(self.receipt.exists())
        with self.assertRaises(ValueError):
            provider.parse(INSTRUCTION)
        with self.assertRaises(ValueError):
            RecordedPlanProvider(EVIDENCE, self.receipt)

    def test_instruction_binding_rejects_without_consuming(self):
        provider = RecordedPlanProvider(EVIDENCE, self.receipt)
        with self.assertRaises(ValueError):
            provider.parse('去设备 A')
        self.assertFalse(self.receipt.exists())

    def test_changed_evidence_fails_hash(self):
        changed = Path(self.temp.name) / 'changed.json'
        changed.write_bytes(EVIDENCE.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            RecordedPlanProvider(changed, self.receipt)

    def test_request_binding_rejected(self):
        self.report['request_id'] = 'other-request'
        with self.assertRaises(ValueError):
            validated_response(self.report)

    def test_strict_schema_and_semantics_revalidated(self):
        original = self.report['raw_model_response']
        data = json.loads(original)
        data['steps'][0]['code'] = 'not allowed'
        self.report['raw_model_response'] = json.dumps(data)
        with self.assertRaises(PlanValidationError):
            validated_response(self.report)
        data = json.loads(original)
        data['steps'][0]['place_id'] = 'station_b'
        self.report['raw_model_response'] = json.dumps(data)
        with self.assertRaises(PlanValidationError):
            validated_response(self.report)

    def test_recorded_validation_cannot_replace_raw_model_plan(self):
        self.report['validated_plan']['steps'][0]['evidence'] = '设备 A'
        with self.assertRaises(ValueError):
            validated_response(self.report)


if __name__ == '__main__':
    unittest.main()
