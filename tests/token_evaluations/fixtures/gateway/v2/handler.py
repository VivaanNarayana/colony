from client import fetch
from settings import settings


def handle(request, transport):
    config = settings(request.environment, request.overrides)
    return fetch(transport, config["timeout_ms"], config["attempts"])
