"""Taking in comments that were made somewhere else (H9).

Review comments get left in other tools — a diff viewer with a line gutter, a
code host, an editor plugin. Wherever they land, they land outside this ledger,
and a comment nobody can list is a comment lost (G3). This module is the way
back in.

**The boundary is a documented file format, not a tool.** Nothing here knows
what produced its input; it reads ``specround.import/v0`` (see
``docs/import-format.md``) and turns each item into a ``comment.add`` on an open
round. Teaching this module about a particular viewer's storage would make every
future viewer a change to the core, so the per-tool converters live outside the
package in ``adapters/`` and their whole job is to emit this format.

Three rules do the work:

**Where it lands is verified, never guessed.** An item quotes the text it is
about. A quote alone goes through the same quote-to-anchor path a shell caller
uses; a quote plus offsets is checked against the round's base before it is
believed. An item whose quote is not in the base is refused *by itself*, with a
reason — the alternative is a comment silently attached to whatever those
offsets point at now, which is the quiet wrong answer the format exists to
prevent (§5.1).

**Where it came from is recorded.** The origin — the producing tool and that
tool's own id — goes in ``ext.import``. The format reserves ``ext`` for exactly
this (§2), and it is what makes the third rule possible.

**Importing twice imports once.** A second pass over the same file recognises
its own earlier work by that origin and skips it. Re-running an import after
fixing one bad item is therefore safe, which matters because refusals are
per-item: the good ones land on the first run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from specround.anchors import Anchor, count_occurrences
from specround.errors import AnchorError, SpecroundError
from specround.fold import Comment, State
from specround.store import ReviewStore
from specround.wire import comments_on

#: The name and major version of the file this module reads. Same compatibility
#: rule as the ledger's: a major this reader does not implement is refused
#: rather than guessed at, because a converter written against a later contract
#: means something by a field that is not here yet.
IMPORT_SCHEMA_NAME = "specround.import"
IMPORT_SCHEMA_VERSION = 0
IMPORT_SCHEMA = f"{IMPORT_SCHEMA_NAME}/v{IMPORT_SCHEMA_VERSION}"

#: The key under ``ext`` that records where an imported comment came from.
EXT_KEY = "import"

#: How an accepted item found its anchor — reported so a reader can tell an
#: item that named exact offsets from one resolved by quoting.
BY_QUOTE = "quote"
BY_SPAN = "span"
WHOLE = "document"

_BATCH_KEYS = frozenset({"schema", "source", "comments"})
_ITEM_KEYS = frozenset({"id", "body", "author", "ts", "quote", "occurrence", "span", "whole"})
_SPAN_KEYS = frozenset({"start", "end"})

__all__ = [
    "BY_QUOTE",
    "BY_SPAN",
    "Batch",
    "BatchError",
    "EXT_KEY",
    "IMPORT_SCHEMA",
    "IMPORT_SCHEMA_NAME",
    "IMPORT_SCHEMA_VERSION",
    "Item",
    "Plan",
    "Planned",
    "Rejected",
    "Skipped",
    "WHOLE",
    "apply_plan",
    "load_batch",
    "parse_batch",
    "plan_import",
]


class BatchError(SpecroundError):
    """The import file cannot be read as ``specround.import/v0``.

    Distinct from a per-item refusal on purpose. A malformed file is the
    caller's to fix and nothing in it is trustworthy; an item whose quote is not
    in the base is a well-formed statement about a document that moved on, and
    the rest of the file still imports.
    """


# -- the file ------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One comment as the file states it, before any document is consulted."""

    #: The producing tool's own identifier. Half of the idempotency key.
    id: str
    body: str
    author: str | None = None
    #: When the source recorded it. The ledger's own ``ts`` is append time, so
    #: this is carried in ``ext`` rather than dropped.
    ts: str | None = None
    quote: str | None = None
    occurrence: int | None = None
    span: tuple[int, int] | None = None
    #: True for a comment about the document as a whole, which has no anchor.
    whole: bool = False


@dataclass(frozen=True)
class Batch:
    """A parsed import file: where the comments came from, and what they are."""

    source: str
    items: tuple[Item, ...] = ()


def _require_mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BatchError(f"{what} must be a JSON object")
    return value


def _check_keys(payload: Mapping[str, Any], allowed: frozenset[str], what: str) -> None:
    """Refuse unknown keys, for the reason the ledger refuses them (§2).

    A field this reader ignores is a field the writer believes is in effect. In
    an import that costs more than usual: the writer is a converter someone else
    wrote, and the thing it silently failed to say is where a comment goes.
    """
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise BatchError(
            f"{what}: unknown field(s) {', '.join(unknown)} — "
            f"known: {', '.join(sorted(allowed))}"
        )


def _string(payload: Mapping[str, Any], key: str, what: str, *, required: bool) -> str | None:
    if key not in payload:
        if required:
            raise BatchError(f"{what}: missing required field {key!r}")
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise BatchError(f"{what}: {key!r} must be a string")
    value = value.strip()
    if not value:
        if required:
            raise BatchError(f"{what}: {key!r} must not be empty")
        return None
    return value


def _span(payload: Mapping[str, Any], what: str) -> tuple[int, int] | None:
    if "span" not in payload:
        return None
    raw = _require_mapping(payload["span"], f"{what}: 'span'")
    _check_keys(raw, _SPAN_KEYS, f"{what}: 'span'")
    bounds = []
    for key in ("start", "end"):
        if key not in raw:
            raise BatchError(f"{what}: 'span' is missing {key!r}")
        value = raw[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise BatchError(f"{what}: span.{key} must be an integer")
        if value < 0:
            raise BatchError(f"{what}: span.{key} must not be negative")
        bounds.append(value)
    start, end = bounds
    if end < start:
        raise BatchError(f"{what}: span.end ({end}) precedes span.start ({start})")
    return start, end


def _parse_item(payload: Any, index: int) -> Item:
    what = f"comments[{index}]"
    raw = _require_mapping(payload, what)
    _check_keys(raw, _ITEM_KEYS, what)

    identifier = _string(raw, "id", what, required=True)
    body = _string(raw, "body", what, required=True)
    assert identifier is not None and body is not None  # required

    whole = raw.get("whole", False)
    if not isinstance(whole, bool):
        raise BatchError(f"{what}: 'whole' must be true or false")

    quote = _string(raw, "quote", what, required=False)
    span = _span(raw, what)
    occurrence = raw.get("occurrence")
    if occurrence is not None:
        if not isinstance(occurrence, int) or isinstance(occurrence, bool):
            raise BatchError(f"{what}: 'occurrence' must be an integer")
        if occurrence < 0:
            raise BatchError(f"{what}: 'occurrence' must not be negative")

    if whole:
        # A whole-document comment says so in one field. Anything else in the
        # item would be a second, contradicting statement about where it goes.
        for name, value in (("quote", quote), ("span", span), ("occurrence", occurrence)):
            if value is not None:
                raise BatchError(
                    f"{what}: 'whole' is a comment on the document, so it cannot also carry {name!r}"
                )
        return Item(id=identifier, body=body, author=_string(raw, "author", what, required=False),
                    ts=_string(raw, "ts", what, required=False), whole=True)

    if quote is None:
        # Not defaulted to a whole-document comment: an item that meant to name
        # a span and lost its quote would import as a comment on the document,
        # exit 0, and look like it worked.
        raise BatchError(
            f"{what}: no 'quote' — an anchored item quotes the text it is about, "
            "and a comment on the document as a whole says 'whole': true"
        )
    if span is not None and occurrence is not None:
        # Two ways to pick between repeats of the same quote, and nothing makes
        # them agree. Offsets are already unambiguous, so this is the one to drop.
        raise BatchError(
            f"{what}: 'span' and 'occurrence' both choose which appearance of the "
            "quote is meant — offsets already say, so drop 'occurrence'"
        )
    return Item(
        id=identifier,
        body=body,
        author=_string(raw, "author", what, required=False),
        ts=_string(raw, "ts", what, required=False),
        quote=quote,
        occurrence=occurrence,
        span=span,
    )


def parse_batch(payload: Any) -> Batch:
    """Read a decoded import file, refusing anything this contract does not define."""
    raw = _require_mapping(payload, "an import file")
    _check_keys(raw, _BATCH_KEYS, "import file")

    schema = raw.get("schema")
    if not isinstance(schema, str) or not schema:
        raise BatchError("import file: missing a 'schema' field")
    name, separator, version = schema.rpartition("/v")
    if not separator or not version.isdigit():
        raise BatchError(f"import file: malformed schema {schema!r}: expected '<name>/v<major>'")
    if name != IMPORT_SCHEMA_NAME:
        raise BatchError(
            f"import file: foreign schema {name!r}; this reader knows {IMPORT_SCHEMA_NAME!r}"
        )
    if int(version) != IMPORT_SCHEMA_VERSION:
        raise BatchError(
            f"import file: schema {schema!r} is major version {int(version)}; "
            f"this reader implements v{IMPORT_SCHEMA_VERSION} and will not guess"
        )

    source = _string(raw, "source", "import file", required=True)
    assert source is not None  # required

    comments = raw.get("comments")
    if not isinstance(comments, list):
        raise BatchError("import file: 'comments' must be a list")
    items = tuple(_parse_item(entry, index) for index, entry in enumerate(comments))

    seen: dict[str, int] = {}
    for index, item in enumerate(items):
        first = seen.get(item.id)
        if first is not None:
            # Not deduplicated quietly: the id is what makes a re-import a no-op,
            # so a file that uses one id twice has already broken that promise and
            # only its author knows which of the two was meant.
            raise BatchError(
                f"comments[{index}]: id {item.id!r} is also comments[{first}]'s — "
                "source ids identify a comment, and a repeat makes re-import ambiguous"
            )
        seen[item.id] = index
    return Batch(source=source, items=items)


def load_batch(path: Path) -> Batch:
    """Read and parse an import file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BatchError(f"cannot read {path}: {exc}") from exc
    return parse_text(text)


def parse_text(text: str) -> Batch:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BatchError(f"not JSON: {exc}") from exc
    return parse_batch(payload)


# -- the plan ------------------------------------------------------------


@dataclass(frozen=True)
class Planned:
    """An item that has somewhere to land, and how it found it."""

    item: Item
    anchor: Anchor | None
    how: str


@dataclass(frozen=True)
class Skipped:
    """An item this store already holds, from an earlier run."""

    item: Item
    comment: str


@dataclass(frozen=True)
class Rejected:
    """An item that named text the round's base does not have."""

    item: Item
    reason: str


@dataclass(frozen=True)
class Plan:
    """What one import file would do to one round — computed before anything is written."""

    source: str
    round: str
    planned: list[Planned] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.planned) + len(self.skipped) + len(self.rejected)


def imported_origin(comment: Comment) -> tuple[str, str] | None:
    """The ``(source, id)`` an imported comment came from, or ``None``.

    Read defensively because ``ext`` is by contract an object this reader does
    not police (§2) — another writer may put anything under a key of that name,
    and a shape that is not this one simply is not one of ours.
    """
    ext = comment.ext or {}
    origin = ext.get(EXT_KEY)
    if not isinstance(origin, Mapping):
        return None
    source, identifier = origin.get("source"), origin.get("id")
    if not isinstance(source, str) or not isinstance(identifier, str):
        return None
    if not source or not identifier:
        return None
    return source, identifier


def already_imported(state: State, key: str, source: str) -> dict[str, str]:
    """Source ids already on this document from ``source``, to their comment ids."""
    found: dict[str, str] = {}
    for comment in comments_on(state, key):
        origin = imported_origin(comment)
        if origin is not None and origin[0] == source:
            found.setdefault(origin[1], comment.id)
    return found


def _resolve(store: ReviewStore, round_id: str, base: str, item: Item) -> Planned:
    """Where this item goes in the round's base, or ``AnchorError`` saying why not."""
    if item.whole:
        return Planned(item=item, anchor=None, how=WHOLE)

    assert item.quote is not None  # the parser refuses an anchored item without one

    if item.span is not None:
        start, end = item.span
        if end > len(base):
            raise AnchorError(
                f"span [{start}:{end}] runs past the end of the base "
                f"({len(base)} characters) — the source counted in a different text"
            )
        found = base[start:end]
        if found != item.quote:
            # The offsets and the quote are two statements about one place, and
            # they disagree. Believing the offsets would attach the comment to
            # text nobody wrote it about; believing the quote would discard the
            # only thing that told them apart. Neither, with the reason.
            raise AnchorError(
                f"the base has {found!r} at [{start}:{end}], not the quoted "
                f"{item.quote!r} — drop 'span' to place it by quote, or re-open the "
                "round on the text the source read"
            )
        return Planned(item=item, anchor=store.anchor_span_in_round(round_id, start, end), how=BY_SPAN)

    total = count_occurrences(base, item.quote)
    if total == 0:
        raise AnchorError(
            f"the quote {item.quote!r} is not in the base this round froze "
            "(the document may have been revised since the source read it)"
        )
    if total > 1 and item.occurrence is None:
        raise AnchorError(
            f"the quote {item.quote!r} appears {total} times in the base: the item has "
            f"to say which one with 'occurrence' (0..{total - 1}) or with 'span'"
        )
    if item.occurrence is not None and item.occurrence >= total:
        raise AnchorError(
            f"the quote {item.quote!r} appears {total} time(s); "
            f"occurrence {item.occurrence} does not exist"
        )
    anchor = store.anchor_in_round(round_id, item.quote, occurrence=item.occurrence or 0)
    return Planned(item=item, anchor=anchor, how=BY_QUOTE)


def plan_import(store: ReviewStore, round_id: str, key: str, batch: Batch) -> Plan:
    """Work out what ``batch`` would do, touching nothing.

    Every item ends in exactly one of three lists, so a caller reading the plan
    is reading the whole file. A refusal is per-item: the document moved on
    under one comment, which says nothing about the others.
    """
    base = store.base_text(round_id)
    seen = already_imported(store.fold(), key, batch.source)
    plan = Plan(source=batch.source, round=round_id)
    for item in batch.items:
        existing = seen.get(item.id)
        if existing is not None:
            plan.skipped.append(Skipped(item=item, comment=existing))
            continue
        try:
            plan.planned.append(_resolve(store, round_id, base, item))
        except AnchorError as exc:
            plan.rejected.append(Rejected(item=item, reason=str(exc)))
    return plan


def apply_plan(plan: Plan, store: ReviewStore, *, author: str) -> list[tuple[Item, str]]:
    """Record everything the plan resolved, and hand back the ids it wrote.

    Only the resolved items. Applying a plan with refusals in it is the normal
    case — the good comments land now, and the same file can be run again once
    the refused ones are fixed, because what landed is skipped the second time.
    """
    written: list[tuple[Item, str]] = []
    for entry in plan.planned:
        item = entry.item
        origin: dict[str, Any] = {"source": plan.source, "id": item.id}
        if item.ts:
            origin["ts"] = item.ts
        comment_id = store.add_comment(
            plan.round,
            author=item.author or author,
            body=item.body,
            anchor=entry.anchor,
            ext={EXT_KEY: origin},
        )
        written.append((item, comment_id))
    return written
