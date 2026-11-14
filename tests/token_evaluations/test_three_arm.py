"""Prospective three-group controls; all logs/reviews are fabricated, never model runs."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.token_evaluation import cli, native, runner
from scripts.token_evaluation.protocol import (
    arm_order,
    arm_protocol,
    collector_hashes,
    digest,
    evaluation_arms,
    planned_attempt_ids,
    validate_authorization,
    validate_manifest,
)
from scripts.token_evaluation.quality import review_template, validate_reviews
from scripts.token_evaluation.report import mask, report
from scripts.token_evaluation.rubrics import split_workload_rubrics
from tests.token_evaluations.test_agent_quality import build_reviewed_run, judgments, set_verdict
from tests.token_evaluations.test_evidence import trace as trace
from tests.token_evaluations.test_protocol import manifest
from tests.token_evaluations.test_report import put, rewrite_events
from tests.token_evaluations.test_report import run as run


def test_full_catalog_has_exactly42_distinct_attempts_and_one_control(tmp_path):
    value = manifest(tmp_path)
    workloads = json.loads((Path(__file__).parent / 'fixtures/catalog.json').read_text())['workloads']
    for workload in workloads:
        workload['source_root'] = str(tmp_path)
    split_workload_rubrics(workloads)
    value['workloads'] = workloads
    value['main_target'] = {**copy.deepcopy(value['target']), 'revision': 'd' * 40}
    value['limits']['max_attempts'] = 42
    validate_manifest(value)
    plan = planned_attempt_ids(value)
    assert len(plan) == len(set(plan.values())) == 42
    for arm in ('no_colony', 'main', 'colony'):
        assert sum(key[2] == arm for key in plan) == 14
    assert [arm_order(value, 0, i)[0] for i in range(3)] == ['no_colony', 'main', 'colony']
    for bad in (28, 41, 43, 56):
        value['limits']['max_attempts'] = bad
        with pytest.raises(ValueError, match='exactly match'):
            validate_manifest(value)


@pytest.mark.parametrize('bad', [None, {}, {'revision': 'main'}, {'python': 'relative'}])
def test_main_runtime_must_be_fully_pinned(tmp_path, bad):
    value = manifest(tmp_path)
    value['main_target'] = bad
    with pytest.raises(ValueError):
        validate_manifest(value)


def test_authorization_binds_main_revision_and_schedule(tmp_path):
    value = manifest(tmp_path)
    value['main_target'] = {**value['target'], 'revision': 'd' * 40}
    value['limits']['max_attempts'] = 21
    frozen = {'protocol': value, 'protocol_sha256': digest(value)}
    auth = {'protocol_sha256': frozen['protocol_sha256'], 'revision': value['target']['revision'],
            'model': value['client']['model'], 'limits': value['limits'], 'owner_intent': 'Synthetic control'}
    with pytest.raises(ValueError):
        validate_authorization(auth, frozen)
    auth['main_revision'] = value['main_target']['revision']
    validate_authorization(auth, frozen)
    auth['main_revision'] = 'e' * 40
    with pytest.raises(ValueError):
        validate_authorization(auth, frozen)


def test_main_uses_own_python_flags_and_homes_without_changing_shared_policy(tmp_path):
    value = manifest(tmp_path)
    value['target']['mcp_args'] = ['--context-budget', '2048']
    value['main_target'] = {**value['target'], 'python': str(tmp_path / 'main-python.exe'), 'mcp_args': []}
    team = {'homes': {'a': str(tmp_path / 'main-a'), 'b': str(tmp_path / 'main-b')}}
    args = native._client_args(value, tmp_path, team, {'phase': 'reuse'}, tmp_path,
                               'main', {'CODEX_HOME': str(tmp_path)}, ['source.py'])
    settings = dict(arg.split('=', 1) for i, arg in enumerate(args) if i and args[i - 1] == '-c')
    assert json.loads(settings['mcp_servers.colony.command']) == value['main_target']['python']
    mcp = json.loads(settings['mcp_servers.colony.args'])
    assert str(tmp_path / 'main-b') in mcp and '--workflow-read-only' in mcp
    assert '--context-budget' not in mcp
    assert arm_protocol(value, 'main')['client'] == value['client']
    assert arm_protocol(value, 'colony') is value


def test_template_defaults_to_three_groups_and_requires_explicit_main(tmp_path, monkeypatch):
    catalog = Path(__file__).parent / 'fixtures/catalog.json'
    client = tmp_path / 'client.exe'
    client.write_bytes(b'local-only-metadata')
    args = cli.parser().parse_args(['template', '--catalog', str(catalog),
        '--target-python', 'candidate', '--target-repo', 'repo', '--revision', 'candidate',
        '--main-target-python', 'main-python', '--main-target-repo', 'repo', '--main-revision', 'main',
        '--client', str(client), '--model', 'gpt-6-luna', '--track', 'natural',
        '--output', str(tmp_path / 'protocol.json')])
    pins = []
    def target(python, repo, revision, *flags):
        pins.append((python, revision))
        return {'python': str(tmp_path / python), 'revision': ('a' if revision == 'candidate' else 'b') * 40,
                'runtime_hashes': {'colony/a.py': 'c' * 64},
                'mcp_args': flags[0], 'setup_args': flags[1], 'server_args': flags[2]}
    monkeypatch.setattr(cli, '_target', target)
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='metadata-only'))
    assert cli.template(args)['native_sessions_started'] == 0
    frozen = json.loads((tmp_path / 'protocol.json').read_text())
    assert frozen['limits']['max_attempts'] == 42
    assert evaluation_arms(frozen) == ('no_colony', 'main', 'colony')
    assert pins == [('candidate', 'candidate'), ('main-python', 'main')]
    args.main_revision = None
    with pytest.raises(ValueError, match='requires --main'):
        cli.template(args)
    args.paired = True
    with pytest.raises(ValueError, match='cannot also declare main'):
        cli.template(args)


def three_group_review(run):
    root, _, _ = run
    root, _, _ = build_reviewed_run(run)
    frozen = json.loads((root / 'run.json').read_text())
    protocol = frozen['protocol']
    protocol['target']['revision'] = 'a' * 40
    protocol['main_target'] = {**copy.deepcopy(protocol['target']), 'revision': 'b' * 40}
    protocol['collector']['files'] = collector_hashes()
    frozen['protocol_sha256'] = digest(protocol)
    source = root / 'workspaces/main/gateway/v1/settings.py'
    source.parent.mkdir(parents=True)
    source.write_bytes((root / 'workspaces/colony/gateway/v1/settings.py').read_bytes())
    frozen['files'][source.relative_to(root).as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
    put(root / 'run.json', frozen)
    pre = json.loads((root / 'preflight.json').read_text())
    pre['protocol_sha256'] = frozen['protocol_sha256']
    put(root / 'preflight.json', pre)
    records = {x['arm']: x for x in [json.loads(line) for line in
               (root / 'control/attempts.jsonl').read_text().splitlines()]}
    records['main'] = {**records['colony'], 'id': 'm', 'arm': 'main', 'events': 'm.events.jsonl'}
    events = [json.loads(line) for line in (root / 'logs/b.events.jsonl').read_text().splitlines()]
    events[-1]['usage']['input_tokens'] = 120
    (root / 'logs/m.events.jsonl').write_text('\n'.join(map(json.dumps, events)))
    records['main']['events_sha256'] = hashlib.sha256((root / 'logs/m.events.jsonl').read_bytes()).hexdigest()
    task = protocol['workloads'][0]['tasks'][0]
    from scripts.token_evaluation.runtime import environment
    env = environment(root)
    env['CODEX_HOME'] = str(root / 'private/client')
    for arm, row in records.items():
        identity = row['id']
        invocation = json.loads((root / 'control/b-invocation.json').read_text())
        guidance = '' if arm == 'no_colony' else 'Fixture installed guidance.'
        prompt = native._prompt(arm_protocol(protocol, arm), task, guidance).encode()
        (root / 'prompts' / (identity + '.txt')).write_bytes(prompt)
        homes = root / ('cohorts/main/private' if arm == 'main' else 'private') / 'gateway/team'
        team = {'homes': {role: str(homes / ('agent-' + role)) for role in ('a', 'b')}}
        workspace = root / 'sessions' / identity
        invocation.update(id=identity, arm=arm, protocol_sha256=frozen['protocol_sha256'],
            client_workspace=str(workspace), prompt_sha256=hashlib.sha256(prompt).hexdigest(),
            guidance_sha256=hashlib.sha256(guidance.encode()).hexdigest(),
            configuration=None if arm == 'no_colony' else {'mode': 'managed', 'role': 'a', 'files': []},
            args=native._client_args(protocol, root / 'active' / arm / 'gateway', team, task, root,
                                    arm, env, ['settings.py'], client_workspace=workspace))
        put(root / 'control' / (identity + '-invocation.json'), invocation)
    ordered = [records[arm] for arm in arm_order(protocol, 0, 0)]
    (root / 'control/attempts.jsonl').write_text('\n'.join(map(json.dumps, ordered)))
    collection = json.loads((root / 'collection.json').read_text())
    collection.update(protocol_sha256=frozen['protocol_sha256'],
        preflight_sha256=hashlib.sha256((root / 'preflight.json').read_bytes()).hexdigest(),
        initial_empty_stores={arm: True for arm in evaluation_arms(protocol)},
        transitions=[{'arm': row['arm'], 'workload': 'gateway', 'task': 'trace', 'snapshot': 'v1',
                      'files_sha256': digest(protocol['workloads'][0]['snapshots'][0]['files'])} for row in ordered])
    put(root / 'collection.json', collection)
    mask(root, root / 'triple-mask')
    public = json.loads((root / 'triple-mask/answers.json').read_text())
    return root, public, judgments(public)


@pytest.mark.parametrize('failure', [None, 'main_disagreement', 'main_tool_limit', 'control_disagreement', 'main_policy', 'missing_control', 'wrong_main_python'])
def test_single_packet_two_reviews_and_strict_three_group_report(run, failure):
    root, public, advice = three_group_review(run)
    assert len(public) == 3 and len(review_template(public)['reviews']) == 2
    frozen = json.loads((root / 'run.json').read_text())
    if failure == 'main_tool_limit':
        def extra_calls(events):
            for i in range(7):
                events.insert(-2, {'type': 'item.completed', 'item': {'type': 'mcp_tool_call',
                    'id': f'repo{i}', 'server': 'repository', 'tool': 'fixture_files',
                    'arguments': {}, 'status': 'completed', 'result': {'content': []}}})
        rewrite_events(root, 'm', extra_calls)
        mask(root, root / 'limit-mask')
        public = json.loads((root / 'limit-mask/answers.json').read_text())
        advice = judgments(public)
    if failure in {'main_disagreement', 'control_disagreement'}:
        private = json.loads((root / 'triple-mask/private-map.json').read_text())
        main_case = next(x['case'] for x in private if x['id'] == ('m' if failure == 'main_disagreement' else 'a'))
        set_verdict(next(c for c in advice['reviews'][0]['cases'] if c['case'] == main_case), 'correct', 'failed')
    elif failure in {'main_policy', 'wrong_main_python'}:
        path = root / 'control/m-invocation.json'
        invocation = json.loads(path.read_text())
        index = invocation['args'].index('-c')
        if failure == 'wrong_main_python':
            invocation['args'][invocation['args'].index('mcp_servers.colony.command=' + json.dumps(frozen['protocol']['target']['python']))] = 'mcp_servers.colony.command="foreign"'
        else:
            invocation['args'][index + 1] = 'approval_policy="on-request"'
        put(path, invocation)
    elif failure == 'missing_control':
        rows = [json.loads(line) for line in (root / 'control/attempts.jsonl').read_text().splitlines()]
        (root / 'control/attempts.jsonl').write_text('\n'.join(json.dumps(x) for x in rows if x['arm'] != 'no_colony'))
    put(root / 'advice.json', advice)
    cli.bind_quality(root / ('limit-mask' if failure == 'main_tool_limit' else 'triple-mask'), root / 'advice.json', root / 'triple-quality.json')
    if failure in {'main_policy', 'wrong_main_python', 'missing_control'}:
        with pytest.raises(ValueError):
            report(root, root / 'triple-quality.json', root / 'triple-report.json')
        return
    value = report(root, root / 'triple-quality.json', root / 'triple-report.json')
    assert set(value['arms']) == {'no_colony', 'main', 'colony'}
    assert {k: v['known_usage']['gross_tokens'] for k, v in value['arms'].items()} == {'no_colony': 120, 'main': 140, 'colony': 100}
    assert value['comparisons']['candidate_vs_no_colony']['informational']
    primary = value['primary_comparison']
    assert primary['gross_token_difference'] == -40
    assert primary['acceptance_pass'] is (failure in {None, 'control_disagreement'})
    assert (primary['qualified_gross_reduction'] is None) is (failure not in {None, 'control_disagreement'})


def test_exactly_two_reviewers_cover_all42_masked_cases(run):
    _, public, _ = three_group_review(run)
    full = [{**copy.deepcopy(public[i % 3]), 'case': f'masked{i}'} for i in range(42)]
    advice = judgments(full)
    assert len(advice['reviews']) == 2
    assert all(len(r['cases']) == 42 for r in advice['reviews'])
    validate_reviews(full, advice)
    advice['reviews'][0]['cases'].pop()
    with pytest.raises(ValueError, match='Missing/duplicate'):
        validate_reviews(full, advice)


def test_main_runtime_drift_blocks_dispatch_even_when_candidate_matches(tmp_path, monkeypatch):
    value = manifest(tmp_path)
    value['workloads'] = []
    value['target']['runtime_hashes'] = {'colony/a.py': 'b' * 64}
    value['main_target'] = {**value['target'], 'python': str(tmp_path / 'main-python.exe')}
    calls = []
    def inventory(args, **kw):
        calls.append(args[0])
        return SimpleNamespace(stdout=json.dumps({'a.py': ('c' if args[0] == value['main_target']['python'] else 'b') * 64}))
    monkeypatch.setattr(runner.subprocess, 'run', inventory)
    with pytest.raises(ValueError, match='runtime drift'):
        runner._verify({'protocol': value, 'protocol_sha256': digest(value), 'arms': {}})
    assert calls == [value['target']['python'], value['main_target']['python']]


def test_previous7_contract_requires_original_collector(tmp_path):
    value = manifest(tmp_path)
    value['contract'] = 'colony.token-evaluation/7'
    with pytest.raises(ValueError, match='original pinned collector'):
        validate_manifest(value)


@pytest.mark.parametrize('own_publication', [False, True])
def test_main_and_candidate_cannot_share_publication_identity(trace, own_publication):
    from scripts.token_evaluation.evidence import evaluate_mechanisms
    root, frozen, events, _, _, _ = trace
    source = root / 'workspaces/main/w/v1/a.py'
    source.parent.mkdir(parents=True)
    source.write_bytes((root / 'workspaces/colony/w/v1/a.py').read_bytes())
    rows = []
    for identity, arm, task, calls in [('candidate-learn', 'colony', 'learn', events['learn']),
                                      *([('main-learn', 'main', 'learn', events['learn'])] if own_publication else []),
                                      ('main-reuse', 'main', 'reuse', events['reuse'])]:
        path = root / (identity + '.jsonl')
        path.write_text('\n'.join(map(json.dumps, calls)), encoding='utf-8')
        rows.append({'id': identity, 'arm': arm, 'task': task, 'workload': 'w', 'events_path': str(path)})
    proofs = evaluate_mechanisms(root, frozen, rows)
    assert proofs['candidate-learn']['publication']
    assert proofs['main-reuse']['hit'] is own_publication
    if own_publication:
        assert proofs['main-reuse']['published_attempt'] == 'main-learn'
