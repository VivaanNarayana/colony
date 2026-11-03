WIRE_ATTEMPTS = 3

class RetryingBackend:
    def __init__(self, send):
        self.send = send

    def submit(self, job_id):
        for attempt in range(WIRE_ATTEMPTS):
            try:
                return self.send(job_id)
            except ConnectionError:
                if attempt + 1 == WIRE_ATTEMPTS:
                    raise
