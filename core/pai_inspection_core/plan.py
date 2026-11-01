"""Small, explicit plan language; no model-generated code is executable."""
from dataclasses import asdict, dataclass
import json
import re
import unicodedata

MAX_INPUT_CHARS = 512
MAX_STEPS = 8
OPERATIONS = ('navigate_to', 'inspect_indicator', 'return_to_base', 'stop')
ALIASES = {
    'base': ('base', '基地', '起点', '出发地点', '出発地点', '拠点', 'ホーム'),
    'station_a': ('station_a', 'station a', 'A站', 'A站点', '站点A', 'A地点', '设备 A', '设备A', '設備A', 'A', 'ステーションA', '地点A'),
    'station_b': ('station_b', 'station b', 'B站', 'B站点', '站点B', 'B地点', '设备 B', '设备B', '設備B', 'B', 'ステーションB', '地点B'),
}

class PlanValidationError(ValueError):
    pass

@dataclass(frozen=True)
class TaskStep:
    op: str
    place_id: str | None
    evidence: str

@dataclass(frozen=True)
class TaskPlan:
    decision: str
    steps: tuple[TaskStep, ...]
    unresolved: tuple[str, ...] = ()

    def as_dict(self):
        return {'decision': self.decision, 'steps': [asdict(s) for s in self.steps], 'unresolved': list(self.unresolved)}

    @classmethod
    def from_json(cls, raw: str):
        if not isinstance(raw, str) or len(raw) > 16384:
            raise PlanValidationError('response is absent or too long')
        def unique(pairs):
            result = {}
            for key, val in pairs:
                if key in result:
                    raise PlanValidationError('duplicate JSON key')
                result[key] = val
            return result
        try:
            data = json.loads(raw, object_pairs_hook=unique)
        except (json.JSONDecodeError, TypeError) as exc:
            raise PlanValidationError('response must be a JSON object') from exc
        if not isinstance(data, dict) or set(data) != {'decision', 'steps', 'unresolved'}:
            raise PlanValidationError('unexpected plan fields')
        if data['decision'] not in ('accept', 'reject') or not isinstance(data['steps'], list) or not isinstance(data['unresolved'], list):
            raise PlanValidationError('invalid plan types')
        if len(data['steps']) > MAX_STEPS or len(data['unresolved']) > 16:
            raise PlanValidationError('too many plan items')
        steps = []
        for s in data['steps']:
            if not isinstance(s, dict) or set(s) != {'op', 'place_id', 'evidence'}:
                raise PlanValidationError('unexpected step fields')
            if s['op'] not in OPERATIONS or s['place_id'] not in (*ALIASES, None) or not isinstance(s['evidence'], str) or not 0 < len(s['evidence']) <= MAX_INPUT_CHARS:
                raise PlanValidationError('invalid step')
            steps.append(TaskStep(**s))
        if any(not isinstance(u, str) or not 0 < len(u) <= MAX_INPUT_CHARS for u in data['unresolved']):
            raise PlanValidationError('invalid unresolved evidence')
        return cls(data['decision'], tuple(steps), tuple(data['unresolved']))

PLAN_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['decision', 'steps', 'unresolved'],
    'properties': {
        'decision': {'type': 'string', 'enum': ['accept', 'reject']},
        'steps': {'type': 'array', 'maxItems': 8, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['op', 'place_id', 'evidence'],
            'properties': {'op': {'type': 'string', 'enum': list(OPERATIONS)},
                'place_id': {'anyOf': [{'type': 'string', 'enum': list(ALIASES)}, {'type': 'null'}]},
                'evidence': {'type': 'string'}}}},
        'unresolved': {'type': 'array', 'items': {'type': 'string'}},
    },
}

# Conservative, documented supported command grammar. Anything left over is
# unknown/ambiguous, including negations, arbitrary destinations and injections.
_INSPECT = r'检查|查看|巡检|确认|確認|点検|inspect|check'
_RETURN = r'返回|回到|回|戻って|戻る|return|go back'
_STOP = r'停止|停下|止まって|止まる|stop'
_MOVE = r'前往|到达|到|去|移动|行って|行き|向かって|移動|へ|に|go to|navigate to|visit'
_FILLER = r'指示灯|信号灯|灯色|ランプ|表示灯|indicator|light|颜色|色|依次|按顺序|順に|先|まず|然后|再|最後に|最后|続いて|次に|then|first|finally|and|please|请|ください|を|して|する|の|は|と|并|并且|和|后|後|地'
_ALIAS_PAIRS = sorted(((a, p) for p, aliases in ALIASES.items() for a in aliases), key=lambda x: len(x[0]), reverse=True)
_ALIAS_RE = re.compile('|'.join(('(?<![a-z_])' + re.escape(a) + '(?![a-z_])') if a in ('A', 'B', 'base') else re.escape(a) for a, _ in _ALIAS_PAIRS), re.I)
_REPORT_RULE = '红灯记为异常，绿灯记为正常，看不清就记为未确认'
_JP_REPORT_RULE = '赤は異常、緑は正常、不明は未確認として記録'

def _norm(text):
    return unicodedata.normalize('NFKC', text).lower()

def check_instruction(instruction):
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > MAX_INPUT_CHARS:
        raise PlanValidationError('instruction must have 1..512 characters')

def local_semantics(instruction: str) -> TaskPlan:
    """A conservative semantic validator/oracle, never a production LLM substitute."""
    check_instruction(instruction)
    text = _norm(instruction)
    command = instruction
    inspection_context = False
    for reporting in (_REPORT_RULE, _JP_REPORT_RULE):
        if reporting in instruction:
            head, tail = instruction.split(reporting, 1)
            if tail.strip('。 .'):
                return TaskPlan('reject', (), (instruction,))
            command = head.rstrip('。 .')
            inspection_context = bool(re.search(_INSPECT, _norm(command), re.I))
            text = _norm(command)
            break
    masked = _ALIAS_RE.sub(' ', text)
    for pattern in (_FILLER, _INSPECT, _RETURN, _STOP, _MOVE):
        masked = re.sub(pattern, ' ', masked, flags=re.I)
    residual = re.sub(r'[\s,，。.;；、!?！？→\-]+', '', masked)
    if residual:
        return TaskPlan('reject', (), (instruction,))
    # Split at explicit sequencing/punctuation; aliases joined with 和/と/and
    # stay in one clause and inherit its inspection verb.
    clauses = re.split(r'[,，。.;；、]|然后|再|最後に|最后|続いて|次に|\bthen\b|\bfinally\b|→', command, flags=re.I)
    steps = []
    for clause in clauses:
        if not clause.strip():
            continue
        c = _norm(clause)
        matches = list(_ALIAS_RE.finditer(c))
        inspect = re.search(_INSPECT, c, re.I) or inspection_context
        returning = re.search(_RETURN, c, re.I)
        stop = re.search(_STOP, c, re.I)
        if len(matches) > 1 and re.search(_INSPECT, c, re.I) and re.search(_MOVE, c, re.I):
            return TaskPlan('reject', (), (instruction,))
        if stop:
            if matches or inspect or returning:
                return TaskPlan('reject', (), (instruction,))
            steps.append(TaskStep('stop', None, clause.strip()))
            continue
        if not matches:
            if returning:
                steps.append(TaskStep('return_to_base', None, clause.strip()))
            elif re.sub(r'[\s]+', '', c):
                return TaskPlan('reject', (), (instruction,))
            continue
        if not (inspect or returning or re.search(_MOVE, c, re.I)):
            return TaskPlan('reject', (), (instruction,))
        for match in matches:
            place = next(p for a, p in _ALIAS_PAIRS if _norm(a) == match.group())
            # NFKC preserves offsets for the documented Chinese/Japanese aliases.
            evidence = clause[match.start():match.end()]
            if place == 'base':
                if re.search(_INSPECT, c, re.I):
                    return TaskPlan('reject', (), (instruction,))
                steps.append(TaskStep('return_to_base', None, evidence))
            else:
                if returning:
                    return TaskPlan('reject', (), (instruction,))
                steps.append(TaskStep('navigate_to', place, evidence))
                if inspect:
                    steps.append(TaskStep('inspect_indicator', place, evidence))
    if not steps or len(steps) > MAX_STEPS or any(s.op == 'stop' for s in steps[:-1]):
        return TaskPlan('reject', (), (instruction,))
    return TaskPlan('accept', tuple(steps))

def validate_plan(plan: TaskPlan, instruction: str) -> TaskPlan:
    check_instruction(instruction)
    if not isinstance(plan, TaskPlan):
        raise PlanValidationError('TaskPlan required')
    # Apply identical strict shape checks to in-process dataclasses too.
    TaskPlan.from_json(json.dumps(plan.as_dict(), ensure_ascii=False))
    expected = local_semantics(instruction)
    if plan.decision == 'reject':
        if plan.steps or not plan.unresolved or any(u not in instruction for u in plan.unresolved):
            raise PlanValidationError('rejection requires original unresolved evidence and zero steps')
        return plan  # false negative is safe and remains an evaluation failure
    if expected.decision != 'accept' or plan.unresolved:
        raise PlanValidationError('unknown or ambiguous instruction: reject entire task')
    if [(s.op, s.place_id) for s in plan.steps] != [(s.op, s.place_id) for s in expected.steps]:
        raise PlanValidationError('plan changes instruction actions or order')
    for s, e in zip(plan.steps, expected.steps):
        if s.evidence not in instruction or not s.evidence or e.evidence not in s.evidence:
            raise PlanValidationError('place/action evidence must quote the instruction')
    return plan
