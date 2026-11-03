def execute(backend, job_id, attempts, delay_ms, sleep):
    for attempt in range(attempts):
        result = backend.submit(job_id)
        if not result.retryable or attempt == attempts - 1:
            return result
        sleep(delay_ms)
