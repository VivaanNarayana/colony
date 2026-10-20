import tempfile

import pytest

from colony.capture import scrubbed_tail


def capture(data, limit=256):
    with tempfile.TemporaryFile() as output:
        output.write(data)
        return scrubbed_tail(output, limit)


@pytest.mark.parametrize(
    "secret",
    [
        b"-----BEGIN RSA PRIVATE KEY-----\nSYNTHETIC_BODY\n-----END RSA PRIVATE KEY-----",
        b"-----BEGIN PRIVATE KEY-----\nSYNTHETIC_BODY",  # Interrupted command, no END.
        b"password=SYNTHETIC_BODY",
        b'"api_key": "SYNTHETIC_BODY"',
        b"Bearer SYNTHETIC_BODY",
        b"postgresql://user:SYNTHETIC_BODY@host/db",
        b"sk-proj-" + b"S" * 40,
    ],
)
@pytest.mark.parametrize("cutoff", [1, 10])
def test_secret_opener_crosses_tail_boundary(secret, cutoff):
    suffix = b"\n" + b"z" * (256 + cutoff - len(secret) - 1)
    text, truncated = capture(secret + suffix)
    assert truncated and "[REDACTED]" in text
    assert "SYNTHETIC_BODY" not in text and "PRIVATE KEY" not in text
    assert "S" * 20 not in text
    if secret.startswith(b"-----BEGIN") and b"-----END" not in secret:
        assert text == "[REDACTED]"  # Without END, the remaining output is secret too.
    else:
        assert text.endswith(suffix.decode()) and len(text.encode()) <= 256


def test_large_secret_spans_entire_tail():
    text, truncated = capture(b"-----BEGIN PRIVATE KEY-----\n" + b"S" * 1_000_000)
    assert truncated and text == "[REDACTED]"


def test_secret_crosses_both_scan_page_and_tail_boundary():
    prefix = b"log\n" * 1023
    secret = b"-----BEGIN PRIVATE KEY-----\nSYNTHETIC_BODY\n-----END PRIVATE KEY-----"
    text, truncated = capture(prefix + secret + b"\n" + b"z" * 230)
    assert truncated and "SYNTHETIC_BODY" not in text and "[REDACTED]" in text


@pytest.mark.parametrize(
    "data", [b"", b"ordinary stdout\nstderr\n", "unicode: caf\u00e9\n".encode()]
)
def test_small_output_and_empty_file(data):
    assert capture(data) == (data.decode(), False)


def test_ordinary_tail_and_secret_entirely_before_cutoff():
    assert capture(b"password=gone\n" + b"z" * 300) == ("z" * 256, True)


def test_overlapping_secret_patterns_are_removed_together():
    data = b'password="Bearer SYNTHETIC_BODY"\nkeep diagnostic\n'
    text, truncated = capture(data)
    assert text == "[REDACTED]\nkeep diagnostic\n" and not truncated


def test_multibyte_character_at_cutoff():
    text, truncated = capture("caf\u00e9".encode() + b"z" * 255)
    assert truncated and text.endswith("z" * 255) and len(text) <= 256
