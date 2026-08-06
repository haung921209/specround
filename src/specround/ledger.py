"""The append-only event log (G3, G5, G10).

One JSON object per line, in a plain file. Appending is the only mutation this
module offers — there is no update and no delete, and the reader refuses a file
whose positions no longer match the ``seq`` its records claim, so a hand edit
that removes or reorders history shows up as an error instead of a quieter
wrong answer.

Nothing here runs git. A ledger is a file next to a document; committing it is
a way to share and back up history, never a precondition for recording it.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from specround.errors import SchemaError
from specround.events import (
    SCHEMA,
    canonical_json,
    check_event_type,
    check_schema_compatible,
    derive_id,
    validate_event,
)
from specround.fold import State, fold

try:  # pragma: no cover - platform dependent
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None  # type: ignore[assignment]

Clock = Callable[[], str]


def utc_now() -> str:
    """Timestamps are UTC, second resolution, and never used for ordering."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ledger:
    """An append-only JSONL event log."""

    def __init__(self, path: Path, *, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self._clock: Clock = clock or utc_now

    # -- reading ---------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> list[dict[str, Any]]:
        """Return every record, in file order, validated."""
        if not self.path.is_file():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            return self._parse(handle.read().splitlines())

    def count(self) -> int:
        return len(self.read())

    def state(self) -> State:
        """Fold the whole log into the current state."""
        return fold(self.read())

    def _parse(self, lines: list[str]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            if not line.strip():
                raise SchemaError(
                    f"{self.path}:{index + 1}: blank line — the ledger is one record per line"
                )
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"{self.path}:{index + 1}: not valid JSON ({exc})") from exc
            try:
                validate_event(record)
            except SchemaError as exc:
                raise SchemaError(f"{self.path}:{index + 1}: {exc}") from exc
            if record["seq"] != index:
                raise SchemaError(
                    f"{self.path}:{index + 1}: record claims seq {record['seq']} but sits at "
                    f"position {index} — history was reordered or truncated"
                )
            records.append(record)
        return records

    # -- appending -------------------------------------------------------

    @contextmanager
    def _exclusive(self) -> Iterator[Any]:
        """Hold the log open for append with an exclusive lock.

        The lock spans read-then-write because ``seq`` is assigned from the
        current length: two writers without it would hand out the same seq.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8", newline="\n")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield handle
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Validate ``event`` against the whole history, then write one line.

        The prospective history is folded before anything is written, so a
        record that would contradict the log (an unknown round, a comment on a
        closed round, a second verdict on a settled comment) is rejected and the
        file is left untouched. Returns the stored record.
        """
        record = dict(event)
        record.setdefault("schema", SCHEMA)
        # Check the type before touching the file or deriving an id, so a typo in
        # the type reports the typo rather than a downstream symptom.
        check_schema_compatible(record["schema"])
        check_event_type(record.get("type"))
        with self._exclusive() as handle:
            handle.seek(0)
            prior = self._parse(handle.read().splitlines())
            record["seq"] = len(prior)
            record.setdefault("ts", self._clock())
            if not record.get("id"):
                record["id"] = derive_id(record)
            validate_event(record)
            # The reader is the validator: folding prior + record raises on any
            # cross-record violation before this becomes durable.
            fold([*prior, record])
            handle.write(canonical_json(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record
