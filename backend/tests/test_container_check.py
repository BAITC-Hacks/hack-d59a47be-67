"""Safety/validation tests for the opt-in Docker runner, without a Docker daemon."""

from types import SimpleNamespace

import pytest

from scripts import check_container as check


def report():
    return {
        "uid": 10001,
        "files": ["requirements.lock", "backend/app/main.py", "backend/migrations/001_initial.sql"],
        "data_empty": True,
        "key_absent": True,
        "disabled": {"status": "not_configured", "engine": "none"},
        "enabled": {"status": "configured", "engine": "oleg"},
    }


def test_environment_discards_credentials_application_and_compose_overrides():
    environment = check.isolated_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/synthetic",
            "DOCKER_HOST": "unix:///synthetic.sock",
            "OPENAI_API_KEY": "must-never-leak",
            "OPENAI_BASE_URL": "https://untrusted.invalid",
            "AI_ENABLED": "true",
            "COMPOSE_FILE": "another-project.yaml",
            "COMPOSE_PROJECT_NAME": "real",
            "COMPOSE_ENV_FILES": "/real/.env",
            "DATABASE_PATH": "/real/data.sqlite3",
            "HTTP_PROXY": "http://user:secret@proxy.invalid",
            "PRIVATE_SERVICE_TOKEN": "sensitive",
        },
        False,
        "abcdef0",
    )
    assert environment["DOCKER_HOST"] == "unix:///synthetic.sock"
    assert environment["HOME"] == "/synthetic"
    assert environment["OPENAI_API_KEY"] == ""
    assert environment["AI_ENABLED"] == "false"
    assert environment["COMPOSE_DISABLE_ENV_FILE"] == "1"
    assert not set(environment) & {
        "COMPOSE_FILE",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_ENV_FILES",
        "DATABASE_PATH",
        "OPENAI_BASE_URL",
        "HTTP_PROXY",
        "PRIVATE_SERVICE_TOKEN",
    }


def test_only_loopback_with_valid_single_port_is_accepted():
    assert check.loopback_url({"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "48123"}]}) == (
        "http://127.0.0.1:48123"
    )


@pytest.mark.parametrize(
    "ports",
    [
        {},
        {"8000/tcp": []},
        {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}]},
        {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "0"}]},
        {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "65536"}]},
        {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}], "22/tcp": []},
    ],
)
def test_external_ambiguous_and_invalid_bindings_fail(ports):
    with pytest.raises(check.CheckFailure):
        check.loopback_url(ports)


def test_image_allows_runtime_code_and_requires_optional_ai_when_requested():
    check.validate_image(report(), ["DATABASE_PATH=/data/career_quest.sqlite3"], True)
    absent = report()
    absent["enabled"] = {"status": "not_configured", "engine": "none"}
    check.validate_image(absent, [], False)
    with pytest.raises(check.CheckFailure, match="AI module unavailable"):
        check.validate_image(absent, [], True)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "data/raw/employees.json",
        "backend/app/ai/.env",
        "backend/app/ai/fixture.json",
        "backend/app/key.pem",
        "var/career_quest.sqlite3",
        "frontend/private.txt",
        "../secret.py",
    ],
)
def test_image_rejects_data_credentials_and_extra_files(path):
    baked = report()
    baked["files"].append(path)
    with pytest.raises(check.CheckFailure, match="forbidden file"):
        check.validate_image(baked, [], False)


@pytest.mark.parametrize("field,value", [("uid", 0), ("data_empty", False), ("key_absent", False)])
def test_image_rejects_root_baked_data_or_provider_credentials(field, value):
    baked = report()
    baked[field] = value
    with pytest.raises(check.CheckFailure):
        check.validate_image(baked, [], False)


def test_image_config_cannot_bake_even_an_empty_provider_key():
    with pytest.raises(check.CheckFailure, match="provider config"):
        check.validate_image(report(), ["OPENAI_API_KEY="], False)


def test_missing_docker_returns_not_run_without_starting_any_process(monkeypatch, capsys):
    monkeypatch.setattr(check.shutil, "which", lambda executable: None)
    monkeypatch.setattr(check.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected process"))
    assert check.main([]) == 2
    assert "NOT RUN" in capsys.readouterr().err


def test_command_failure_does_not_expose_stdout_stderr_or_credentials(monkeypatch):
    monkeypatch.setattr(
        check.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="sensitive stdout", stderr="sensitive stderr"
        ),
    )
    with pytest.raises(check.CheckFailure) as caught:
        check.Runner({}).run(["docker", "info"])
    assert "sensitive" not in str(caught.value)


def test_failure_cleans_only_generated_project_and_supports_snapshot_without_git(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check.shutil, "which", lambda executable: "/synthetic/bin/" + executable)
    commands = []

    def run(self, command, **kwargs):
        commands.append(command)
        return "linux" if command[:2] == ["docker", "info"] else ""

    def fail(runner, compose, project, require_ai, commit):
        assert project.startswith("cqcheck") and len(project) == 31
        assert compose[compose.index("--project-name") + 1] == project
        assert compose[compose.index("--file") + 1] == str(tmp_path / "compose.yaml")
        assert check.Path(compose[compose.index("--env-file") + 1]).read_text() == ""
        assert commit == "unknown"
        raise check.CheckFailure("synthetic failure")

    monkeypatch.setattr(check.Runner, "run", run)
    monkeypatch.setattr(check, "acceptance", fail)
    assert check.main([]) == 1
    cleanup = commands[-1]
    assert cleanup[-5:] == ["down", "--volumes", "--remove-orphans", "--rmi", "local"]
    assert "prune" not in cleanup
    assert "PASS" not in capsys.readouterr().out


def test_explicit_invalid_commit_fails_before_running_docker(monkeypatch):
    monkeypatch.setattr(check.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected process"))
    with pytest.raises(SystemExit) as caught:
        check.main(["--commit-sha", "not-a-commit"])
    assert caught.value.code == 2
