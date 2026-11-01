import json
from pai_inspection_core import PlanValidationError
from pai_inspection_core.cli import _safe_error, evaluate_main, budget_main
import pai_inspection_core.cli as cli

def test_sdk_error_does_not_echo_body_credentials_or_project():
    class ClientError(Exception):
        code = 403
    error = _safe_error(ClientError('Bearer secret-access-token refresh_token=secret-refresh project=private-project'))
    assert error['http_status'] == 403
    assert 'secret' not in json.dumps(error)
    assert 'private-project' not in json.dumps(error)
    assert 'secret' not in json.dumps(_safe_error(ValueError('secret ADC contents')))

def test_schema_error_keeps_actionable_local_message():
    assert _safe_error(PlanValidationError('plan changes instruction actions or order'))['message'] == 'plan changes instruction actions or order'

def test_stub_report_explicit_and_actual_attempts_zero(tmp_path):
    output = tmp_path / 'stub.json'
    assert evaluate_main(['--mode','stub','--output',str(output)]) == 0
    report = json.loads(output.read_text())
    assert report['mode'] == 'stub-test-double'
    assert report['attempts_this_run'] == 0
    assert report['total'] == 16

def test_budget_cli_updates_only_persistent_local_allowance(tmp_path,capsys):
    ledger=tmp_path/'ledger.sqlite'
    assert budget_main(['--ledger',str(ledger),'set','--max-usd','5','--max-attempts','300'])==0
    report=json.loads(capsys.readouterr().out)
    assert report['before']['max_usd']==1
    assert report['after']['max_attempts']==300
    assert report['cloud_billing_changed'] is False
    assert budget_main(['--ledger',str(ledger),'show'])==0
    assert json.loads(capsys.readouterr().out)['max_usd']==5

def test_http_failure_stops_batch_and_retains_failure(monkeypatch,tmp_path):
    calls=[]
    class TransportTestDouble:
        def parse(self,text,*,audit_callback=None):
            calls.append(text)
            audit_callback(json.dumps({'request_id':'transport-test-double','raw_response':None,'error':{'category':'http_permission','http_status':403}}))
            raise RuntimeError('sensitive raw body must not be printed')
    monkeypatch.setattr(cli,'_planner',lambda mode:TransportTestDouble())
    output=tmp_path/'failed.json'
    assert evaluate_main(['--mode','vertex','--output',str(output)])==1
    report=json.loads(output.read_text())
    assert len(calls)==1
    assert report['executed_cases']==1 and len(report['skipped_case_ids'])==15
    assert report['cases'][0]['classification']=='http_permission'
    assert 'sensitive raw body' not in output.read_text()

def test_sentinel_selection_uses_same_frozen_suite_and_labels(tmp_path):
    output=tmp_path/'sentinel.json'
    cases=__import__('pathlib').Path(__file__).resolve().parents[2]/'docs/semantic_acceptance_cases.json'
    assert evaluate_main(['--mode','stub','--cases',str(cases),'--limit','4','--stop-on-failure','--output',str(output)])==0
    report=json.loads(output.read_text())
    assert report['passed']==4 and report['suite_total']==24
    assert report['hard_gate_passed'] and report['attempts_this_run']==0
