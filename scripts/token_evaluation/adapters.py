"""Frozen adapter selection shared by launch, receipt rebuilding and preflight."""
import json
from pathlib import Path


def task_mode(protocol):
    return protocol.get('target', {}).get('adapter', 'ordinary') == 'task'


def task_args(protocol, repo, team, task, files):
    role = 'a' if task['phase'] == 'learn' else 'b'
    args = ['-I', str(Path(__file__).with_name('task_adapter.py')),
            '--home', team['homes'][role], '--repo', str(repo),
            '--question', task['question'], '--files', json.dumps(list(files)),
            '--max-calls', str(protocol['research']['max_calls']),
            *protocol['target'].get('mcp_args', [])]
    intent = task.get('host_intent', {})
    if intent.get('direct_path'):
        args += ['--direct-path', intent['direct_path']]
    if intent.get('remember'):
        args += ['--remember']
    if role == 'b':
        args += ['--read-only']
    return args


def research_call(server, tool):
    return server == 'repository' or (server == 'colony' and tool in {'read', 'fixture_files', 'fixture_search'})


def allowed_tool(server, tool, adapter='ordinary'):
    if adapter == 'task':
        return server == 'colony' and tool in {'read', 'research', 'publish', 'fixture_files', 'fixture_search'}
    return ((server == 'repository' and tool in {'fixture_files', 'fixture_read', 'fixture_search'})
            or (server == 'colony' and tool in {'ask', 'push'}))
