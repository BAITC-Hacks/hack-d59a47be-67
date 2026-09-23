#!/usr/bin/env python3
"""Copy the seven starter-kit files into a local directory, without changing source."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import stat
import zipfile

FILES = (
    "README.md", "README.ru.md", "README.kz.md", "employees.json",
    "events.json", "skills.json", "activity_history.csv",
)
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024


def safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or ".." in path.parts or ":" in name:
        raise ValueError(f"Unsafe source path: {name!r}")
    return path


def read_source(source: Path) -> dict[str, bytes]:
    """Never use extractall; read only a complete, unambiguous kit."""
    if source.is_dir():
        result = {}
        for name in FILES:
            path = source / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Expected a regular source file: {name}")
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"Source file too large: {name}")
            result[name] = path.read_bytes()
    else:
        with zipfile.ZipFile(source) as archive:
            candidates: dict[str, dict[str, zipfile.ZipInfo]] = {}
            seen = set()
            if len(archive.infolist()) > 10000:
                raise ValueError("Archive contains too many entries")
            for entry in archive.infolist():
                path = safe_path(entry.filename)
                normalized_name = str(path)
                if normalized_name in seen:
                    raise ValueError(f"Duplicate ZIP entry: {entry.filename}")
                seen.add(normalized_name)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError(f"Symbolic link in ZIP: {entry.filename}")
                if entry.is_dir() or "__MACOSX" in path.parts or path.name not in FILES:
                    continue
                if entry.file_size > MAX_FILE_BYTES:
                    raise ValueError(f"Source file too large: {entry.filename}")
                candidates.setdefault(str(path.parent), {})[path.name] = entry
            complete = [items for items in candidates.values() if set(items) == set(FILES)]
            if len(complete) != 1:
                raise ValueError("ZIP must contain exactly one complete seven-file kit")
            if sum(entry.file_size for entry in complete[0].values()) > MAX_TOTAL_BYTES:
                raise ValueError("Source kit too large")
            result = {name: archive.read(complete[0][name]) for name in FILES}
    if sum(map(len, result.values())) > MAX_TOTAL_BYTES:
        raise ValueError("Source kit too large")
    return result


def prepare(source: Path, destination: Path) -> dict[str, str]:
    payloads = read_source(source)
    for path in (destination, *destination.parents):
        if path.is_symlink():
            raise ValueError(f"Destination may not contain symbolic links: {path}")
    # Validate every target before writing; an existing different file is never overwritten.
    for name, contents in payloads.items():
        target = destination / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"Destination is not a regular file: {target}")
        if target.exists() and target.read_bytes() != contents:
            raise ValueError(f"Destination differs; choose an empty directory: {target}")
    destination.mkdir(parents=True, exist_ok=True)
    for name, contents in payloads.items():
        target = destination / name
        if not target.exists():
            # Exclusive creation refuses a target created by another process meanwhile.
            with target.open("xb") as stream:
                stream.write(contents)
    return {name: hashlib.sha256(contents).hexdigest() for name, contents in payloads.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="ZIP or directory containing the seven kit files")
    parser.add_argument("--destination", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    try:
        hashes = prepare(args.source, args.destination)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        parser.exit(2, f"Dataset preparation failed: {error}\n")
    print(f"Prepared {len(hashes)} files in {args.destination}")
    for name, digest in hashes.items():
        print(f"{digest}  {name}")


if __name__ == "__main__":
    main()
