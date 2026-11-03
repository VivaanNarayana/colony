DEFAULT_POLICY = {"attempts": 2, "delay_ms": 100}
QUEUE_POLICY = {"priority": {"attempts": 4, "delay_ms": 250}}


def policy(queue, job_overrides):
    return DEFAULT_POLICY | QUEUE_POLICY.get(queue, {}) | job_overrides
