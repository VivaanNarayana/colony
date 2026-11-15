"""Consume retained native-host discovery evidence; SDK checks are insufficient."""
from .adapters import task_mode
from .protocol import arm_protocol, evaluation_arms, require


def validate_host_discovery(protocol):
    if not any(task_mode(arm_protocol(protocol, arm)) for arm in evaluation_arms(protocol)
               if arm != 'no_colony'):
        return None
    proof = protocol.get('host_discovery')
    require(isinstance(proof, dict), 'Task mode requires pinned native-host tool-refresh evidence')
    require(proof.get('contract') == 'colony.native-host-discovery/1'
            and proof.get('client_sha256') == protocol['client']['sha256']
            and proof.get('client_version') == protocol['client']['version']
            and proof.get('observer') == 'native_client',
            'Tool-refresh evidence must bind this native client; SDK evidence is insufficient')
    events = proof.get('events')
    require(isinstance(events, list) and 3 <= len(events) <= 32, 'Missing native-host discovery trace')
    # The transcript is a retained declaration, like native invocation receipts,
    # not cryptographic attestation. Do not manufacture it from an SDK probe.
    state, connection = 0, None
    for event in events:
        require(isinstance(event, dict) and isinstance(event.get('connection'), str)
                and 0 < len(event['connection']) <= 128, 'Invalid discovery connection')
        if state == 0 and event.get('type') == 'tools/list':
            tools = event.get('tools')
            if (isinstance(tools, list) and all(isinstance(t, str) for t in tools)
                    and {'read', 'research'} <= set(tools) and 'publish' not in tools):
                state, connection = 1, event['connection']
        elif event['connection'] == connection:
            if state == 1 and event.get('type') == 'notifications/tools/list_changed':
                state = 2
            elif state == 2 and event.get('type') == 'tools/list':
                tools = event.get('tools')
                if (isinstance(tools, list) and all(isinstance(t, str) for t in tools)
                        and {'read', 'research', 'publish'} <= set(tools)):
                    state = 3
    require(state == 3, 'Native host must refresh tools after the same connection notification')
    return {'native_host_refresh_evidence': 'retained_declaration_not_attestation'}
