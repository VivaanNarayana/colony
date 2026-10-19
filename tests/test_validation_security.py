import pytest

from colony.models import Source
from colony.security import scrub
from colony.validation import snapshot, validate


def test_hash_symbol_config_and_unsupported(team):
    _, _, repo, _, _ = team
    source = snapshot(repo, "build.py")
    assert (
        validate(repo, [source.model_copy(update={"symbol": "build", "contains": "TIMEOUT = 30"})])[
            0
        ]
        == "validated"
    )
    assert validate(repo, [source.model_copy(update={"symbol": "invented"})])[0] == "stale"
    assert validate(repo, [source.model_copy(update={"contains": "TIMEOUT = 99"})])[0] == "stale"
    assert (
        validate(repo, [source.model_copy(update={"kind": "url", "ref": "https://example.com"})])[0]
        == "unverified"
    )


@pytest.mark.parametrize(
    "ref", ["../issuer.pem", "/etc/passwd", "C:/Windows/win.ini", "..\\secret"]
)
def test_path_escape_fails_closed(team, ref):
    _, _, repo, _, _ = team
    assert validate(repo, [Source(ref=ref, content_hash="a" * 64)])[0] == "stale"


def test_symlink_escape(team, tmp_path):
    _, _, repo, _, _ = team
    outside = tmp_path / "outside"
    outside.write_text("private")
    try:
        (repo / "link").symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit symlinks")
    assert validate(repo, [Source(ref="link", content_hash="a" * 64)])[0] == "stale"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-" + "a" * 40,
        "ghp_" + "b" * 36,
        "AKIA" + "A" * 16,
        "postgresql://user:password@host/db",
        "Bearer abcdefghijklmnop",
        "password=superprivate",
        '"api_key": "superprivate"',
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_scrub(secret):
    assert scrub(secret) == "[REDACTED]"
    assert scrub("Run python -m pytest") == "Run python -m pytest"
