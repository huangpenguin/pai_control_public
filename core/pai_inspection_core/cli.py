"""Planning/evaluation commands. Stub mode always identifies itself as a test double."""
import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sys
from .plan import TaskPlan, local_semantics, validate_plan, PlanValidationError
from .vertex import VertexPlanner, safe_failure
from .budget import UsageLedger, DEFAULT_LEDGER

class StubPlanner:
    mode = 'stub-test-double'
    last_response_text = None
    def parse(self, text):
        result = validate_plan(local_semantics(text), text)
        self.last_response_text = json.dumps(result.as_dict(), ensure_ascii=False)
        return result

def _safe_error(exc):
    status = getattr(exc, 'code', None)
    result = {'error': type(exc).__name__}
    if isinstance(status, int) and not isinstance(status, bool):
        result['http_status'] = status
    if isinstance(exc, PlanValidationError) or isinstance(exc, ValueError) and str(exc) in (
            'set GOOGLE_CLOUD_PROJECT to an existing authorized project',
            'invalid Vertex configuration',
            'gemini-2.5-flash retired: set PAI_VERTEX_MODEL=gemini-3.1-flash-lite'):
        result['message'] = str(exc)
    elif type(exc).__name__ == 'DefaultCredentialsError':
        result['message'] = 'ADC authentication unavailable; complete the interactive ADC login.'
    else:
        result['message'] = 'Request failed; check authentication, HTTP status and local service availability.'
    return result

def _planner(mode):
    return VertexPlanner() if mode == 'vertex' else StubPlanner()

def budget_main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect/change the local cumulative allowance; never changes cloud billing')
    parser.add_argument('--ledger', type=Path, default=DEFAULT_LEDGER)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('show')
    edit = commands.add_parser('set', help='Explicitly set the authorized cumulative allowance, preserving all usage')
    edit.add_argument('--max-usd', required=True)
    edit.add_argument('--max-attempts', type=int, required=True)
    args = parser.parse_args(argv)
    ledger = UsageLedger(args.ledger)
    result = ledger.summary() if args.command == 'show' else ledger.set_limits(args.max_usd, args.max_attempts)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

def plan_main(argv=None):
    parser = argparse.ArgumentParser(description='Create a validated plan; this CLI never moves a robot')
    parser.add_argument('--mode', choices=['vertex', 'stub'], default='vertex')
    parser.add_argument('--text', required=True)
    args = parser.parse_args(argv)
    try:
        planner = _planner(args.mode)
        plan = planner.parse(args.text)
        print(json.dumps({'mode': planner.mode, 'plan': plan.as_dict()}, ensure_ascii=False, indent=2))
        return 0 if plan.decision == 'accept' else 2
    except Exception as exc:
        print(json.dumps({'mode': args.mode, **_safe_error(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

def evaluate_main(argv=None):
    parser = argparse.ArgumentParser(description='Evaluate fixed human-labelled plans; never move a robot')
    parser.add_argument('--mode', choices=['vertex', 'stub'], default='vertex')
    parser.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[2] / 'docs' / 'semantic_cases.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--stop-on-failure', action='store_true', help='Use for sentinel batches')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('output already exists; choose a new file to preserve failures')
    all_cases = json.loads(args.cases.read_text())
    if args.offset < 0 or args.limit is not None and args.limit <= 0:
        parser.error('offset must be nonnegative and limit positive')
    cases = all_cases[args.offset:None if args.limit is None else args.offset+args.limit]
    if not cases:
        parser.error('selected batch is empty')
    report = {'mode': 'vertex-real' if args.mode == 'vertex' else 'stub-test-double',
              'created_utc': datetime.now(timezone.utc).isoformat(), 'cases': [],
              'case_file_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              'suite_total': len(all_cases), 'offset': args.offset, 'requested_case_ids': [c['id'] for c in cases]}
    # Retain each failure, including authentication/budget errors; no retry.
    try:
        planner = _planner(args.mode)
    except Exception as exc:
        planner = None
        setup_error = _safe_error(exc)
        setup_error['classification'] = safe_failure(exc)['category']
    for case in cases:
        row = dict(case)
        snapshots = []
        try:
            if planner is None:
                row.update(setup_error)
                row['passed'] = False
            else:
                result = (planner.parse(case['instruction'], audit_callback=snapshots.append)
                          if args.mode == 'vertex' else planner.parse(case['instruction']))
                row['actual'] = result.as_dict()
                row['raw_response'] = None if args.mode == 'vertex' else planner.last_response_text
                row['passed'] = result.decision == case['decision'] and [[s.op, s.place_id] for s in result.steps] == case['steps']
                row['classification'] = 'passed' if row['passed'] else 'model_semantic_mismatch'
        except Exception as exc:
            row.update(passed=False, raw_response=None, **_safe_error(exc))
        if snapshots:
            audit = json.loads(snapshots[0])
            row.update(audit=audit, raw_response=audit['raw_response'], request_id=audit['request_id'])
            if not row['passed']:
                row['classification'] = audit['error']['category'] if audit['error'] else 'model_semantic_mismatch'
            try:
                raw_plan = TaskPlan.from_json(audit['raw_response'])
                row['raw_plan_matches_golden_steps'] = (raw_plan.decision == case['decision'] and
                    [[s.op,s.place_id] for s in raw_plan.steps] == case['steps'])
                if row['raw_plan_matches_golden_steps'] and row.get('classification') == 'plan_validation':
                    row['classification'] = 'validation_rejection_with_correct_golden_steps'
            except PlanValidationError:
                row['raw_plan_matches_golden_steps'] = False
        report['cases'].append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        fatal = row.get('classification') in ('http_parameter','http_permission','http_availability','authentication','budget','request_error')
        if fatal or not row['passed'] and args.stop_on_failure:
            report['stopped_early'] = True
            break
    report['passed'] = sum(r['passed'] for r in report['cases'])
    report['total'] = len(cases)
    report['executed_cases'] = len(report['cases'])
    report['skipped_case_ids'] = [c['id'] for c in cases[len(report['cases']):]]
    hard_cases = [row for row in report['cases'] if row.get('hard_gate')]
    report['hard_gate_passed'] = all(row['passed'] for row in hard_cases)
    report['attempts_this_run'] = len({row['request_id'] for row in report['cases'] if row.get('request_id')})
    if planner is not None and hasattr(planner, 'ledger'):
        report['cumulative_usage'] = planner.ledger.summary()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'mode': report['mode'], 'passed': report['passed'], 'total': report['total'], 'output': str(args.output)}, ensure_ascii=False))
    return 0 if report['passed'] == report['total'] else 1

if __name__ == '__main__':
    raise SystemExit(plan_main())
