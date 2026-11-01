#!/usr/bin/env python3
"""Immutable input snapshots and crash-visible campaign publication."""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

SCHEMA = "pump-admin-cto-campaign-attempt-v1"


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def compressed_json(value) -> bytes:
    return gzip.compress(canonical_json(value), mtime=0)


class InputSnapshot:
    """Copy declared inputs once, then detect mutation of their source paths."""

    def __init__(self, paths: dict[str, Path], expected: dict[str, str] | None = None):
        self.sources = {name: Path(path).resolve() for name, path in paths.items()}
        self.expected = expected or {}
        self.temp = None
        self.paths = {}
        self.hashes = {}

    def __enter__(self):
        unknown = sorted(set(self.expected) - set(self.sources))
        if unknown:
            raise ValueError(f"expected hashes name unknown inputs: {unknown}")
        self.temp = tempfile.TemporaryDirectory(prefix="pump-admin-cto-fuzz-inputs-")
        root = Path(self.temp.name)
        try:
            for index, (name, source) in enumerate(sorted(self.sources.items())):
                raw = source.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if name in self.expected and self.expected[name] != digest:
                    raise ValueError(f"input hash mismatch for {name}: expected {self.expected[name]}, got {digest}")
                target = root / f"{index:02d}-{name}.bin"
                target.write_bytes(raw)
                self.paths[name] = target
                self.hashes[name] = digest
        except Exception:
            self.temp.cleanup()
            self.temp = None
            raise
        return self

    def changed_sources(self) -> dict[str, dict[str, str]]:
        changed = {}
        for name, path in self.sources.items():
            actual = file_sha(path)
            if actual != self.hashes[name]:
                changed[name] = {"before": self.hashes[name], "after": actual}
        return changed

    def __exit__(self, exc_type, exc, traceback):
        if self.temp is not None:
            self.temp.cleanup()


class CampaignPublisher:
    """Publish one immutable directory or retain a visible interrupted attempt."""

    def __init__(self, output: Path, attempt: dict):
        self.output = Path(output).resolve()
        self.attempt = dict(attempt)
        self.lock_handle = None
        self.stage = None
        self.committed = False

    def __enter__(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.output.parent / (self.output.name + ".lock")
        self.lock_handle = lock_path.open("a+b")
        try:
            fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.lock_handle.close()
            raise FileExistsError(f"campaign destination is locked: {self.output}") from error
        if self.output.exists() or self.output.is_symlink():
            self._unlock()
            raise FileExistsError(f"refusing to overwrite campaign: {self.output}")
        suffix = f".partial-{os.getpid()}-{uuid.uuid4().hex}"
        self.stage = self.output.parent / ("." + self.output.name + suffix)
        self.stage.mkdir(mode=0o700)
        self.attempt.update(schema=SCHEMA, status="in_progress", output_name=self.output.name)
        self.write_json("attempt.json", self.attempt)
        return self

    def _target(self, relative: str) -> Path:
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts or not value.parts:
            raise ValueError("publication path must remain inside the campaign")
        target = self.stage / value
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_bytes(self, relative: str, raw: bytes) -> None:
        target = self._target(relative)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"refusing to overwrite staged artifact: {relative}")
        with target.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

    def write_json(self, relative: str, value, *, compressed=False) -> None:
        self.write_bytes(relative, compressed_json(value) if compressed else canonical_json(value))

    def commit(self, final_attempt: dict) -> Path:
        if self.committed:
            raise RuntimeError("campaign already committed")
        complete = dict(self.attempt)
        complete.update(final_attempt, status="complete")
        self.write_json("complete.json", complete)
        directory = os.open(self.stage, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        os.rename(self.stage, self.output)
        parent = os.open(self.output.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        self.committed = True
        return self.output

    def _unlock(self):
        if self.lock_handle is not None and not self.lock_handle.closed:
            fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_UN)
            self.lock_handle.close()

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None and self.stage is not None and self.stage.exists() and not self.committed:
            try:
                self.write_json("interrupted.json", {"schema": SCHEMA, "status": "interrupted",
                                                       "error_type": exc_type.__name__})
            except (OSError, ValueError):
                pass
        self._unlock()


def find_partial_attempts(output: Path) -> list[Path]:
    output = Path(output).resolve()
    return sorted(output.parent.glob("." + output.name + ".partial-*"))
