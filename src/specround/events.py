"""The wire format — the contract itself (G5).

Every line of the ledger is one JSON object validated by this module. The
format is deliberately the narrow waist of the tool: any language, any editor,
any agent that can write these lines is a participant, and ``cat`` is a valid
reader.

Compatibility rule: the ``schema`` field carries a name and a major version.
A reader refuses a major it does not know rather than guessing at unfamiliar
records. Within a major version the field set is closed — unknown keys are an
error, not a silent pass — and additive experiments go under the reserved
``ext`` object.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from specround.anchors import Anchor
from specround.errors import AnchorError, SchemaError
from specround.reanchor import STRATEGIES

SCHEMA_NAME = "specround.ledger"
SCHEMA_VERSION = 0
#: The value every record carries in its ``schema`` field.
SCHEMA = f"{SCHEMA_NAME}/v{SCHEMA_VERSION}"

ROUND_OPEN = "round.open"
COMMENT_ADD = "comment.add"
SUGGESTION_ADD = "suggestion.add"
REPLY = "reply"
DISPOSITION = "disposition"
ROUND_CLOSE = "round.close"
ANCHOR_REANCHOR = "anchor.reanchor"
ANCHOR_ORPHAN = "anchor.orphan"
THREAD_RESOLVE = "thread.resolve"
THREAD_REOPEN = "thread.reopen"

EVENT_TYPES = (
    ROUND_OPEN,
    COMMENT_ADD,
    SUGGESTION_ADD,
    REPLY,
    DISPOSITION,
    ROUND_CLOSE,
    ANCHOR_REANCHOR,
    ANCHOR_ORPHAN,
    THREAD_RESOLVE,
    THREAD_REOPEN,
)

#: Events that rebind (or fail to rebind) a comment's anchor to a new snapshot.
#: They never rewrite the record that carried the original anchor — where a
#: comment lives now is the latest of these, and the whole path is still in the
#: log (G1 across revisions, G3 loss 0).
ANCHOR_KINDS = (ANCHOR_REANCHOR, ANCHOR_ORPHAN)

#: Events that end or re-open a thread — a comment and the replies under it (G11).
#: A thread has no object of its own: replies are flat, so the root comment's id
#: *is* the thread's id and these events target it.
THREAD_KINDS = (THREAD_RESOLVE, THREAD_REOPEN)

#: Who closed the conversation, as a closed vocabulary (G4, G11).
#: ``author`` says *which* participant; this says which *kind*. Both are required
#: on thread events because "an agent decided this discussion was over" and "a
#: person did" are different facts to a reader, and a convention in the author
#: string (``agent:reviewer``) is not something a reader can check.
HUMAN = "human"
AGENT = "agent"
ACTORS = (HUMAN, AGENT)

#: Disposition vocabulary (G3). Korean reading: 반영 / 기각 / 답변 / 보류.
APPLIED = "applied"
REJECTED = "rejected"
ANSWERED = "answered"
DEFERRED = "deferred"
VERDICTS = (APPLIED, REJECTED, ANSWERED, DEFERRED)
#: A terminal verdict settles a comment for good; a later disposition is refused.
TERMINAL_VERDICTS = (APPLIED, REJECTED, ANSWERED)

#: Event kinds that create a comment-like object a reply or disposition can target.
COMMENT_KINDS = (COMMENT_ADD, SUGGESTION_ADD)

#: Reserved for additive experiments without a schema bump.
EXT_FIELD = "ext"

_TEXT = "text"  # string, may be empty
_STRING = "string"  # non-empty string
_INDEX = "index"  # non-negative integer
_OBJECT = "object"
_STRING_LIST = "string-list"
_FLAG = "flag"  # boolean

_ENVELOPE: dict[str, str] = {
    "schema": _STRING,
    "seq": _INDEX,
    "ts": _STRING,
    "type": _STRING,
    "id": _STRING,
    "author": _STRING,
}

_PAYLOAD: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    # type: (required, optional)
    ROUND_OPEN: ({"doc": _STRING, "base": _STRING}, {"title": _TEXT}),
    COMMENT_ADD: ({"round": _STRING, "body": _STRING}, {"anchor": _OBJECT}),
    SUGGESTION_ADD: (
        {"round": _STRING, "patch": _STRING},
        {"anchor": _OBJECT, "body": _TEXT},
    ),
    REPLY: ({"target": _STRING, "body": _STRING}, {}),
    DISPOSITION: ({"target": _STRING, "verdict": _STRING, "reason": _STRING}, {}),
    ROUND_CLOSE: ({"round": _STRING}, {"unresolved": _STRING_LIST, "note": _TEXT}),
    ANCHOR_REANCHOR: (
        {"target": _STRING, "base": _STRING, "anchor": _OBJECT, "strategy": _STRING},
        {"ambiguous": _FLAG},
    ),
    ANCHOR_ORPHAN: ({"target": _STRING, "base": _STRING, "reason": _STRING}, {}),
    # Resolving carries an optional note: the thread itself is the record of why
    # it ended. Re-opening carries a required reason, because it overturns a
    # decision someone already recorded — the same rule that makes ``reason``
    # mandatory on a disposition and on an orphan.
    THREAD_RESOLVE: ({"target": _STRING, "actor": _STRING}, {"note": _TEXT}),
    THREAD_REOPEN: ({"target": _STRING, "actor": _STRING, "reason": _STRING}, {}),
}

#: Event ids are prefixed by kind so a bare id in a log line is readable.
_ID_PREFIX: dict[str, str] = {
    ROUND_OPEN: "r",
    COMMENT_ADD: "c",
    SUGGESTION_ADD: "s",
    REPLY: "p",
    DISPOSITION: "d",
    ROUND_CLOSE: "x",
    ANCHOR_REANCHOR: "a",
    ANCHOR_ORPHAN: "o",
    THREAD_RESOLVE: "v",  # resol-v-e
    THREAD_REOPEN: "n",  # reope-n
}
_ID_HASH_CHARS = 12


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialise one record the way the ledger stores it.

    Keys are sorted and separators are tight, so the same record is always the
    same bytes — that is what makes ids derivable and files comparable.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def parse_schema(value: Any) -> tuple[str, int]:
    """Split a ``schema`` field into (name, major version)."""
    if not isinstance(value, str) or not value:
        raise SchemaError("record is missing a 'schema' field")
    name, separator, version = value.rpartition("/v")
    if not separator or not version.isdigit():
        raise SchemaError(f"malformed schema {value!r}: expected '<name>/v<major>'")
    return name, int(version)


def check_schema_compatible(value: Any) -> None:
    """Raise unless this reader understands ``value``."""
    name, major = parse_schema(value)
    if name != SCHEMA_NAME:
        raise SchemaError(f"foreign ledger schema {name!r}: this reader knows {SCHEMA_NAME!r}")
    if major != SCHEMA_VERSION:
        raise SchemaError(
            f"ledger schema {value!r} is major version {major}; "
            f"this reader implements v{SCHEMA_VERSION} and will not guess"
        )


def check_event_type(value: Any) -> str:
    """Return ``value`` if it names an event type this version knows."""
    if not isinstance(value, str) or not value:
        raise SchemaError("record is missing a 'type' field")
    if value not in EVENT_TYPES:
        raise SchemaError(
            f"unknown event type {value!r}; known types: {', '.join(EVENT_TYPES)}"
        )
    return value


def _check_field(where: str, name: str, kind: str, value: Any) -> None:
    if kind in (_STRING, _TEXT):
        if not isinstance(value, str):
            raise SchemaError(f"{where}: {name!r} must be a string")
        if kind == _STRING and not value:
            raise SchemaError(f"{where}: {name!r} must not be empty")
    elif kind == _INDEX:
        if not isinstance(value, int) or isinstance(value, bool):
            raise SchemaError(f"{where}: {name!r} must be an integer")
        if value < 0:
            raise SchemaError(f"{where}: {name!r} must not be negative")
    elif kind == _OBJECT:
        if not isinstance(value, Mapping):
            raise SchemaError(f"{where}: {name!r} must be an object")
    elif kind == _STRING_LIST:
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise SchemaError(f"{where}: {name!r} must be a list of non-empty strings")
    elif kind == _FLAG:
        if not isinstance(value, bool):
            raise SchemaError(f"{where}: {name!r} must be true or false")
    else:  # pragma: no cover - guards a typo in the table above
        raise SchemaError(f"{where}: unknown field kind {kind!r} for {name!r}")


def validate_event(record: Any) -> None:
    """Raise ``SchemaError`` unless ``record`` is a valid ledger record."""
    if not isinstance(record, Mapping):
        raise SchemaError("a ledger record must be a JSON object")

    check_schema_compatible(record.get("schema"))

    kind = check_event_type(record.get("type"))

    where = f"{kind} record"
    required, optional = _PAYLOAD[kind]
    allowed = set(_ENVELOPE) | set(required) | set(optional) | {EXT_FIELD}
    unknown = sorted(set(record) - allowed)
    if unknown:
        raise SchemaError(
            f"{where}: unknown field(s) {', '.join(unknown)} "
            f"(additive data goes under {EXT_FIELD!r})"
        )

    for name, field_kind in {**_ENVELOPE, **required}.items():
        if name not in record:
            raise SchemaError(f"{where}: missing required field {name!r}")
        _check_field(where, name, field_kind, record[name])
    for name, field_kind in optional.items():
        if name in record:
            _check_field(where, name, field_kind, record[name])
    if EXT_FIELD in record:
        _check_field(where, EXT_FIELD, _OBJECT, record[EXT_FIELD])

    if kind == DISPOSITION and record["verdict"] not in VERDICTS:
        raise SchemaError(
            f"{where}: unknown verdict {record['verdict']!r}; "
            f"known verdicts: {', '.join(VERDICTS)}"
        )
    if kind in THREAD_KINDS and record["actor"] not in ACTORS:
        # Closed like the verdicts: "who ended this conversation" is only a fact
        # a reader can act on while the answer comes from a fixed set.
        raise SchemaError(
            f"{where}: unknown actor {record['actor']!r}; "
            f"known actors: {', '.join(ACTORS)}"
        )
    if kind == ANCHOR_REANCHOR and record["strategy"] not in STRATEGIES:
        # Closed like the verdicts: a reader has to be able to tell a comment
        # that merely moved from one whose text was rewritten under it.
        raise SchemaError(
            f"{where}: unknown strategy {record['strategy']!r}; "
            f"known strategies: {', '.join(STRATEGIES)}"
        )
    if "anchor" in record:
        try:
            Anchor.from_json(record["anchor"])
        except AnchorError as exc:
            raise SchemaError(f"{where}: {exc}") from exc


def derive_id(record: Mapping[str, Any]) -> str:
    """Derive a stable id for a record that does not carry one.

    The digest covers every other field, ``seq`` included, so ids are unique
    even for two byte-identical comments and reproducible for a given append
    order — replaying a ledger yields the same ids.
    """
    prefix = _ID_PREFIX[check_event_type(record.get("type"))]
    payload = {k: v for k, v in record.items() if k != "id"}
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:_ID_HASH_CHARS]}"
