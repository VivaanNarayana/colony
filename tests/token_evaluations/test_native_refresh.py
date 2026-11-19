"""Host launch regressions; no provider calls or simulated native proof."""
import copy
from types import SimpleNamespace

import pytest
from test_protocol import manifest

from scripts.token_evaluation import native
from scripts.token_evaluation.host_discovery import required_host_features
from scripts.token_evaluation.protocol import validate_contract


@pytest.mark.parametrize('candidate,main', [('task', 'task'), ('task', 'ordinary'),
                                          ('ordinary', 'task'), ('ordinary', 'ordinary')])
@pytest.mark.parametrize('arm', ['colony', 'main', 'no_colony'])
@pytest.mark.parametrize('phase', ['learn', 'reuse'])
def test_refresh_configuration_is_shared_across_all_campaign_arms(tmp_path, candidate, main, arm, phase):
    spec = manifest(tmp_path)
    spec['target']['adapter'] = candidate
    spec['main_target'] = {**spec['target'], 'adapter': main}
    before = copy.deepcopy(spec)
    args = native._client_args(spec, tmp_path, {'homes': {'a': 'writer', 'b': 'reader'}},
                               {'phase': phase, 'question': 'What does settings.py do?'},
                               tmp_path, arm, {'CODEX_HOME': 'private'}, ['settings.py'])
    enabled = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '--enable']
    assert enabled == (['mcp_tool_list_changed'] if 'task' in (candidate, main) else [])
    assert ('suppress_unstable_features_warning=true' in args) == ('task' in (candidate, main))
    assert spec == before


@pytest.mark.parametrize('reported', ['true', 'false', None])
def test_metadata_rejects_disabled_or_unsupported_refresh_feature(tmp_path, monkeypatch, reported):
    calls = []

    def features(args, **kwargs):
        calls.append(args)
        text = 'code_mode_host stable true\nshell_tool stable false\n'
        if reported is not None:
            text += f'mcp_tool_list_changed under development {reported}\n'
        return SimpleNamespace(stdout=text)

    monkeypatch.setattr(native.subprocess, 'run', features)
    args = ['client', 'exec', '--enable', 'mcp_tool_list_changed', '--disable', 'shell_tool', '-']
    if reported == 'true':
        result = native._client_metadata(args, {})
        assert result['enabled'] == ['mcp_tool_list_changed']
        assert result['native_sessions'] == 0
    else:
        with pytest.raises(ValueError, match='metadata mismatch'):
            native._client_metadata(args, {})
    assert calls == [['client', 'features', 'list', '--enable', 'mcp_tool_list_changed',
                      '--disable', 'shell_tool']]


def test_paired_task_campaign_enables_refresh_and_old_protocol_requires_original_collector(tmp_path):
    spec = manifest(tmp_path)
    assert required_host_features(spec) == {}
    spec['target']['adapter'] = 'task'
    assert required_host_features(spec) == {'mcp_tool_list_changed': True}
    for version in (11, 12):
        with pytest.raises(ValueError, match='original pinned collector'):
            validate_contract({**spec, 'contract': f'colony.token-evaluation/{version}'})


@pytest.mark.parametrize('last_tools', [None, ['read', 'research']])
def test_feature_flag_does_not_replace_observed_native_refresh(tmp_path, last_tools):
    from scripts.token_evaluation.host_discovery import validate_host_discovery
    spec = manifest(tmp_path)
    spec['target']['adapter'] = 'task'
    events = [
        {'connection': 'task', 'type': 'tools/list', 'tools': ['read', 'research']},
        {'connection': 'task', 'type': 'notifications/tools/list_changed'},
    ]
    if last_tools is not None:
        events.append({'connection': 'task', 'type': 'tools/list', 'tools': last_tools})
    spec['host_discovery'] = {
        'contract': 'colony.native-host-discovery/2', 'observer': 'native_client',
        'client_sha256': spec['client']['sha256'], 'client_version': spec['client']['version'],
        'client_features': {'mcp_tool_list_changed': True}, 'events': events,
    }
    with pytest.raises(ValueError):
        validate_host_discovery(spec)
