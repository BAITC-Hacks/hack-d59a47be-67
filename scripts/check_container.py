"""Opt-in Docker acceptance with an isolated project and synthetic data only.

Exit 2 means Docker/Compose is unavailable; it is NOT a successful container test.
No .env, real credentials, kit, existing containers or existing volumes are used.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:8000"
HOST_ENV_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SYSTEMROOT",
    "WINDIR",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "DOCKER_CONFIG",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "SSH_AUTH_SOCK",
}

# Fixed programs, not code assembled from request/data fields. Payloads enter via stdin.
IMAGE_CHECK = r"""
import json, os
from pathlib import Path
from backend.app.ai_adapter import AIAdapter
files = [str(p.relative_to('/app')) for p in Path('/app').rglob('*') if p.is_file()]
print(json.dumps({'uid': os.getuid(), 'files': files,
    'data_empty': not any(Path('/data').iterdir()),
    'key_absent': not os.environ.get('OPENAI_API_KEY'),
    'disabled': AIAdapter(False).capability, 'enabled': AIAdapter(True).capability}))
"""

SEED = r"""
import json, sys
from backend.app.auth import hash_password
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.imports import ImportService
payload = json.load(sys.stdin)
db = Database(Settings.from_env())
db.migrate()
importer = ImportService(db)
preview = importer.preview(payload['files'])
importer.commit(payload['files'], preview['preview_token'])
with db.connect() as conn:
    conn.execute('INSERT INTO accounts VALUES (?,?,?,?,?)',
        ('CONTAINER_SYNTH_ACCOUNT', 'container.synthetic', hash_password(payload['password']),
         'employee', payload['employee']))
print('synthetic seed installed')
"""

DATABASE_CHECK = r"""
import json, os
from backend.app.config import Settings
from backend.app.database import Database
db = Database(Settings.from_env())
with db.connect() as conn:
    print(json.dumps({'uid': os.getuid(), 'ready': db.readiness(),
        'foreign_keys': conn.execute('PRAGMA foreign_keys').fetchone()[0],
        'busy_timeout': conn.execute('PRAGMA busy_timeout').fetchone()[0],
        'migrations': [tuple(row) for row in conn.execute(
            'SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version')],
        'employees': conn.execute('SELECT COUNT(*) FROM employees').fetchone()[0],
        'completions': conn.execute('SELECT COUNT(*) FROM completion_requests').fetchone()[0]}))
"""


class CheckFailure(RuntimeError):
    """Only safe, fixed diagnostic messages may be printed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def isolated_environment(source: dict[str, str], require_ai: bool, commit: str) -> dict[str, str]:
    """Allowlist host Docker access; never inherit application/Compose/provider config."""
    environment = {key: value for key, value in source.items() if key in HOST_ENV_KEYS}
    environment.update(
        COMPOSE_DISABLE_ENV_FILE="1",
        APP_ENV="test",
        AI_ENABLED=str(require_ai).lower(),
        OPENAI_API_KEY="",
        COMMIT_SHA=commit,
        ALLOWED_ORIGINS=json.dumps([ORIGIN]),
        SESSION_TTL_SECONDS="3600",
        SQLITE_WAL="false",
        SQLITE_LOCAL_DISK="false",
    )
    return environment


def loopback_url(bindings: dict) -> str:
    ports = bindings.get("8000/tcp", [])
    require(len(ports) == 1, "Expected one explicitly published application port")
    entry = ports[0]
    port = entry.get("HostPort", "")
    require(entry.get("HostIp") == "127.0.0.1", "Container port is not restricted to loopback")
    require(isinstance(port, str) and port.isdigit() and 1 <= int(port) <= 65535, "Invalid local port")
    require(set(bindings) == {"8000/tcp"}, "Unexpected additional published port")
    return f"http://127.0.0.1:{int(port)}"


def validate_image(report: dict, image_env: list[str], require_ai: bool) -> None:
    require(report.get("uid", 0) != 0, "Image executes as root")
    require(report.get("data_empty") is True, "Image contains preloaded data")
    require(report.get("key_absent") is True, "Image contains a provider credential")
    require(not any(item.startswith("OPENAI_API_KEY=") for item in image_env), "Image bakes provider config")
    permitted = re.compile(
        r"(?:requirements\.lock|backend/__init__\.py|backend/app/[a-zA-Z0-9_]+\.py|"
        r"backend/app/ai/[a-zA-Z0-9_]+\.py|backend/app/ai/requirements\.txt|"
        r"backend/migrations/[0-9]{3}_[a-z0-9_]+\.sql)"
    )
    require(bool(report.get("files")), "Image application files are missing")
    require(all(permitted.fullmatch(path) for path in report["files"]), "Image contains a forbidden file")
    require(report.get("disabled") == {"status": "not_configured", "engine": "none"}, "AI default failed")
    if require_ai:
        require(report.get("enabled") == {"status": "configured", "engine": "oleg"}, "AI module unavailable")


class Runner:
    def __init__(self, environment: dict[str, str]):
        self.environment = environment

    def run(self, command: list[str], *, stdin: str | None = None, timeout: int = 30) -> str:
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=self.environment,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CheckFailure(
                "Command unavailable or timed out; output suppressed to protect credentials"
            ) from exc
        require(result.returncode == 0, "Command failed; raw output suppressed to protect credentials")
        return result.stdout.strip()


class HTTP:
    def __init__(self):
        self.base = ""
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, path: str, expected: int = 200, *, body=None, headers=None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = self.opener.open(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            require(response.status == expected, f"HTTP check failed: {path} expected {expected}")
            return json.load(response)

    def ready(self):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                return self.call("/api/health/ready")
            except (OSError, CheckFailure):
                time.sleep(0.2)
        raise CheckFailure("Container did not become ready within 30 seconds")


def acceptance(runner: Runner, compose: list[str], project: str, require_ai: bool, commit: str):
    # Existing fixtures are explicitly synthetic. They are sent through stdin, never baked into the image.
    sys.path.insert(0, str(ROOT))
    from backend.tests.test_workflow import EMPLOYEE, EVENT, OTHER, source_batch

    print("Building isolated Docker image (may require downloading the official base image)...", flush=True)
    runner.run([*compose, "build", "backend"], timeout=600)
    image = f"{project}-backend"
    image_env = json.loads(
        runner.run(["docker", "image", "inspect", image, "--format", "{{json .Config.Env}}"])
    )
    report = json.loads(
        runner.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                f"{project}-image-check",
                "--label",
                f"com.docker.compose.project={project}",
                "--label",
                "com.docker.compose.service=backend",
                "--label",
                "com.docker.compose.oneoff=True",
                "--network",
                "none",
                "--entrypoint",
                "python",
                image,
                "-c",
                IMAGE_CHECK,
            ]
        )
    )
    validate_image(report, image_env, require_ai)
    print("PASS: build, non-root, image file allowlist, empty data and AI import boundary", flush=True)
    password = secrets.token_urlsafe(24)
    client = HTTP()
    before = None
    previous_volume = None
    expected_ai = (
        {"status": "configured", "engine": "oleg"}
        if require_ai
        else {"status": "not_configured", "engine": "none"}
    )
    for cycle in range(2):
        name = f"{project}-check-{cycle}"
        runner.run(
            [
                *compose,
                "run",
                "--no-deps",
                "--detach",
                "--name",
                name,
                "--publish",
                "127.0.0.1::8000",
                "backend",
            ],
            timeout=60,
        )
        details = json.loads(runner.run(["docker", "inspect", name]))[0]
        require(details["Config"]["Labels"].get("com.docker.compose.project") == project, "Project mismatch")
        require(all(mount["Type"] == "volume" for mount in details["Mounts"]), "Unexpected bind mount")
        mounts = [mount for mount in details["Mounts"] if mount["Destination"] == "/data"]
        require(len(mounts) == 1, "SQLite is not on its own named volume")
        volume = mounts[0]["Name"]
        require(volume == f"{project}_sqlite_data", "Unexpected SQLite volume")
        if previous_volume is not None:
            require(previous_volume == volume, "Recreated container uses a different volume")
        previous_volume = volume
        client.base = loopback_url(details["NetworkSettings"]["Ports"])
        ready = client.ready()
        require(
            ready["database"] == "ready" and ready["capabilities"]["ai"] == expected_ai, "Readiness failed"
        )
        client.call("/api/health")
        require(
            client.call("/api/version") == {"api_version": "1.0.0", "commit_sha": commit}, "Version mismatch"
        )
        if cycle == 0:
            client.call("/api/me", 401)
            require(ready["capabilities"]["dataset"]["status"] == "not_loaded", "Fresh volume is not empty")
            runner.run(
                ["docker", "exec", "-i", name, "python", "-c", SEED],
                stdin=json.dumps(
                    {
                        "files": source_batch(),
                        "password": password,
                        "employee": EMPLOYEE,
                    }
                ),
            )
            login = client.call(
                "/api/auth/login",
                body={"username": "container.synthetic", "password": password},
                headers={"Origin": ORIGIN},
            )
            csrf = {
                "Origin": ORIGIN,
                "X-CSRF-Token": login["csrf_token"],
                "Idempotency-Key": "container-replay",
            }
            profile = client.call(f"/api/employees/{EMPLOYEE}")
            payload = {
                "expected_state_version": profile["state_version"],
                "event_id": EVENT,
                "mode": "demo_simulation",
            }
            saved = client.call(f"/api/employees/{EMPLOYEE}/completions", body=payload, headers=csrf)
            client.call(f"/api/employees/{OTHER}", 403)
            client.call("/api/hr/summary", 403)
            client.call("/api/auth/logout", 403, body={}, headers={"Origin": ORIGIN})
        else:
            require(client.call("/api/me")["employee_id"] == EMPLOYEE, "Session lost after recreation")
            replay = client.call(f"/api/employees/{EMPLOYEE}/completions", body=payload, headers=csrf)
            require(replay == saved, "Lost-response completion replay changed after recreation")
            client.call("/api/auth/logout", body={}, headers=csrf)
            client.call("/api/me", 401)
        state = json.loads(runner.run(["docker", "exec", name, "python", "-c", DATABASE_CHECK]))
        require(state["uid"] != 0 and state["ready"], "Database/non-root check failed")
        require(state["foreign_keys"] == 1 and state["busy_timeout"] >= 5000, "SQLite configuration failed")
        require(
            state["employees"] == 2 and state["completions"] == 1 and bool(state["migrations"]),
            "Persistence failed",
        )
        if before is not None:
            require(state == before, "Data or applied migrations changed on container recreation")
        before = state
        runner.run(["docker", "rm", "--force", name])
        print(f"PASS: container cycle {cycle + 1}, health/readiness/version/auth/rights/SQLite", flush=True)
    print(
        "PASS: named volume, migrations, session and exact completion replay survive container recreation",
        flush=True,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-ai", action="store_true", help="Require AI module import; no provider calls or key"
    )
    parser.add_argument("--commit-sha", help="Hexadecimal source commit when checking an exported snapshot")
    args = parser.parse_args(argv)
    if args.commit_sha is not None and not re.fullmatch(r"[0-9a-fA-F]{7,64}", args.commit_sha):
        parser.error("--commit-sha must be a hexadecimal commit ID (7–64 characters)")
    if shutil.which("docker") is None:
        print("NOT RUN: Docker is unavailable; container build/runtime remain unverified.", file=sys.stderr)
        return 2
    commit = args.commit_sha.lower() if args.commit_sha else "unknown"
    if args.commit_sha is None and (ROOT / ".git").exists() and shutil.which("git"):
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            commit = "unknown"
    runner = Runner(isolated_environment(dict(os.environ), args.require_ai, commit))
    try:
        require(
            runner.run(["docker", "info", "--format", "{{.OSType}}"]) == "linux",
            "Linux Docker daemon required",
        )
        runner.run(["docker", "compose", "version"])
    except CheckFailure:
        print(
            "NOT RUN: Docker daemon/Compose is unavailable; container build/runtime remain unverified.",
            file=sys.stderr,
        )
        return 2
    project = "cqcheck" + secrets.token_hex(12)
    result = 0
    with tempfile.TemporaryDirectory(prefix="career-quest-container-check-") as directory:
        env_file = Path(directory) / "empty.env"
        env_file.write_text("", encoding="utf-8")
        compose = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(env_file),
            "--file",
            str(ROOT / "compose.yaml"),
        ]
        try:
            acceptance(runner, compose, project, args.require_ai, commit)
        except (CheckFailure, OSError, ValueError, KeyError, ImportError) as exc:
            message = (
                str(exc)
                if isinstance(exc, CheckFailure)
                else "Acceptance could not complete; inspect the local setup"
            )
            print(f"FAIL: {message}", file=sys.stderr)
            result = 1
        finally:
            # Only this random project; no prune/default projects/base images/cache.
            # compose rm explicitly includes one-off containers created by compose run.
            for cleanup in (
                [*compose, "rm", "--stop", "--force"],
                [*compose, "down", "--volumes", "--remove-orphans", "--rmi", "local"],
            ):
                try:
                    runner.run(cleanup, timeout=60)
                except CheckFailure:
                    print(
                        f"Cleanup incomplete for isolated project {project}; no global cleanup attempted.",
                        file=sys.stderr,
                    )
                    result = 1
    if result == 0:
        print("Container acceptance PASS (synthetic data; no live AI call).")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
