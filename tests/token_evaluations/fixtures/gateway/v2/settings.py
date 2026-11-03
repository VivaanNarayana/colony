DEFAULTS = {"timeout_ms": 900, "attempts": 3}
ENVIRONMENTS = {"production": {"timeout_ms": 2200}}

def settings(environment, overrides):
    return DEFAULTS | ENVIRONMENTS.get(environment, {}) | overrides
