"""Application-level cumulative allowance, not a cloud billing hard cap."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import json
import sqlite3
import uuid
import warnings

MAX_MICRO_USD = 1_000_000
MAX_ATTEMPTS = 100
# Public standard online text rates checked 2026-10-11. Prices per 1M tokens.
PRICE_DATE = '2026-10-11'
PRICE_SOURCE = 'https://cloud.google.com/vertex-ai/generative-ai/pricing'
PRICES = {'gemini-2.5-flash': ('0.30', '2.50'), 'gemini-3.1-flash-lite': ('0.25', '1.50'),
          # Standard 3.8 rates conservatively exclude promotional credits back.
          'gemini-3.8-flash': ('1.50', '7.50'), 'gemini-3.1-pro-preview': ('2.00', '12.00')}
DEFAULT_LEDGER = Path(__file__).resolve().parents[2] / 'state' / 'vertex-usage.sqlite3'

class BudgetExceeded(RuntimeError):
    pass

def cost_micro(model, location, input_tokens, output_tokens):
    incoming, outgoing = map(Decimal, PRICES[model])
    if model in ('gemini-3.1-flash-lite', 'gemini-3.8-flash') and location != 'global':
        incoming *= Decimal('1.1')
        outgoing *= Decimal('1.1')
    # USD / million tokens -> microUSD / token, hence no million divisor.
    return int((incoming * input_tokens + outgoing * output_tokens).to_integral_value(rounding=ROUND_CEILING))

class UsageLedger:
    def __init__(self, path=DEFAULT_LEDGER):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, created TEXT NOT NULL, model TEXT NOT NULL, location TEXT NOT NULL, reserved_micro INTEGER NOT NULL, charged_micro INTEGER NOT NULL, status TEXT NOT NULL, usage_json TEXT, error TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), max_micro INTEGER NOT NULL CHECK(max_micro>0), max_attempts INTEGER NOT NULL CHECK(max_attempts>0), updated TEXT NOT NULL)')
            # Old ledgers and new installations start with the original allowance.
            # Only the explicit budget command changes it, never environment state.
            con.execute('INSERT OR IGNORE INTO policy VALUES (1,?,?,?)',
                        (MAX_MICRO_USD, MAX_ATTEMPTS, datetime.now(timezone.utc).isoformat()))

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.execute('PRAGMA busy_timeout=10000')
        return con

    @staticmethod
    def _summary(con):
        count, spent, unknown, retained = con.execute("SELECT count(*), coalesce(sum(charged_micro),0), coalesce(sum(status IN ('reserved','unknown')),0), coalesce(sum(CASE WHEN status IN ('reserved','unknown') THEN charged_micro ELSE 0 END),0) FROM requests").fetchone()
        maximum, attempts = con.execute('SELECT max_micro,max_attempts FROM policy WHERE id=1').fetchone()
        return {'attempts': count, 'accounted_usd': spent / 1_000_000,
                'unknown_or_pending': unknown, 'unknown_reserved_usd': retained / 1_000_000,
                'known_estimated_usd': (spent-retained) / 1_000_000,
                'max_usd': maximum / 1_000_000, 'max_attempts': attempts,
                'warning_80_percent': count * 5 >= attempts * 4 or spent * 5 >= maximum * 4}

    def summary(self):
        with self._connect() as con:
            con.execute('BEGIN')
            return self._summary(con)

    def set_limits(self, max_usd, max_attempts):
        amount = Decimal(str(max_usd))
        micro = amount * 1_000_000
        if (not amount.is_finite() or amount <= 0 or micro != micro.to_integral_value()
                or not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts <= 0):
            raise ValueError('positive USD amount with at most six decimals and positive integer attempts required')
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            before = self._summary(con)
            con.execute('UPDATE policy SET max_micro=?,max_attempts=?,updated=? WHERE id=1',
                        (int(micro), max_attempts, datetime.now(timezone.utc).isoformat()))
            after = self._summary(con)
        return {'before': before, 'after': after, 'cloud_billing_changed': False}

    def request_accounting(self, request_id):
        if request_id is None:
            return None
        with self._connect() as con:
            row = con.execute('SELECT reserved_micro,charged_micro,status,usage_json FROM requests WHERE id=?', (request_id,)).fetchone()
        if row is None:
            raise ValueError('unknown request')
        return {'reserved_usd': row[0] / 1_000_000, 'accounted_usd': row[1] / 1_000_000,
                'usage_status': row[2], 'usage_metadata': json.loads(row[3]) if row[3] is not None else None}

    def reserve(self, model, location, upper_micro):
        request_id = uuid.uuid4().hex
        if upper_micro <= 0:
            raise ValueError('positive reservation required')
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            count, spent = con.execute('SELECT count(*),coalesce(sum(charged_micro),0) FROM requests').fetchone()
            maximum, attempts = con.execute('SELECT max_micro,max_attempts FROM policy WHERE id=1').fetchone()
            if count >= attempts or spent + upper_micro > maximum:
                raise BudgetExceeded('cumulative application allowance exhausted; inspect persistent policy with pai-budget show')
            con.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)', (request_id, datetime.now(timezone.utc).isoformat(), model, location, upper_micro, upper_micro, 'reserved', None, None))
            if (count + 1) * 5 >= attempts * 4 or (spent + upper_micro) * 5 >= maximum * 4:
                warnings.warn('Vertex allowance has reached 80%; check persistent usage ledger', RuntimeWarning)
        return request_id

    def settle(self, request_id, usage, error=None):
        # Preserve server metadata exactly. Missing/invalid counters retain reservation.
        raw = usage.model_dump(mode='json') if hasattr(usage, 'model_dump') else usage
        raw = raw if isinstance(raw, dict) else None
        counters = [raw.get(k) if raw else None for k in ('prompt_token_count', 'candidates_token_count', 'total_token_count')]
        valid = all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counters)
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT model,location,reserved_micro,status FROM requests WHERE id=?', (request_id,)).fetchone()
            if row is None or row[3] not in ('reserved', 'unknown'):
                raise ValueError('unknown or already settled request')
            model, location, reserve, _ = row
            charge, status = reserve, 'unknown'
            if valid:
                prompt, candidates, total = counters
                thoughts = raw.get('thoughts_token_count')
                if total >= prompt + candidates and (thoughts is None or isinstance(thoughts, int) and not isinstance(thoughts, bool) and thoughts >= 0):
                    output = max(total - prompt, candidates + (thoughts or 0))
                    charge, status = cost_micro(model, location, prompt, output), 'known'
            con.execute('UPDATE requests SET charged_micro=?,status=?,usage_json=?,error=? WHERE id=?', (charge, status, json.dumps(raw) if raw is not None else None, error, request_id))
        return status
