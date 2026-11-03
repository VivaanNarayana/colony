Queue dispatcher. Per-job policy overrides take precedence over queue policy.
An injected backend returns retryable result objects. Injected sleep accepts milliseconds.
Concrete retries are defined in backend.py; other injected implementations are unspecified.
Payment settlement policy is outside this repository.
