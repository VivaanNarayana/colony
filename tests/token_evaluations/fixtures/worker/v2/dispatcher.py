from executor import execute
from policy import policy


def dispatch(job, backend, sleep):
    options = policy(job.queue, job.overrides)
    return execute(backend, job.id, options["attempts"], options["delay_ms"], sleep)
