"""Schema validation — the format is the contract, so it is checked as one."""

import json

import pytest

from specround.errors import SchemaError
from specround.events import (
    EVENT_TYPES,
    SCHEMA,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    TERMINAL_VERDICTS,
    VERDICTS,
    canonical_json,
    derive_id,
    parse_schema,
    validate_event,
)


def valid(kind: str, **overrides):
    """A minimal valid record of each kind."""
    base = {
        "schema": SCHEMA,
        "seq": 0,
        "ts": "2020-01-01T00:00:01Z",
        "type": kind,
        "id": "x-000000000000",
        "author": "alice",
    }
    payloads = {
        "round.open": {"doc": "spec.md", "base": "sha256:" + "0" * 64},
        "comment.add": {"round": "r-1", "body": "why 30 seconds?"},
        "suggestion.add": {"round": "r-1", "patch": "-30\n+60\n"},
        "reply": {"target": "c-1", "body": "because of the proxy"},
        "disposition": {"target": "c-1", "verdict": "applied", "reason": "agreed"},
        "round.close": {"round": "r-1"},
    }
    return {**base, **payloads[kind], **overrides}


@pytest.mark.parametrize("kind", EVENT_TYPES)
def test_every_event_type_has_a_valid_minimal_form(kind):
    validate_event(valid(kind))


@pytest.mark.parametrize("kind", EVENT_TYPES)
def test_missing_any_required_field_is_rejected(kind):
    record = valid(kind)
    for name in record:
        incomplete = {k: v for k, v in record.items() if k != name}
        with pytest.raises(SchemaError):
            validate_event(incomplete)


def test_unknown_event_type_is_rejected():
    with pytest.raises(SchemaError, match="unknown event type"):
        validate_event(valid("comment.add", type="comment.delete"))


def test_unknown_field_is_rejected_and_ext_is_the_escape_hatch():
    with pytest.raises(SchemaError, match="unknown field"):
        validate_event(valid("comment.add", line=42))
    validate_event(valid("comment.add", ext={"line": 42}))
    with pytest.raises(SchemaError, match="'ext' must be an object"):
        validate_event(valid("comment.add", ext="line 42"))


def test_a_foreign_or_future_schema_is_refused_not_guessed():
    with pytest.raises(SchemaError, match="major version 1"):
        validate_event(valid("comment.add", schema=f"{SCHEMA_NAME}/v1"))
    with pytest.raises(SchemaError, match="foreign ledger schema"):
        validate_event(valid("comment.add", schema="other.tool/v0"))
    with pytest.raises(SchemaError, match="malformed schema"):
        validate_event(valid("comment.add", schema="specround.ledger"))
    with pytest.raises(SchemaError, match="missing a 'schema' field"):
        validate_event({"type": "reply"})


def test_parse_schema_splits_name_and_major():
    assert parse_schema(SCHEMA) == (SCHEMA_NAME, SCHEMA_VERSION)


def test_empty_strings_are_rejected_where_content_is_required():
    with pytest.raises(SchemaError, match="'body' must not be empty"):
        validate_event(valid("comment.add", body=""))
    with pytest.raises(SchemaError, match="'reason' must not be empty"):
        validate_event(valid("disposition", reason=""))
    with pytest.raises(SchemaError, match="'author' must not be empty"):
        validate_event(valid("reply", author=""))


def test_optional_text_may_be_empty():
    validate_event(valid("round.open", title=""))
    validate_event(valid("suggestion.add", body=""))


def test_wrong_types_are_rejected():
    with pytest.raises(SchemaError, match="'seq' must be an integer"):
        validate_event(valid("reply", seq="0"))
    with pytest.raises(SchemaError, match="'seq' must not be negative"):
        validate_event(valid("reply", seq=-1))
    with pytest.raises(SchemaError, match="'body' must be a string"):
        validate_event(valid("reply", body=["a"]))
    with pytest.raises(SchemaError, match="'unresolved' must be a list"):
        validate_event(valid("round.close", unresolved="c-1"))
    with pytest.raises(SchemaError, match="'unresolved' must be a list"):
        validate_event(valid("round.close", unresolved=["c-1", ""]))
    with pytest.raises(SchemaError, match="must be a JSON object"):
        validate_event(["not", "a", "record"])


def test_seq_being_a_bool_is_not_an_integer():
    # bool is an int subclass; a `true` in the seq field is corruption.
    with pytest.raises(SchemaError, match="'seq' must be an integer"):
        validate_event(valid("reply", seq=True))


def test_verdict_vocabulary_is_closed():
    for verdict in VERDICTS:
        validate_event(valid("disposition", verdict=verdict))
    with pytest.raises(SchemaError, match="unknown verdict"):
        validate_event(valid("disposition", verdict="wontfix"))


def test_deferred_is_the_only_non_terminal_verdict():
    assert set(VERDICTS) - set(TERMINAL_VERDICTS) == {"deferred"}


def test_anchor_is_validated_as_part_of_the_record():
    good = {"exact": "30 seconds", "start": 10, "end": 20}
    validate_event(valid("comment.add", anchor=good))
    with pytest.raises(SchemaError, match="span and quote disagree"):
        validate_event(valid("comment.add", anchor={"exact": "30", "start": 0, "end": 9}))
    with pytest.raises(SchemaError, match="unknown anchor field"):
        validate_event(valid("comment.add", anchor={**good, "line": 3}))
    with pytest.raises(SchemaError, match="'anchor' must be an object"):
        validate_event(valid("comment.add", anchor="30 seconds"))


def test_canonical_json_is_stable_and_readable():
    record = valid("comment.add", body="한글 body")
    once = canonical_json(record)
    assert once == canonical_json(dict(reversed(list(record.items()))))
    assert json.loads(once) == record
    assert "한글" in once  # not \u-escaped: a human can read the log
    assert "\n" not in once  # one record is always one line


def test_derived_ids_are_prefixed_by_kind_and_stable():
    for kind, prefix in [
        ("round.open", "r-"),
        ("comment.add", "c-"),
        ("suggestion.add", "s-"),
        ("reply", "p-"),
        ("disposition", "d-"),
        ("round.close", "x-"),
    ]:
        record = {k: v for k, v in valid(kind).items() if k != "id"}
        derived = derive_id(record)
        assert derived.startswith(prefix)
        assert derive_id(record) == derived  # deterministic


def test_derived_ids_differ_for_identical_content_at_different_positions():
    first = {k: v for k, v in valid("comment.add", seq=0).items() if k != "id"}
    second = {k: v for k, v in valid("comment.add", seq=1).items() if k != "id"}
    assert derive_id(first) != derive_id(second)


def test_derive_id_refuses_an_unknown_type():
    with pytest.raises(SchemaError, match="unknown event type"):
        derive_id({"type": "comment.delete"})
    with pytest.raises(SchemaError, match="missing a 'type' field"):
        derive_id({"author": "alice"})
