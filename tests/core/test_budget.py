from concurrent.futures import ProcessPoolExecutor
import sqlite3
import pytest
from pai_inspection_core.budget import UsageLedger, BudgetExceeded, cost_micro

def reserve_worker(path):
    try:
        UsageLedger(path).reserve('gemini-2.5-flash', 'global', 300_000)
        return True
    except BudgetExceeded:
        return False

def test_cross_process_reservation_is_atomic(tmp_path):
    path = str(tmp_path / 'ledger.sqlite')
    UsageLedger(path)
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve_worker, [path] * 8))
    assert sum(results) == 3
    assert UsageLedger(path).summary()['accounted_usd'] == .9

def test_attempt_cap_and_persistence(tmp_path):
    path = tmp_path / 'ledger.sqlite'
    ledger = UsageLedger(path)
    for _ in range(100):
        ledger.reserve('gemini-2.5-flash', 'global', 1)
    with pytest.raises(BudgetExceeded):
        UsageLedger(path).reserve('gemini-2.5-flash', 'global', 1)
    assert UsageLedger(path).summary()['attempts'] == 100

def test_missing_usage_retains_reserve_and_records_unknown(tmp_path):
    ledger = UsageLedger(tmp_path / 'ledger.sqlite')
    key = ledger.reserve('gemini-2.5-flash', 'global', 100_000)
    assert ledger.settle(key, None, 'TimeoutError') == 'unknown'
    assert ledger.summary()['accounted_usd'] == .1
    assert ledger.summary()['unknown_or_pending'] == 1

def test_real_counters_include_thinking_and_raw_metadata(tmp_path):
    ledger = UsageLedger(tmp_path / 'ledger.sqlite')
    key = ledger.reserve('gemini-2.5-flash', 'global', 100_000)
    usage = {'prompt_token_count': 100, 'candidates_token_count': 20, 'thoughts_token_count': 80, 'total_token_count': 200}
    assert ledger.settle(key, usage) == 'known'
    assert ledger.summary()['accounted_usd'] == cost_micro('gemini-2.5-flash', 'global', 100, 100) / 1e6
    with sqlite3.connect(ledger.path) as con:
        assert 'thoughts_token_count' in con.execute('SELECT usage_json FROM requests').fetchone()[0]

def test_old_ledger_migration_preserves_unknown_and_explicit_policy_change(tmp_path):
    path = tmp_path / 'old.sqlite'
    ledger = UsageLedger(path)
    key = ledger.reserve('gemini-2.5-flash', 'global', 166_363)
    ledger.settle(key, None, 'ClientError')
    with sqlite3.connect(path) as db:
        original = db.execute('SELECT * FROM requests').fetchall()
        db.execute('DROP TABLE policy')  # Existing version had only the requests table.
    migrated = UsageLedger(path)
    assert migrated.summary()['max_usd'] == 1
    change = migrated.set_limits('5', 300)
    assert change['before']['max_attempts'] == 100
    assert change['after']['unknown_reserved_usd'] == .166363
    assert UsageLedger(path).summary()['max_usd'] == 5
    assert UsageLedger(path).summary()['max_attempts'] == 300
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM requests').fetchall() == original

def test_lower_policy_blocks_reservation_without_rewriting_history(tmp_path):
    ledger = UsageLedger(tmp_path / 'ledger.sqlite')
    key = ledger.reserve('gemini-2.5-flash', 'global', 100_000)
    ledger.settle(key, None)
    before = ledger.request_accounting(key)
    ledger.set_limits('.05', 1)
    with pytest.raises(BudgetExceeded):
        ledger.reserve('gemini-3.1-flash-lite', 'global', 1)
    assert ledger.request_accounting(key) == before
    assert ledger.summary()['attempts'] == 1

def test_changed_policy_is_shared_by_processes_and_models(tmp_path):
    path = str(tmp_path / 'ledger.sqlite')
    ledger = UsageLedger(path)
    ledger.set_limits('.6', 300)
    ledger.reserve('gemini-3.1-flash-lite', 'global', 300_000)
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve_worker, [path] * 8))
    assert sum(results) == 1
    assert UsageLedger(path).summary()['accounted_usd'] == .6

@pytest.mark.parametrize('amount,attempts,reserve,count', [('5',300,4_000_000,1), ('5',5,1,4)])
def test_warning_tracks_stored_policy_not_old_constants(tmp_path, amount, attempts, reserve, count):
    ledger = UsageLedger(tmp_path / 'ledger.sqlite')
    ledger.set_limits(amount, attempts)
    for _ in range(count-1):
        ledger.reserve('gemini-2.5-flash', 'global', reserve)
    with pytest.warns(RuntimeWarning, match='80%'):
        ledger.reserve('gemini-2.5-flash', 'global', reserve)
    assert ledger.summary()['warning_80_percent']
