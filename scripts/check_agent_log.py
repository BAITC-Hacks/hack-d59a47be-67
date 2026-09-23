#!/usr/bin/env python3
"""Require AGENTS.md alongside a staged change or a branch diff.

This checks that the journal changed, not the truth or completeness of its entry.
Agents still have to describe decisions, verification and next steps themselves.
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--base", help="Compare merge base with HEAD, for CI/PRs")
    args = parser.parse_args()
    command = ["git", "diff", "--name-only", "-z"]
    command += ["--cached"] if args.staged else [f"{args.base}...HEAD"]
    try:
        result = subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        print(exc.stderr.decode(errors="replace"), file=sys.stderr)
        return 2
    changed = set(result.stdout.decode().strip("\0").split("\0")) - {""}
    if changed - {"AGENTS.md"} and "AGENTS.md" not in changed:
        print("Обновите AGENTS.md в том же коммите: что изменено, проверки и следующий шаг.", file=sys.stderr)
        return 1
    print("AGENTS.md: журнал включён в изменение (или изменение пустое).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
