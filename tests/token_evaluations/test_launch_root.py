"""Invalid launch metadata must fail before either arm can incur native work."""
import json
from unittest.mock import Mock

import pytest
from test_native import setup_run

from scripts.token_evaluation import native, runner


@pytest.mark.parametrize('entrypoint', ['native_preflight', 'native_execute',
                                      'public_preflight', 'public_execute'])
@pytest.mark.parametrize('change', ['missing', 'null', 'nonstring', 'relative',
                                  'stale', 'parent_alias', 'case_alias'])
def test_invalid_recorded_launch_root_rejected_before_native_work(tmp_path, monkeypatch,
                                                                entrypoint, change):
    setup_run(tmp_path, monkeypatch)
    path = tmp_path / 'run.json'
    run = json.loads(path.read_text())
    run['files'] = {}
    declared = {'null': None, 'nonstring': [], 'relative': '.',
                'stale': str(tmp_path / 'old-location'),
                'parent_alias': str(tmp_path / 'unused' / '..'),
                'case_alias': str(tmp_path.parent / tmp_path.name.upper())}
    if change != 'missing':
        run['run_dir'] = declared[change]
    else:
        run.pop('run_dir', None)
    path.write_text(json.dumps(run))
    if entrypoint == 'public_preflight':
        (tmp_path / 'preflight.json').unlink()
    if entrypoint == 'public_execute':
        protocol = run['protocol']
        authorization = {'protocol_sha256': run['protocol_sha256'],
                         'revision': protocol['target']['revision'],
                         'model': protocol['client']['model'], 'limits': protocol['limits'],
                         'owner_intent': 'Provider-free launch-boundary regression.'}
        (tmp_path / 'authorization.json').write_text(json.dumps(authorization))
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob('*') if p.is_file()}
    environment = Mock(side_effect=AssertionError('native environment reached'))
    provision = Mock(side_effect=AssertionError('provisioning reached'))
    launch = Mock(side_effect=AssertionError('model reached'))
    client = Mock(side_effect=AssertionError('client metadata process reached'))
    monkeypatch.setattr(native.runtime, 'environment', environment)
    monkeypatch.setattr(native.runtime, 'provision', provision)
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    monkeypatch.setattr(native.subprocess, 'run', client)
    if entrypoint.endswith('preflight'):
        check = native.preflight_live if entrypoint.startswith('native') else runner.preflight
        result = check(tmp_path)
        assert result['ok'] is result['native_ready'] is False
        assert any('Prepared launch directory' in error for error in result['errors'])
    else:
        with pytest.raises(ValueError, match='Prepared launch directory'):
            if entrypoint.startswith('native'):
                native.execute_live(tmp_path, {}, tmp_path / 'auth')
            else:
                runner.execute(tmp_path, tmp_path / 'authorization.json', tmp_path / 'auth',
                               execute_native=True)
    for boundary in (environment, provision, launch, client):
        boundary.assert_not_called()
    after = {str(p.relative_to(tmp_path)): p.read_bytes()
             for p in tmp_path.rglob('*') if p.is_file()}
    if entrypoint == 'public_preflight':
        after.pop('preflight.json')  # The public wrapper retains a failure diagnostic.
    assert after == before
    assert not (tmp_path / 'private/client').exists()
    assert not list((tmp_path / 'control').glob('*-invocation.json'))
