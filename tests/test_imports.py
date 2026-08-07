"""Absorbing comments made in another tool (H9).

Four things are under test, and the reason they are separate is that each one
is a different way the boundary could quietly stop holding.

**The file format is closed.** A converter is code somebody else wrote, and the
one thing it can get wrong that nobody notices is a field this reader ignores —
so unknown keys, wrong schemas and self-contradicting items are refusals, not
defaults.

**Nothing is guessed onto the document.** An item whose quote is not in the
round's base, or whose offsets disagree with its own quote, is refused *by
itself* and with a reason. The failure this prevents is the silent one: a
comment attached to text nobody wrote it about.

**Importing twice imports once.** The origin recorded under ``ext`` is what
makes a re-run a no-op, so it is asserted as a fact about the ledger and not
just as a count.

**A refusal does not take the file down with it.** Per-item, so the good
comments land on the first run and the fixed ones land on the second.
"""

import json

import pytest

from specround.errors import InvariantError
from specround.imports import (
    BY_QUOTE,
    BY_SPAN,
    EXT_KEY,
    IMPORT_SCHEMA,
    WHOLE,
    Batch,
    BatchError,
    Item,
    apply_plan,
    load_batch,
    parse_batch,
    parse_text,
    plan_import,
)


def batch(*comments, source="cmux", schema=IMPORT_SCHEMA):
    return {"schema": schema, "source": source, "comments": list(comments)}


def item(identifier="ext-1", body="too short for the proxy", **extra):
    return {"id": identifier, "body": body, **extra}


@pytest.fixture
def imported(store, round_id, doc):
    """Plan a batch against the open round, then optionally record it."""

    def go(payload, *, apply=False, author="agent:importer"):
        parsed = parse_batch(payload)
        plan = plan_import(store, round_id, store.doc_key(doc), parsed)
        if apply:
            apply_plan(plan, store, author=author)
        return plan

    return go


# -- the file format -----------------------------------------------------


def test_a_well_formed_file_parses_into_items():
    parsed = parse_batch(
        batch(
            item(quote="30 seconds", author="bob", ts="2026-08-06T06:07:16Z"),
            item("ext-2", "and another", whole=True),
        )
    )
    assert parsed == Batch(
        source="cmux",
        items=(
            Item(
                id="ext-1",
                body="too short for the proxy",
                author="bob",
                ts="2026-08-06T06:07:16Z",
                quote="30 seconds",
            ),
            Item(id="ext-2", body="and another", whole=True),
        ),
    )


def test_an_unknown_top_level_field_is_refused():
    payload = batch(item(quote="30 seconds"))
    payload["repoRoot"] = "/somewhere"
    with pytest.raises(BatchError, match="unknown field"):
        parse_batch(payload)


def test_an_unknown_item_field_is_refused():
    with pytest.raises(BatchError, match=r"comments\[0\].*unknown field"):
        parse_batch(batch(item(quote="30 seconds", side="additions")))


def test_a_foreign_schema_is_refused():
    with pytest.raises(BatchError, match="foreign schema"):
        parse_batch(batch(item(quote="x"), schema="rdjson/v0"))


def test_the_version_is_checked_before_the_field_names():
    # A file from a contract this reader does not implement is expected to have
    # fields it does not know. Naming one of those would send the caller to fix
    # the wrong thing.
    payload = batch(item(quote="x"), schema="specround.import/v9")
    payload["something_v9_added"] = True
    with pytest.raises(BatchError, match="will not guess"):
        parse_batch(payload)


def test_a_later_major_is_refused_rather_than_guessed_at():
    with pytest.raises(BatchError, match="will not guess"):
        parse_batch(batch(item(quote="x"), schema="specround.import/v1"))


@pytest.mark.parametrize("missing", ["schema", "source", "comments"])
def test_the_three_required_top_level_fields(missing):
    payload = batch(item(quote="30 seconds"))
    del payload[missing]
    with pytest.raises(BatchError):
        parse_batch(payload)


@pytest.mark.parametrize("field", ["id", "body"])
def test_an_item_needs_an_id_and_a_body(field):
    entry = item(quote="30 seconds")
    del entry[field]
    with pytest.raises(BatchError, match=f"missing required field {field!r}"):
        parse_batch(batch(entry))


def test_an_anchored_item_without_a_quote_is_refused_not_defaulted():
    # The failure mode this closes: an item that meant to name a span and lost
    # its quote would otherwise import as a comment on the whole document,
    # exit 0, and look exactly like it worked.
    with pytest.raises(BatchError, match="no 'quote'"):
        parse_batch(batch(item()))


def test_whole_and_quote_together_are_two_answers_to_one_question():
    with pytest.raises(BatchError, match="cannot also carry 'quote'"):
        parse_batch(batch(item(quote="30 seconds", whole=True)))


def test_span_and_occurrence_together_are_refused():
    with pytest.raises(BatchError, match="drop 'occurrence'"):
        parse_batch(
            batch(item(quote="30 seconds", occurrence=0, span={"start": 1, "end": 11}))
        )


def test_a_span_needs_both_ends():
    with pytest.raises(BatchError, match="missing 'end'"):
        parse_batch(batch(item(quote="30 seconds", span={"start": 1})))


def test_a_backwards_span_is_refused():
    with pytest.raises(BatchError, match="precedes"):
        parse_batch(batch(item(quote="x", span={"start": 9, "end": 2})))


def test_a_repeated_source_id_in_one_file_is_refused():
    # Two items claiming one id have already broken the promise that makes a
    # re-import a no-op, and only their author knows which was meant.
    with pytest.raises(BatchError, match="makes re-import ambiguous"):
        parse_batch(batch(item("dup", quote="30 seconds"), item("dup", "other", whole=True)))


def test_the_quote_is_taken_exactly_as_written(doc, store):
    # Whitespace in a quote is data: it has to match the base character for
    # character. Trimming would anchor an indented line to its un-indented
    # middle, which is a different span than the item named.
    doc.write_text("intro\n    indented line\n", encoding="utf-8")
    indented = store.open_round(doc, author="alice")
    parsed = parse_batch(batch(item(quote="    indented line")))
    assert parsed.items[0].quote == "    indented line"

    plan = plan_import(store, indented, store.doc_key(doc), parsed)
    assert plan.planned[0].anchor.exact == "    indented line"


def test_a_quote_that_is_present_but_empty_is_refused():
    with pytest.raises(BatchError, match="'quote' must not be empty"):
        parse_batch(batch(item(quote="")))


def test_an_empty_comment_list_is_a_valid_file():
    assert parse_batch(batch()).items == ()


def test_text_that_is_not_json_says_so():
    with pytest.raises(BatchError, match="not JSON"):
        parse_text("{oops")


def test_load_batch_reads_a_file(tmp_path):
    path = tmp_path / "in.json"
    path.write_text(json.dumps(batch(item(quote="30 seconds"))), encoding="utf-8")
    assert load_batch(path).source == "cmux"


def test_load_batch_names_the_file_it_could_not_read(tmp_path):
    with pytest.raises(BatchError, match="cannot read"):
        load_batch(tmp_path / "nope.json")


# -- anchoring -----------------------------------------------------------


def test_a_quote_lands_through_the_ordinary_quote_path(imported, doc_text):
    plan = imported(batch(item(quote="30 seconds")))
    assert [entry.how for entry in plan.planned] == [BY_QUOTE]
    anchor = plan.planned[0].anchor
    assert anchor is not None
    assert doc_text[anchor.start : anchor.end] == "30 seconds"


def test_offsets_are_verified_against_the_base_before_they_are_believed(imported, doc_text):
    start = doc_text.index("30 seconds")
    plan = imported(
        batch(item(quote="30 seconds", span={"start": start, "end": start + len("30 seconds")}))
    )
    assert [entry.how for entry in plan.planned] == [BY_SPAN]
    assert plan.planned[0].anchor.start == start


def test_offsets_that_disagree_with_their_quote_refuse_that_item(imported, doc_text):
    start = doc_text.index("30 seconds")
    plan = imported(batch(item(quote="30 seconds", span={"start": start + 3, "end": start + 13})))
    assert plan.planned == []
    assert len(plan.rejected) == 1
    assert "not the quoted" in plan.rejected[0].reason


def test_offsets_past_the_end_of_the_base_refuse_that_item(imported):
    plan = imported(batch(item(quote="30 seconds", span={"start": 9_000, "end": 9_010})))
    assert "runs past the end" in plan.rejected[0].reason


def test_a_quote_the_base_does_not_have_is_refused_with_a_reason(imported):
    plan = imported(batch(item(quote="a phrase from some other document")))
    assert plan.planned == []
    assert "is not in the base" in plan.rejected[0].reason


def test_a_repeated_quote_asks_which_one_rather_than_picking(doc, store):
    doc.write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
    repeats = store.open_round(doc, author="alice")
    plan = plan_import(
        store, repeats, store.doc_key(doc), parse_batch(batch(item(quote="alpha")))
    )
    assert plan.planned == []
    assert "appears 2 times" in plan.rejected[0].reason


def test_occurrence_picks_between_repeats(doc, store):
    doc.write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
    repeats = store.open_round(doc, author="alice")
    plan = plan_import(
        store,
        repeats,
        store.doc_key(doc),
        parse_batch(batch(item(quote="alpha", occurrence=1))),
    )
    assert plan.planned[0].anchor.start == len("alpha\nbeta\n")


def test_an_occurrence_that_does_not_exist_is_refused(imported):
    plan = imported(batch(item(quote="30 seconds", occurrence=4)))
    assert "occurrence 4 does not exist" in plan.rejected[0].reason


def test_a_whole_document_item_lands_without_an_anchor(imported):
    plan = imported(batch(item(whole=True)))
    assert plan.planned[0].anchor is None
    assert plan.planned[0].how == WHOLE


# -- recording -----------------------------------------------------------


def test_applying_records_the_comment_with_its_origin(imported, store, doc):
    plan = imported(
        batch(item(quote="30 seconds", author="bob", ts="2026-08-06T06:07:16Z")), apply=True
    )
    del plan
    comments = list(store.fold().comments.values())
    assert len(comments) == 1
    recorded = comments[0]
    assert recorded.author == "bob"
    assert recorded.body == "too short for the proxy"
    assert recorded.ext == {
        EXT_KEY: {"source": "cmux", "id": "ext-1", "ts": "2026-08-06T06:07:16Z"}
    }
    assert recorded.anchor is not None and recorded.anchor.exact == "30 seconds"


def test_an_item_without_an_author_records_the_caller(imported, store):
    imported(batch(item(quote="30 seconds")), apply=True, author="agent:importer")
    assert list(store.fold().comments.values())[0].author == "agent:importer"


def test_a_source_without_a_timestamp_carries_no_ts(imported, store):
    imported(batch(item(quote="30 seconds")), apply=True)
    assert list(store.fold().comments.values())[0].ext == {
        EXT_KEY: {"source": "cmux", "id": "ext-1"}
    }


def test_planning_alone_writes_nothing(imported, store):
    imported(batch(item(quote="30 seconds")))
    assert store.fold().comments == {}


# -- idempotency ---------------------------------------------------------


def test_the_same_file_imported_twice_records_once(imported, store):
    payload = batch(item(quote="30 seconds"), item("ext-2", "and the retries", whole=True))
    imported(payload, apply=True)
    again = imported(payload, apply=True)
    assert again.planned == []
    assert [entry.item.id for entry in again.skipped] == ["ext-1", "ext-2"]
    assert len(store.fold().comments) == 2


def test_a_skip_names_the_comment_that_is_already_there(imported, store):
    imported(batch(item(quote="30 seconds")), apply=True)
    existing = next(iter(store.fold().comments))
    assert imported(batch(item(quote="30 seconds"))).skipped[0].comment == existing


def test_the_same_id_from_a_different_source_is_a_different_comment(imported, store):
    imported(batch(item(quote="30 seconds")), apply=True)
    plan = imported(batch(item(quote="30 seconds"), source="other-tool"), apply=True)
    assert len(plan.planned) == 1
    assert len(store.fold().comments) == 2


def test_a_comment_this_tool_did_not_import_is_not_mistaken_for_one(store, round_id, doc, imported):
    store.add_comment(round_id, author="bob", body="hand written", ext={EXT_KEY: "not an object"})
    plan = imported(batch(item(quote="30 seconds")))
    assert len(plan.planned) == 1
    assert plan.skipped == []


def test_re_running_after_a_refusal_lands_the_fixed_item(imported, store):
    first = imported(
        batch(item(quote="30 seconds"), item("ext-2", "missing", quote="not in the base")),
        apply=True,
    )
    assert len(first.planned) == 1 and len(first.rejected) == 1

    fixed = imported(
        batch(item(quote="30 seconds"), item("ext-2", "missing", quote="Retries are not specified yet")),
        apply=True,
    )
    assert [entry.item.id for entry in fixed.skipped] == ["ext-1"]
    assert [entry.item.id for entry in fixed.planned] == ["ext-2"]
    assert len(store.fold().comments) == 2


# -- the round -----------------------------------------------------------


def test_a_closed_round_takes_no_imports(store, round_id, doc):
    store.close_round(round_id, author="alice")
    plan = plan_import(
        store, round_id, store.doc_key(doc), parse_batch(batch(item(quote="30 seconds")))
    )
    with pytest.raises(InvariantError):
        apply_plan(plan, store, author="agent:importer")


def test_every_item_ends_in_exactly_one_bucket(imported):
    imported(batch(item(quote="30 seconds")), apply=True)
    plan = imported(
        batch(
            item(quote="30 seconds"),
            item("ext-2", "lands", quote="Retries are not specified yet"),
            item("ext-3", "refused", quote="nowhere in this document"),
        )
    )
    assert plan.total == 3
    assert (len(plan.planned), len(plan.skipped), len(plan.rejected)) == (1, 1, 1)
