import copy
import json
from pathlib import Path

import pytest

from scripts.token_evaluation.protocol import CONTRACT, validate_authorization, validate_manifest


def manifest(tmp_path):
    workloads = json.loads((Path(__file__).parent / 'fixtures/catalog.json').read_text())['workloads'][:1]
    workloads[0]['source_root'] = str(tmp_path)
    return {'contract': CONTRACT, 'track': 'controlled', 'target': {'revision': 'a' * 40, 'python': str(tmp_path / 'python.exe'), 'runtime_hashes': {'colony/a.py': 'b' * 64}},
            'client': {'executable': str(tmp_path / 'codex.exe'), 'sha256': 'c' * 64, 'model': 'frozen', 'version': 'frozen', 'effort': 'low'},
            'limits': {'max_attempts': 128, 'session_seconds': 10, 'wall_seconds': 60, 'gross_token_stop': 100, 'disk_bytes': 10000, 'min_free_bytes': 1, 'retries': 0},
            'research': {'max_calls': 2}, 'output': {'answer_policy': 'last_agent_message', 'format': 'json'}, 'workloads': workloads}


def test_valid_and_source_start(tmp_path):
    value = manifest(tmp_path)
    assert validate_manifest(value) is value
    value['workloads'][0]['tasks'][0]['snapshot'] = 'v2'
    with pytest.raises(ValueError, match='initial source'):
        validate_manifest(value)


@pytest.mark.parametrize('mcp,setup', [(['--context-budget', '100'], []), (['--context-budget', '100'], ['--context-budget', '200']), (['--context-budget', '100', '--context-budget', '100'], ['--context-budget', '100'])])
def test_context_contract(tmp_path, mcp, setup):
    value = manifest(tmp_path)
    value['target'].update(mcp_args=mcp, setup_args=setup)
    with pytest.raises(ValueError):
        validate_manifest(value)


@pytest.mark.parametrize('key,value', [('retries', 1), ('max_attempts', True), ('session_seconds', 301), ('wall_seconds', 7201), ('gross_token_stop', 0)])
def test_bounded_limits(tmp_path, key, value):
    spec = manifest(tmp_path)
    spec['limits'][key] = value
    with pytest.raises(ValueError):
        validate_manifest(spec)


def test_authorization_exact_binding(tmp_path):
    spec = manifest(tmp_path)
    run = {'protocol': spec, 'protocol_sha256': 'frozen'}
    auth = {'protocol_sha256': 'frozen', 'revision': spec['target']['revision'], 'model': 'frozen', 'limits': copy.deepcopy(spec['limits']), 'owner_intent': 'bounded execution'}
    assert validate_authorization(auth, run) is auth
    for key in ('protocol_sha256', 'revision', 'model', 'limits', 'owner_intent'):
        changed = copy.deepcopy(auth)
        changed[key] = None
        with pytest.raises(ValueError):
            validate_authorization(changed, run)
