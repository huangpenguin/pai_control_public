"""One approved real response, replayed once; no SDK client or fallback."""
import hashlib
import json
from pathlib import Path
import threading
import time

from pai_inspection_core import TaskPlan, validate_plan

EVIDENCE_SHA256 = 'e6255e5c83cfa92f7872d51724046c4adfc69fec191db3e64407b65a1c7d5428'
REQUEST_ID = 'c78b1d1ad60042ae86f017b4373ed472'
INSTRUCTION = '去设备 A。'
MODE = 'recorded-real-model-output'


def validated_response(report):
    if (report.get('request_id') != REQUEST_ID or report.get('instruction') != INSTRUCTION
            or report.get('mode') != 'vertex-real' or report.get('model') != 'gemini-2.5-flash'
            or report.get('success') is not True or report.get('semantic_validated') is not True):
        raise ValueError('approved real response binding does not match')
    plan = validate_plan(TaskPlan.from_json(report['raw_model_response']), INSTRUCTION)
    if plan.as_dict() != report['validated_plan']:
        raise ValueError('raw response differs from recorded validated plan')
    return plan


class RecordedPlanProvider:
    def __init__(self, evidence_path, receipt_path, execution_attempt=1):
        if execution_attempt not in (1, 2):
            raise ValueError('only the two explicitly authorized execution attempts are allowed')
        data = Path(evidence_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != EVIDENCE_SHA256:
            raise ValueError('approved evidence SHA256 does not match')
        self.report = json.loads(data)
        self.plan = validated_response(self.report)
        self.receipt_path = Path(receipt_path)
        self.execution_attempt = execution_attempt
        if self.receipt_path.exists():
            raise ValueError('approved response already consumed')
        self.lock = threading.Lock()
        self.consumed = False

    def parse(self, raw):
        with self.lock:
            if raw != INSTRUCTION:
                raise ValueError('instruction must exactly match approved response')
            if self.consumed:
                raise ValueError('approved response already consumed')
            plan = validated_response(self.report)
            self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive persistent receipt also prevents replay after process restart.
            with self.receipt_path.open('x', encoding='utf-8') as stream:
                json.dump({'source': MODE, 'request_id': REQUEST_ID,
                           'execution_attempt': self.execution_attempt,
                           'evidence_sha256': EVIDENCE_SHA256, 'instruction': raw,
                           'consumed_wall_seconds': time.monotonic()}, stream, ensure_ascii=False)
            self.consumed = True
            return plan
