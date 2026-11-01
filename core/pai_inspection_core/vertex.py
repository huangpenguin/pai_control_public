"""Single-attempt Vertex/ADC planning. Importing this module does not authenticate."""
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
import re
import time
from .budget import UsageLedger, PRICES, cost_micro
from .plan import ALIASES, PLAN_SCHEMA, TaskPlan, PlanValidationError, check_instruction, validate_plan

PROMPT_VERSION = 'task-plan-v3'
MODEL_THINKING = {'gemini-2.5-flash': None, 'gemini-3.1-flash-lite': 'MINIMAL',
                  'gemini-3.8-flash': 'LOW', 'gemini-3.1-pro-preview': 'LOW'}
DEFAULT_MODEL = 'gemini-3.1-pro-preview'

SYSTEM_PROMPT = '''Translate the user's robot instruction to the supplied TaskPlan JSON schema.
Only named-place sequential navigation, stationary red/green indicator inspection,
return to base, and stop are supported. No code, commands, functions or tools.
Allowed places and exact aliases: %s.
Preserve requested order. An inspection requires navigate_to(place) immediately
before inspect_indicator(place). return_to_base and stop use null place_id.
Ordinary navigation requests must NEVER gain inspection steps. Include
inspect_indicator only when inspection is explicitly requested, or when a
later station clause omits the repeated inspection verb within the SAME
clearly stated inspection sequence. Named stations and a return clause alone
do not imply inspection. A navigation-only sequence stays navigation-only.
Every step evidence is an exact contiguous quote of the original instruction
containing that step's named place (its exact original spelling). For an
inspect_indicator step, quote the original station clause, including its place,
even when the place was named in the preceding navigation clause. Never use
only an action such as 检查指示灯 as evidence without the station name. In the
inherited station B inspection below, quote 再去设备 B for both B steps.
return_to_base evidence quotes the original base/return clause; stop quotes
the original stop instruction. Do not reconstruct or normalize any quote.
Unknown places, ambiguity, negation, conditionals, unsupported instructions, or
more than eight steps reject the ENTIRE task: decision reject, steps [], and
unresolved quoting the original offending text. Never substitute a known place.
In the fixed reporting-rule task ending 红灯记为异常，绿灯记为正常，看不清就记为未确认,
the initial inspection intent applies to all subsequent visited stations; inspect
station B too even if its clause just says 再去设备 B. Reporting rules add no
conditional navigation. A reject must contain nonempty unresolved. An accept
must contain empty unresolved.
''' % json.dumps(ALIASES, ensure_ascii=False)

@dataclass(frozen=True)
class VertexConfig:
    project: str
    location: str = 'global'
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 30.0
    max_output_tokens: int = 2048

    @classmethod
    def from_env(cls):
        project = os.environ.get('GOOGLE_CLOUD_PROJECT', '')
        if not project:
            raise ValueError('set GOOGLE_CLOUD_PROJECT to an existing authorized project')
        return cls(project=project, location=os.environ.get('GOOGLE_CLOUD_LOCATION', 'global'), model=os.environ.get('PAI_VERTEX_MODEL', DEFAULT_MODEL))

    def __post_init__(self):
        if not self.project or self.model not in PRICES or not 0 < self.timeout_seconds <= 60 or not 256 <= self.max_output_tokens <= 2048:
            raise ValueError('invalid Vertex configuration')
        if self.model == 'gemini-3.1-pro-preview' and self.location != 'global':
            raise ValueError('invalid Vertex configuration')
        if self.model == 'gemini-2.5-flash' and date.today() >= date(2026, 10, 20):
            raise ValueError('gemini-2.5-flash retired: set PAI_VERTEX_MODEL=gemini-3.1-flash-lite')

class VertexPlanner:
    mode = 'vertex-real'
    def __init__(self, config=None, ledger=None):
        self.config = config or VertexConfig.from_env()
        self.ledger = ledger or UsageLedger()
        self.last_response_text = None
        self.last_request_id = None

    def parse(self, raw_instruction: str, *, audit_callback=None) -> TaskPlan:
        started = time.monotonic()
        request_id = response_text = plan = failure = finish_reason = None
        response_received = False
        self.last_response_text = None
        self.last_request_id = None
        config = self.config
        thinking_fields = ({'thinking_budget': 0} if config.model == 'gemini-2.5-flash'
                           else {'thinking_level': MODEL_THINKING[config.model]})
        try:
            check_instruction(raw_instruction)
            from google import genai
            from google.genai import types
            # Explicit vertexai=True excludes API-key mode. SDK resolves existing ADC.
            with genai.Client(vertexai=True, project=config.project, location=config.location,
                    http_options=types.HttpOptions(api_version='v1', timeout=int(config.timeout_seconds * 1000), retry_options=types.HttpRetryOptions(attempts=1))) as client:
                generation = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT,
                    temperature=0, max_output_tokens=config.max_output_tokens,
                    response_mime_type='application/json', response_json_schema=PLAN_SCHEMA,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    thinking_config=types.ThinkingConfig(**thinking_fields))
                # UTF-8 bytes plus schema/protocol overhead bound input conservatively.
                # Preserve the existing full extra reasoning reserve; no paid count_tokens.
                input_upper = len((SYSTEM_PROMPT + raw_instruction + json.dumps(PLAN_SCHEMA)).encode('utf-8')) + 4096
                output_upper = config.max_output_tokens + 65536
                request_id = self.ledger.reserve(config.model, config.location,
                    cost_micro(config.model, config.location, input_upper, output_upper))
                self.last_request_id = request_id
                try:
                    response = client.models.generate_content(model=config.model, contents=raw_instruction, config=generation)
                except Exception as exc:
                    self.ledger.settle(request_id, None, type(exc).__name__)
                    raise
                self.ledger.settle(request_id, response.usage_metadata)
                response_received = True
                response_text = response.text
                self.last_response_text = response_text  # Legacy CLI only; audit uses locals.
                candidates = getattr(response, 'candidates', None)
                if candidates:
                    reason = getattr(candidates[0], 'finish_reason', None)
                    finish_reason = getattr(reason, 'value', reason)
                plan = validate_plan(TaskPlan.from_json(response_text), raw_instruction)
                return plan
        except Exception as exc:
            failure = safe_failure(exc, response_received, finish_reason)
            raise
        finally:
            if audit_callback is not None:
                accounting = self.ledger.request_accounting(request_id)
                snapshot = {'mode': self.mode, 'model': config.model, 'location': config.location,
                    'instruction': raw_instruction,
                    'instruction_sha256': hashlib.sha256(raw_instruction.encode()).hexdigest() if isinstance(raw_instruction, str) else None,
                    'prompt_version': PROMPT_VERSION,
                    'prompt_sha256': hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
                    'schema_sha256': hashlib.sha256(json.dumps(PLAN_SCHEMA, sort_keys=True).encode()).hexdigest(),
                    'request_id': request_id, 'duration_wall_s': time.monotonic()-started,
                    'raw_response': response_text, 'validated_plan': plan.as_dict() if plan is not None else None,
                    'finish_reason': finish_reason, 'error': failure,
                    'usage_metadata': accounting['usage_metadata'] if accounting else None,
                    'request_accounting': accounting, 'sdk_http_attempts': 1,
                    'generation_config': {'temperature': 0, 'max_output_tokens': config.max_output_tokens,
                        'thinking_config': thinking_fields, 'http_timeout_seconds': config.timeout_seconds,
                        'automatic_function_calling': False}}
                # One immutable per-call value, including late responses from obsolete tasks.
                # Logging failure propagates after accounting; it cannot silently permit motion.
                audit_callback(json.dumps(snapshot, ensure_ascii=False))

def safe_failure(exc, has_response=False, finish_reason=None):
    code = getattr(exc, 'code', None)
    category = 'request_error'
    if isinstance(exc, PlanValidationError):
        category = ('output_limit' if finish_reason == 'MAX_TOKENS' else
                    'plan_validation' if has_response else 'input_validation')
    elif isinstance(code, int):
        category = ('http_parameter' if code == 400 else 'http_permission' if code in (401, 403)
                    else 'http_availability')
    elif type(exc).__name__ == 'DefaultCredentialsError':
        category = 'authentication'
    elif type(exc).__name__ == 'BudgetExceeded':
        category = 'budget'
    elif isinstance(exc, TimeoutError) or 'Timeout' in type(exc).__name__:
        category = 'timeout'
    result = {'category': category, 'type': type(exc).__name__}
    if isinstance(code, int):
        result['http_status'] = code
    status = getattr(exc, 'status', None)
    if isinstance(status, str) and re.fullmatch('[A-Z_]+', status):
        result['status'] = status
    details = getattr(exc, 'details', None)
    if isinstance(details, dict):
        error = details.get('error', details)
        for detail in error.get('details', []) if isinstance(error, dict) else []:
            if isinstance(detail, dict) and detail.get('@type', '').endswith('google.rpc.ErrorInfo'):
                reason = detail.get('reason')
                if isinstance(reason, str) and re.fullmatch('[A-Z_]+', reason):
                    result['reason'] = reason
    return result
