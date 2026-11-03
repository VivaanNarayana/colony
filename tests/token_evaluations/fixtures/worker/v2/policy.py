DEFAULT_POLICY = {"attempts": 2, "delay_ms": 100}
QUEUE_POLICY = {"priority": {"attempts": 5, "delay_ms": 400}}


def policy(queue, job_overrides):
    return DEFAULT_POLICY | QUEUE_POLICY.get(queue, {}) | job_overrides
