"""Provider-free, real-transport task adapter checks; never seed learning stores."""
import asyncio
import json
from pathlib import Path

from .adapters import task_args


async def check(team, protocol, repo, env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import ServerNotification, ToolListChangedNotification

    from .runtime import _roles

    _roles(team)
    files = next(w['snapshots'][0]['files'] for w in protocol['workloads']
                 if w['id'] == Path(repo).name)
    path = next(iter(files))
    roles = {}
    for phase, direct in [('learn', False), ('learn', True), ('reuse', False)]:
        task = {'phase': phase, 'question': 'Read ' + path}
        if direct:
            task['host_intent'] = {'direct_path': path}
        args = task_args(protocol, repo, team, task, files)
        notifications = asyncio.Event()

        async def handler(message):
            if isinstance(message, ServerNotification) and isinstance(message.root, ToolListChangedNotification):
                notifications.set()

        params = StdioServerParameters(command=protocol['target']['python'], args=args, env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write, message_handler=handler) as session:
            initialized = await session.initialize()
            if not initialized.capabilities.tools or not initialized.capabilities.tools.listChanged:
                raise ValueError('Task adapter must advertise tool-list changes')
            names = {t.name for t in (await session.list_tools()).tools}
            if names != {'read', 'research', 'fixture_files', 'fixture_search'}:
                raise ValueError('Task adapter startup catalog mismatch')
            result = await session.call_tool('research', {})
            value = json.loads(result.content[0].text) if not result.isError else {}
            if result.isError or value.get('hit') is not False or value.get('reason') != 'no_relevant_trail':
                raise ValueError('Task adapter authenticated empty lookup failed')
            result = await session.call_tool('read', {} if direct else {'path': path})
            if result.isError or not json.loads(result.content[0].text).get('evidence_ref'):
                raise ValueError('Task adapter failed to mint evidence reference')
            eligible = phase == 'learn' and not direct
            if eligible:
                await asyncio.wait_for(notifications.wait(), 5)
            names = {t.name for t in (await session.list_tools()).tools}
            if ('publish' in names) != eligible or 'ask' in names or 'push' in names:
                raise ValueError('Task adapter conditional publication catalog mismatch')
            roles[phase + ('_direct' if direct else '_research')] = {
                'args': args, 'tools_after_read': sorted(names), 'evidence_reference': True,
                'authenticated_empty_miss': True, 'tools_changed_received': notifications.is_set(),
            }
    return {'ok': True, 'adapter': 'task', 'roles': roles, 'errors': [],
            'sdk_discovery_refresh_verified': True, 'native_host_refresh_verified': False,
            'native_sessions': 0}
