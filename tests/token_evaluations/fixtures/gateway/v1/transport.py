CONNECT_ATTEMPTS = 2

class RetryingTransport:
    def __init__(self, send):
        self.send = send

    def get(self, path, timeout_ms):
        for attempt in range(CONNECT_ATTEMPTS):
            try:
                return self.send(path, timeout_ms)
            except ConnectionError:
                if attempt + 1 == CONNECT_ATTEMPTS:
                    raise
