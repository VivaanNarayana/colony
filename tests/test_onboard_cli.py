import json
import sys

import pytest

from colony import cli, doctor, onboarding, provision


def test_bootstrap_cli_preview_has_copyable_commands_and_no_effects(tmp_path):
    repo = tmp_path / "project with spaces"
    repo.mkdir()
    workspace = tmp_path / "private team"
    args = cli.parser().parse_args(
        [
            "onboard",
            "init",
            "--workspace",
            str(workspace),
            "--repo",
            str(repo),
            "--dry-run",
        ]
    )
    result = cli.execute(args)
    assert result["ok"] and result["status"] == "planned"
    assert "--offline" in result["commands"]["serve_foreground"]
    assert "models" in result["commands"]["prepare_models"]
    assert "--server" in result["commands"]["check_client"]
    assert not workspace.exists() and not list(repo.iterdir())
    assert sorted(p.name for p in tmp_path.iterdir()) == [repo.name]


def test_guided_join_requires_explicit_home_before_enrollment(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(onboarding, "enroll", lambda *a, **k: calls.append(a))
    args = cli.parser().parse_args(
        [
            "onboard",
            "join",
            "--keyfile",
            str(tmp_path / "invitation"),
            "--issuer",
            str(tmp_path / "issuer.pub"),
        ]
    )
    with pytest.raises(cli.OnboardingInputError, match="--agent-home"):
        cli.execute(args)
    assert calls == [] and not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", ["doctor", "models", "onboard", "demo"])
def test_failed_new_command_outputs_json_and_exits_nonzero(command, monkeypatch, capsys, tmp_path):
    if command == "doctor":
        words = ["doctor", "--format", "json"]
        monkeypatch.setattr(doctor, "diagnose", lambda *a, **k: {"ok": False, "checks": []})
    elif command == "models":
        words = ["models", "check", "--dir", str(tmp_path)]
        monkeypatch.setattr(provision, "check_models", lambda *a: {"ready": False, "models": []})
    elif command == "onboard":
        words = ["onboard", "init", "--workspace", str(tmp_path), "--repo", str(tmp_path)]
        monkeypatch.setattr(onboarding, "initialize", lambda *a, **k: {"ok": False})
    else:
        from colony import demo

        words = ["demo", "--workspace", str(tmp_path)]
        monkeypatch.setattr(demo, "run_demo", lambda *a, **k: {"ok": False, "steps": []})
    monkeypatch.setattr(sys, "argv", ["colony", *words])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result.get("ok", result.get("ready")) is False


def test_interactive_mode_requires_terminal_and_cancellation_is_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(cli.OnboardingInputError, match="terminal"):
        cli.execute(cli.parser().parse_args(["onboard", "init", "--interactive"]))
    monkeypatch.setattr("builtins.input", lambda *args: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(cli.OnboardingInputError, match="cancelled"):
        cli._prompt_path("New workspace")
    assert not list(tmp_path.iterdir())


def test_doctor_route_does_not_initialize_client_or_models(monkeypatch, tmp_path):
    from colony.client import Client

    monkeypatch.setattr(Client, "__init__", lambda *a, **k: pytest.fail("Unexpected client"))
    monkeypatch.setattr(provision, "fetch_models", lambda *a: pytest.fail("Unexpected download"))
    result = cli.execute(
        cli.parser().parse_args(
            [
                "--home",
                str(tmp_path / "absent"),
                "doctor",
                "--format",
                "json",
            ]
        )
    )
    assert not result["ok"]
    assert not list(tmp_path.iterdir())


def test_models_default_check_is_readonly_but_fetch_explicit(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        provision, "fetch_models", lambda path: calls.append(path) or {"ready": True}
    )
    result = cli.execute(cli.parser().parse_args(["models", "check", "--dir", str(tmp_path)]))
    assert not result["ready"] and calls == []
    cli.execute(cli.parser().parse_args(["models", "fetch", "--dir", str(tmp_path)]))
    assert calls == [tmp_path]


@pytest.mark.parametrize("controlled", [False, True])
def test_model_cli_only_echoes_controlled_fixed_messages(controlled, monkeypatch, capsys, tmp_path):
    def failure(path):
        if controlled:
            raise provision.ModelPreparationError("Verified local models are required")
        raise ValueError("password=private-exception-sentinel")

    monkeypatch.setattr(provision, "fetch_models", failure)
    monkeypatch.setattr(sys, "argv", ["colony", "models", "fetch", "--dir", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    message = capsys.readouterr().err
    assert "private-exception-sentinel" not in message
    if controlled:
        assert "Verified local models are required" in message
