"""Verify committed backend and AI refs together without merging PRs or changing a checkout.

Exports a conflict-free virtual Git tree into a temporary directory. Only tracked
files are used; the working tree, .env, local data and credentials are not copied.
The caller supplies an existing Python 3.12 dev venv; nothing is installed here.
"""

import argparse
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("Git verification failed: check refs or resolve branch conflicts before running.")
    return result.stdout


def resolve(ref):
    value = git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", value):
        raise RuntimeError("Git returned an invalid commit identifier")
    return value


def run(command, directory, env, timeout=600):
    result = subprocess.run(command, cwd=directory, env=env, timeout=timeout)
    if result.returncode:
        raise RuntimeError("An integration check failed; no PR, branch or working tree was changed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-ref", default="HEAD", help="Committed backend ref; working edits are excluded"
    )
    parser.add_argument("--ai-ref", default="origin/codex/ai-recommendations", help="Fetched AI commit/ref")
    parser.add_argument(
        "--container", action="store_true", help="Also build and exercise the Docker image; requires Docker"
    )
    args = parser.parse_args()
    try:
        backend_sha, ai_sha = resolve(args.backend_ref), resolve(args.ai_ref)
        tree = git("merge-tree", "--write-tree", backend_sha, ai_sha).decode().splitlines()[0]
        if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
            raise RuntimeError("Git returned an invalid tree identifier")
        archive = git("archive", "--format=tar", tree)
        app_settings = {
            "APP_ENV",
            "DATABASE_PATH",
            "ALLOWED_ORIGINS",
            "SESSION_TTL_SECONDS",
            "COOKIE_SECURE",
            "COMMIT_SHA",
            "SQLITE_WAL",
            "SQLITE_LOCAL_DISK",
        }
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("OPENAI_", "AI_", "PYTHON", "COMPOSE_")) and key not in app_settings
        }
        env.update(AI_ENABLED="false", OPENAI_API_KEY="", PYTHONDONTWRITEBYTECODE="1")
        print(f"Committed backend={backend_sha}; AI={ai_sha}; conflict-free tree={tree}", flush=True)
        with tempfile.TemporaryDirectory(prefix="career-quest-integration-") as folder:
            with tarfile.open(fileobj=io.BytesIO(archive)) as source:
                source.extractall(folder, filter="data")
            directory = Path(folder)
            if not (directory / "backend/app/ai/__init__.py").is_file():
                raise RuntimeError("The combined snapshot has no AI module")
            # Test modules import from this temporary snapshot, never the caller's checkout.
            commands = [
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                [sys.executable, "-m", "pytest", "backend/tests", "-q"],
                [sys.executable, "scripts/check_contracts.py"],
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    "backend",
                    "scripts/smoke.py",
                    "scripts/check_contracts.py",
                    "scripts/verify_integration.py",
                    "scripts/check_container.py",
                ],
                [sys.executable, "scripts/smoke.py", "--ai"],
            ]
            for command in commands:
                run(command, directory, env)
            if args.container:
                run(
                    [
                        sys.executable,
                        "scripts/check_container.py",
                        "--require-ai",
                        "--commit-sha",
                        backend_sha,
                    ],
                    directory,
                    env,
                    timeout=1200,
                )
        print("Combined integration PASS; provider selection was synthetic, no live model calls.", flush=True)
        if not args.container:
            print("Docker build/runtime not checked; use --container on a Docker-equipped host.")
        return 0
    except (RuntimeError, OSError, subprocess.TimeoutExpired, tarfile.TarError) as exc:
        print(f"Integration check stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
