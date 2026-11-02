def fetch(transport, timeout_ms, attempts):
    for attempt in range(attempts):
        response = transport.get("/inventory", timeout_ms=timeout_ms)
        if response.status != 503 or attempt == attempts - 1:
            return response
