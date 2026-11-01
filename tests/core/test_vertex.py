"""Transport test doubles only: these tests do not call a real model."""
import json
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from google import genai
from pai_inspection_core import VertexPlanner, VertexConfig
from pai_inspection_core.budget import UsageLedger

@pytest.mark.parametrize('fails', [False, True])
def test_sdk_uses_adc_single_attempt_timeout_and_accounts(monkeypatch, tmp_path, fails):
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        if fails:
            raise TimeoutError('transport test double')
        return SimpleNamespace(text=json.dumps({'decision':'accept','steps':[{'op':'navigate_to','place_id':'station_a','evidence':'A站'}],'unresolved':[]}), usage_metadata=SimpleNamespace(model_dump=lambda **kw: {'prompt_token_count':100,'candidates_token_count':50,'total_token_count':150}))
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs['vertexai'] is True
            assert 'api_key' not in kwargs
            assert kwargs['http_options'].retry_options.attempts == 1
            assert kwargs['http_options'].timeout == 30000
            self.models = SimpleNamespace(generate_content=generate)
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(genai, 'Client', FakeClient)
    ledger = UsageLedger(tmp_path / 'ledger.sqlite')
    planner = VertexPlanner(VertexConfig(project='existing-project',model='gemini-3.1-flash-lite'), ledger)
    if fails:
        with pytest.raises(TimeoutError): planner.parse('去A站')
        assert ledger.summary()['unknown_or_pending'] == 1
    else:
        assert planner.parse('去A站').decision == 'accept'
        assert ledger.summary()['unknown_or_pending'] == 0
    assert len(calls) == 1
    assert calls[0]['config'].automatic_function_calling.disable is True
    assert ledger.summary()['attempts'] == 1

def test_validated_model_evidence_is_returned_without_local_replacement(monkeypatch, tmp_path):
    model_plan = {'decision':'accept','steps':[{'op':'navigate_to','place_id':'station_a','evidence':'去设备 A'}],'unresolved':[]}
    response = SimpleNamespace(text=json.dumps(model_plan, ensure_ascii=False), usage_metadata={'prompt_token_count':10,'candidates_token_count':10,'total_token_count':20})
    class FakeClient:
        def __init__(self, **kw): self.models=SimpleNamespace(generate_content=lambda **kw: response)
        def __enter__(self): return self
        def __exit__(self,*args): pass
    monkeypatch.setattr(genai,'Client',FakeClient)
    planner = VertexPlanner(VertexConfig(project='existing-project'), UsageLedger(tmp_path/'ledger.sqlite'))
    actual = planner.parse('去设备 A')
    assert actual.as_dict() == model_plan
    # Local oracle quotes only '设备 A'; the returned full evidence proves the
    # model-produced step is retained after validation.
    assert actual.steps[0].evidence == '去设备 A'

@pytest.mark.parametrize('model,level', [('gemini-3.1-flash-lite','MINIMAL'), ('gemini-3.8-flash','LOW'), ('gemini-3.1-pro-preview','LOW')])
def test_model_thinking_profile_and_audit_are_explicit(monkeypatch, tmp_path, model, level):
    received=[]
    def generate(**kwargs):
        received.append(kwargs)
        return SimpleNamespace(text=json.dumps({'decision':'accept','steps':[{'op':'stop','place_id':None,'evidence':'停止'}],'unresolved':[]}), usage_metadata={'prompt_token_count':10,'candidates_token_count':10,'total_token_count':20})
    class FakeClient:
        def __init__(self, **kwargs): self.models=SimpleNamespace(generate_content=generate)
        def __enter__(self): return self
        def __exit__(self,*args): pass
    monkeypatch.setattr(genai,'Client',FakeClient)
    snapshots=[]
    planner=VertexPlanner(VertexConfig(project='existing-project',model=model),UsageLedger(tmp_path/'ledger.sqlite'))
    assert planner.parse('停止',audit_callback=lambda raw:snapshots.append(json.loads(raw))).decision=='accept'
    assert received[0]['config'].thinking_config.thinking_level.value==level
    assert received[0]['config'].thinking_config.thinking_budget is None
    assert snapshots[0]['model']==model
    assert snapshots[0]['generation_config']['max_output_tokens']==2048
    assert snapshots[0]['request_accounting']['usage_status']=='known'

def test_overlapping_late_call_audit_cannot_read_other_calls_shared_fields(monkeypatch,tmp_path):
    began,release=Event(),Event()
    def generate(**kwargs):
        raw=kwargs['contents']
        if raw=='去A站':
            began.set()
            assert release.wait(timeout=3)
        place,prompt=('station_a',101) if raw=='去A站' else ('station_b',202)
        return SimpleNamespace(text=json.dumps({'decision':'accept','steps':[{'op':'navigate_to','place_id':place,'evidence':raw}],'unresolved':[]}),usage_metadata={'prompt_token_count':prompt,'candidates_token_count':10,'total_token_count':prompt+10})
    class FakeClient:
        def __init__(self,**kwargs):self.models=SimpleNamespace(generate_content=generate)
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(genai,'Client',FakeClient)
    ledger=UsageLedger(tmp_path/'ledger.sqlite')
    ledger.set_limits('5',300)
    planner=VertexPlanner(VertexConfig(project='existing-project'),ledger)
    snapshots=[]
    callback=lambda raw:snapshots.append(json.loads(raw))
    with ThreadPoolExecutor(max_workers=2) as pool:
        old=pool.submit(planner.parse,'去A站',audit_callback=callback)
        assert began.wait(timeout=3)
        planner.parse('去B站',audit_callback=callback)
        release.set()
        old.result(timeout=3)
    assert [row['instruction'] for row in snapshots]==['去B站','去A站']
    assert len({row['request_id'] for row in snapshots})==2
    for row,prompt,place in zip(snapshots,[202,101],['station_b','station_a']):
        assert row['usage_metadata']['prompt_token_count']==prompt
        assert row['validated_plan']['steps'][0]['place_id']==place
        assert ledger.request_accounting(row['request_id'])['usage_metadata']==row['usage_metadata']

def test_audit_callback_failure_cannot_release_a_plan_but_cost_is_settled(monkeypatch,tmp_path):
    response=SimpleNamespace(text=json.dumps({'decision':'accept','steps':[{'op':'stop','place_id':None,'evidence':'停止'}],'unresolved':[]}),usage_metadata={'prompt_token_count':10,'candidates_token_count':10,'total_token_count':20})
    class FakeClient:
        def __init__(self,**kwargs):self.models=SimpleNamespace(generate_content=lambda **kw:response)
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(genai,'Client',FakeClient)
    ledger=UsageLedger(tmp_path/'ledger.sqlite')
    planner=VertexPlanner(VertexConfig(project='existing-project'),ledger)
    def failed_callback(raw):raise OSError('audit destination unavailable')
    with pytest.raises(OSError):planner.parse('停止',audit_callback=failed_callback)
    assert ledger.summary()['attempts']==1
    assert ledger.summary()['unknown_or_pending']==0

def test_default_model_and_explicit_environment_override(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT','existing-project')
    monkeypatch.delenv('PAI_VERTEX_MODEL',raising=False)
    monkeypatch.delenv('GOOGLE_CLOUD_LOCATION',raising=False)
    assert VertexConfig(project='existing-project').model=='gemini-3.1-pro-preview'
    assert VertexConfig.from_env().model=='gemini-3.1-pro-preview'
    assert VertexConfig.from_env().location=='global'
    monkeypatch.setenv('PAI_VERTEX_MODEL','gemini-3.8-flash')
    assert VertexConfig.from_env().model=='gemini-3.8-flash'
