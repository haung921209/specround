"""The log: append-only, self-describing positions, and refusals."""

import json

import pytest

from specround.errors import InvariantError, LedgerError, SchemaError
from specround.events import SCHEMA, canonical_json
from specround.fold import fold
from specround.ledger import Ledger, utc_now


@pytest.fixture
def ledger(tmp_path, clock):
    return Ledger(tmp_path / ".specround" / "ledger.jsonl", clock=clock)


def open_round(ledger, **overrides):
    return ledger.append(
        {
            "type": "round.open",
            "author": "alice",
            "doc": "spec.md",
            "base": "sha256:" + "1" * 64,
            **overrides,
        }
    )


def test_a_missing_ledger_reads_as_empty(ledger):
    assert ledger.exists() is False
    assert ledger.read() == []
    assert ledger.count() == 0
    assert ledger.state().rounds == {}


def test_append_creates_the_directory_and_the_file(ledger):
    open_round(ledger)
    assert ledger.path.is_file()
    assert ledger.path.parent.is_dir()


def test_append_fills_in_the_envelope(ledger):
    record = open_round(ledger)
    assert record["schema"] == SCHEMA
    assert record["seq"] == 0
    assert record["ts"] == "2020-01-01T00:00:01Z"
    assert record["id"].startswith("r-")


def test_seq_counts_up_from_zero(ledger):
    round_id = open_round(ledger)["id"]
    second = ledger.append(
        {"type": "comment.add", "author": "bob", "round": round_id, "body": "why?"}
    )
    third = ledger.append({"type": "reply", "author": "alice", "target": second["id"], "body": "because"})
    assert [r["seq"] for r in (second, third)] == [1, 2]
    assert [r["seq"] for r in ledger.read()] == [0, 1, 2]


def test_earlier_lines_are_never_rewritten(ledger):
    open_round(ledger)
    before = ledger.path.read_bytes()
    round_id = ledger.read()[0]["id"]
    ledger.append({"type": "comment.add", "author": "bob", "round": round_id, "body": "why?"})
    after = ledger.path.read_bytes()
    # Append-only in the literal sense: the old bytes are a prefix of the new.
    assert after.startswith(before)


def test_each_record_is_exactly_one_line(ledger):
    round_id = open_round(ledger)["id"]
    ledger.append(
        {
            "type": "comment.add",
            "author": "bob",
            "round": round_id,
            "body": "first line\nsecond line\n",
        }
    )
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["body"] == "first line\nsecond line\n"


def test_stored_lines_are_canonical_json(ledger):
    record = open_round(ledger)
    first_line = ledger.path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == canonical_json(record)


def test_a_caller_supplied_id_is_kept(ledger):
    record = open_round(ledger, id="r-custom")
    assert record["id"] == "r-custom"
    assert ledger.read()[0]["id"] == "r-custom"


def test_a_caller_supplied_timestamp_is_kept(ledger):
    record = open_round(ledger, ts="1999-12-31T23:59:59Z")
    assert record["ts"] == "1999-12-31T23:59:59Z"


def test_a_truncated_ledger_is_reported_not_folded(ledger):
    """I2 is an invariant, so reading a truncated file raises one.

    Not a ``SchemaError``: every record here is well formed on its own, and
    what is wrong is the history they sit in. The fold has always said so — the
    reader used to say something else, and the two disagreed about the exit
    code a caller gets.
    """
    round_id = open_round(ledger)["id"]
    ledger.append({"type": "comment.add", "author": "bob", "round": round_id, "body": "why?"})
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    # Drop the first line by hand: every remaining seq is now off by one.
    ledger.path.write_text(lines[1] + "\n", encoding="utf-8")
    with pytest.raises(InvariantError, match="reordered or truncated"):
        ledger.read()


def test_a_reordered_ledger_is_reported(ledger):
    round_id = open_round(ledger)["id"]
    ledger.append({"type": "comment.add", "author": "bob", "round": round_id, "body": "why?"})
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    ledger.path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    with pytest.raises(InvariantError, match="reordered or truncated"):
        ledger.read()


def test_reading_and_folding_agree_on_a_reordered_ledger(ledger):
    """One rule, one implementation — the reader and the fold cannot drift.

    Both paths raise the same class with the same sentence; only the file
    location the reader can add is different.
    """
    round_id = open_round(ledger)["id"]
    ledger.append({"type": "comment.add", "author": "bob", "round": round_id, "body": "why?"})
    records = ledger.read()
    swapped = [dict(records[1]), dict(records[0])]

    with pytest.raises(InvariantError) as folded:
        fold(swapped)
    ledger.path.write_text(
        "\n".join(canonical_json(r) for r in swapped) + "\n", encoding="utf-8"
    )
    with pytest.raises(InvariantError) as read:
        ledger.read()
    assert str(folded.value) in str(read.value)


def test_a_corrupt_line_names_its_line_number(ledger):
    open_round(ledger)
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    with pytest.raises(SchemaError, match=r":2: not valid JSON"):
        ledger.read()


def test_a_blank_line_is_corruption(ledger):
    open_round(ledger)
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(SchemaError, match=r":2: blank line"):
        ledger.read()


def test_an_invalid_record_names_its_line_number(ledger):
    open_round(ledger)
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"schema": SCHEMA, "seq": 1, "type": "reply"}) + "\n")
    with pytest.raises(SchemaError, match=r":2: reply record: missing required field"):
        ledger.read()


def test_a_rejected_append_leaves_the_file_untouched(ledger):
    open_round(ledger)
    before = ledger.path.read_bytes()
    with pytest.raises(InvariantError, match="unknown round"):
        ledger.append(
            {"type": "comment.add", "author": "bob", "round": "r-nonexistent", "body": "why?"}
        )
    assert ledger.path.read_bytes() == before


def test_a_schema_violation_is_refused_before_writing(ledger):
    with pytest.raises(SchemaError, match="unknown event type"):
        ledger.append({"type": "comment.delete", "author": "bob", "round": "r-1"})
    assert ledger.read() == []


def test_two_handles_on_one_file_keep_the_sequence_contiguous(tmp_path, clock):
    path = tmp_path / ".specround" / "ledger.jsonl"
    first = Ledger(path, clock=clock)
    second = Ledger(path, clock=clock)
    round_id = open_round(first)["id"]
    second.append({"type": "comment.add", "author": "bob", "round": round_id, "body": "a"})
    first.append({"type": "comment.add", "author": "carol", "round": round_id, "body": "b"})
    # Each writer reads the current length under the lock, so nothing collides.
    assert [r["seq"] for r in first.read()] == [0, 1, 2]
    assert len(second.state().comments) == 2


def test_default_clock_is_utc_second_resolution():
    stamp = utc_now()
    assert stamp.endswith("Z")
    assert len(stamp) == len("2020-01-01T00:00:00Z")


def test_a_ledger_missing_its_final_newline_is_not_spliced(ledger):
    """An editor that strips the trailing newline must not cost the history.

    The reader accepts such a file — the records in it are intact — so the
    append has to put the terminator back before writing, or the new record
    lands on the end of the last line and every later read fails on a physical
    line holding two objects.
    """
    round_id = open_round(ledger)["id"]
    stripped = ledger.path.read_bytes().rstrip(b"\n")
    ledger.path.write_bytes(stripped)

    ledger.append({"type": "comment.add", "author": "bob", "round": round_id, "body": "why?"})

    assert [r["seq"] for r in ledger.read()] == [0, 1]
    assert ledger.path.read_bytes().startswith(stripped)
    assert ledger.path.read_text(encoding="utf-8").splitlines()[0] == stripped.decode("utf-8")


def test_a_rejected_append_does_not_restore_the_terminator(ledger):
    """Healing the line ending is part of writing, not of trying.

    A refused append leaves the file byte-identical, terminator included: the
    caller is told no and nothing on disk moved.
    """
    open_round(ledger)
    ledger.path.write_bytes(ledger.path.read_bytes().rstrip(b"\n"))
    before = ledger.path.read_bytes()
    with pytest.raises(InvariantError, match="unknown round"):
        ledger.append(
            {"type": "comment.add", "author": "bob", "round": "r-nonexistent", "body": "why?"}
        )
    assert ledger.path.read_bytes() == before


def test_appending_without_a_lock_primitive_is_refused(ledger, monkeypatch):
    """No lock, no append — the alternative is a ledger nobody can read.

    ``fcntl`` is missing on Windows, where the import at the top of the module
    fails and leaves ``None``. Writing anyway hands two writers the same
    ``seq``, and a reader refuses the whole file for it (I2). A platform that
    cannot hold the lock gets a refusal, not a coin flip.
    """
    monkeypatch.setattr("specround.ledger.fcntl", None)
    with pytest.raises(LedgerError, match="exclusive file lock"):
        open_round(ledger)
    assert ledger.exists() is False


def test_concurrent_writers_without_a_lock_leave_the_ledger_readable(ledger, monkeypatch):
    """The reviewer's scenario: twelve threads, no lock.

    Before the fix this produced five physical lines carrying ``seq``
    ``[0, 1, 2, 3, 3]``, and folding the result raised on the duplicate — the
    history was gone. Now every writer is refused and the file that survives is
    the one the last legal append left.
    """
    import threading

    round_id = open_round(ledger)["id"]
    monkeypatch.setattr("specround.ledger.fcntl", None)
    failures: list[Exception] = []

    def write(index: int) -> None:
        try:
            ledger.append(
                {"type": "comment.add", "author": f"w{index}", "round": round_id, "body": f"b{index}"}
            )
        except Exception as exc:  # noqa: BLE001 - the point is that it raised
            failures.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(failures) == 12
    monkeypatch.undo()
    assert [r["seq"] for r in ledger.read()] == [0]
    assert ledger.state().count == 1
