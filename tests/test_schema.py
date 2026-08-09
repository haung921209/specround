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
    """A minimal valid record of each kind.

    The id is derived rather than written in: it has to carry this kind's
    prefix, and a fixture that hard-codes one prefix for every kind is exactly
    the record the format refuses.
    """
    base = {
        "schema": SCHEMA,
        "seq": 0,
        "ts": "2020-01-01T00:00:01Z",
        "type": kind,
        "author": "alice",
    }
    payloads = {
        "round.open": {"doc": "spec.md", "base": "sha256:" + "0" * 64},
        "comment.add": {"round": "r-1", "body": "why 30 seconds?"},
        "suggestion.add": {"round": "r-1", "patch": "-30\n+60\n"},
        "reply": {"target": "c-1", "body": "because of the proxy"},
        "disposition": {"target": "c-1", "verdict": "applied", "reason": "agreed"},
        "round.close": {"round": "r-1"},
        "anchor.reanchor": {
            "target": "c-1",
            "base": "sha256:" + "1" * 64,
            "anchor": {"exact": "30 seconds", "start": 10, "end": 20},
            "strategy": "quote",
        },
        "anchor.orphan": {
            "target": "c-1",
            "base": "sha256:" + "1" * 64,
            "reason": "the quoted text is not in this revision",
        },
        "thread.resolve": {"target": "c-1", "actor": "human"},
        "thread.reopen": {
            "target": "c-1",
            "actor": "agent",
            "reason": "the timeout came back in revision 3",
        },
    }
    record = {**base, **payloads[kind], **overrides}
    record.setdefault("id", derive_id(record))
    return record


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


def test_supersede_is_an_optional_boolean_on_a_disposition():
    validate_event(valid("disposition"))
    for flag in (True, False):
        validate_event(valid("disposition", supersede=flag))
    # A string is refused rather than read for truthiness. This field decides
    # whether I5 lets the record through, and "false" is truthy in most of the
    # languages the format invites to write these lines with.
    with pytest.raises(SchemaError, match="must be true or false"):
        validate_event(valid("disposition", supersede="true"))


def test_supersede_belongs_to_dispositions_and_nothing_else():
    """The field set is closed per kind, so the flag on another record is refused.

    It reads like a general "I mean this" marker, which is the reason the reader
    has to say no: on a ``thread.resolve`` it would look honoured and do nothing.
    """
    with pytest.raises(SchemaError, match="unknown field"):
        validate_event(valid("thread.resolve", supersede=True))


def test_actor_vocabulary_is_closed():
    from specround.events import ACTORS

    for actor in ACTORS:
        validate_event(valid("thread.resolve", actor=actor))
        validate_event(valid("thread.reopen", actor=actor))
    with pytest.raises(SchemaError, match="unknown actor"):
        validate_event(valid("thread.resolve", actor="agent:reviewer"))


def test_who_closed_a_thread_is_two_facts_not_one():
    """``actor`` says which kind, ``author`` says which one.

    A convention in the author string is not checkable — an agent named
    ``agent:reviewer`` and a person who typed that as their name are the same
    bytes. The kind is a closed field so a reader can act on it.
    """
    record = valid("thread.resolve", author="agent:reviewer", actor="agent")
    validate_event(record)
    assert record["author"] != record["actor"]
    with pytest.raises(SchemaError, match="missing required field 'actor'"):
        validate_event({k: v for k, v in record.items() if k != "actor"})


def test_reopening_says_why_and_resolving_need_not():
    validate_event(valid("thread.resolve"))  # no note at all
    validate_event(valid("thread.resolve", note="agreed in the reply"))
    validate_event(valid("thread.resolve", note=""))
    with pytest.raises(SchemaError, match="'reason' must not be empty"):
        validate_event(valid("thread.reopen", reason=""))
    with pytest.raises(SchemaError, match="unknown field"):
        validate_event(valid("thread.resolve", reason="not this field"))


def test_strategy_vocabulary_is_closed():
    from specround.reanchor import STRATEGIES

    for strategy in STRATEGIES:
        validate_event(valid("anchor.reanchor", strategy=strategy))
    with pytest.raises(SchemaError, match="unknown strategy"):
        validate_event(valid("anchor.reanchor", strategy="vibes"))


def test_ambiguous_is_a_flag_not_a_string():
    validate_event(valid("anchor.reanchor", ambiguous=True))
    validate_event(valid("anchor.reanchor", ambiguous=False))
    with pytest.raises(SchemaError, match="'ambiguous' must be true or false"):
        validate_event(valid("anchor.reanchor", ambiguous="yes"))


def test_a_reanchor_carries_an_anchor_and_an_orphan_does_not():
    with pytest.raises(SchemaError, match="missing required field 'anchor'"):
        validate_event({k: v for k, v in valid("anchor.reanchor").items() if k != "anchor"})
    with pytest.raises(SchemaError, match="unknown field"):
        validate_event(valid("anchor.orphan", anchor={"exact": "x", "start": 0, "end": 1}))
    with pytest.raises(SchemaError, match="'reason' must not be empty"):
        validate_event(valid("anchor.orphan", reason=""))


def test_the_ledger_records_no_floating_point_score():
    """Scores stay out of the wire format on purpose.

    Canonical lines have to be the same bytes everywhere for ids to derive and
    files to compare; float formatting is the classic way that stops being true
    across languages. The rung that matched is recorded instead, and anything
    finer belongs in ``ext``.
    """
    from specround.events import _PAYLOAD

    for required, optional in _PAYLOAD.values():
        for name in {**required, **optional}:
            assert "score" not in name and "ratio" not in name


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
        ("anchor.reanchor", "a-"),
        ("anchor.orphan", "o-"),
        ("thread.resolve", "v-"),
        ("thread.reopen", "n-"),
    ]:
        record = {k: v for k, v in valid(kind).items() if k != "id"}
        derived = derive_id(record)
        assert derived.startswith(prefix)
        assert derive_id(record) == derived  # deterministic


def test_each_event_type_has_its_own_id_prefix():
    """A bare id in a log line has to say what kind of record it came from."""
    from specround.events import _ID_PREFIX

    assert sorted(_ID_PREFIX) == sorted(EVENT_TYPES)
    assert len(set(_ID_PREFIX.values())) == len(EVENT_TYPES)


def test_derived_ids_differ_for_identical_content_at_different_positions():
    first = {k: v for k, v in valid("comment.add", seq=0).items() if k != "id"}
    second = {k: v for k, v in valid("comment.add", seq=1).items() if k != "id"}
    assert derive_id(first) != derive_id(second)


def test_derive_id_refuses_an_unknown_type():
    with pytest.raises(SchemaError, match="unknown event type"):
        derive_id({"type": "comment.delete"})
    with pytest.raises(SchemaError, match="missing a 'type' field"):
        derive_id({"author": "alice"})


# -- fields the format defines precisely ---------------------------------


@pytest.mark.parametrize("kind", ["round.open", "anchor.reanchor", "anchor.orphan"])
def test_a_base_that_is_not_a_snapshot_reference_is_rejected(kind):
    """``base`` is ``sha256:<64 hex>``, not any non-empty string.

    A round whose base cannot be resolved is a round nothing can be anchored
    against, and the ledger used to keep accepting history on top of it: the
    unanchored comments still landed, and only the anchored ones failed.
    """
    with pytest.raises(SchemaError, match="base"):
        validate_event(valid(kind, base="not-a-digest-at-all"))


def test_a_base_with_the_wrong_digest_length_is_rejected():
    with pytest.raises(SchemaError, match="base"):
        validate_event(valid("round.open", base="sha256:" + "0" * 63))


def test_an_id_prefix_that_lies_about_the_kind_is_rejected():
    """§3 defines the id as a kind prefix plus twelve digest characters.

    An unchecked prefix makes a bare id in a log line unreadable — and it
    inverts the fold's own diagnosis, which tells a reader that a target 'is a
    round, not a comment' by trusting exactly this.
    """
    with pytest.raises(SchemaError, match="id"):
        validate_event(valid("comment.add", id="r-deadbeef0000"))


def test_an_id_that_is_not_a_prefix_and_a_digest_is_rejected():
    with pytest.raises(SchemaError, match="id"):
        validate_event(valid("comment.add", id="c-not-a-digest"))
    with pytest.raises(SchemaError, match="id"):
        validate_event(valid("comment.add", id="c-DEADBEEF0000"))


def test_a_derived_id_passes_the_id_check_for_every_kind():
    for kind in EVENT_TYPES:
        record = valid(kind)
        record.pop("id")
        record["id"] = derive_id(record)
        validate_event(record)
