"""The format document is part of the contract, so it is tested like one.

Two kinds of drift are caught here: the document forgetting a field the code
knows about, and the document's worked example no longer being something the
code accepts. A format doc that quietly stops matching the format is worse than
no doc, because people plan against it.
"""

import json
from pathlib import Path

import pytest

import specround
from specround.anchors import Anchor
from specround.events import EVENT_TYPES, SCHEMA, VERDICTS, validate_event
from specround.fold import fold
from specround.locations import CONFIG_FILENAME, MODES, ORIGIN_SCHEMA, SOURCES

DOC_PATH = Path(specround.__file__).parents[2] / "docs" / "ledger-format.md"
INVARIANT_IDS = [f"I{n}" for n in range(1, 11)]


@pytest.fixture(scope="module")
def doc_text():
    if not DOC_PATH.is_file():
        pytest.skip(f"{DOC_PATH} is not present in this layout")
    return DOC_PATH.read_text(encoding="utf-8")


def jsonl_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("```jsonl"):
            current = []
        elif line.startswith("```") and current is not None:
            blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return blocks


def test_the_document_exists_and_names_the_current_schema(doc_text):
    assert SCHEMA in doc_text


@pytest.mark.parametrize("kind", EVENT_TYPES)
def test_every_event_type_is_documented(kind, doc_text):
    assert f"`{kind}`" in doc_text


@pytest.mark.parametrize("verdict", VERDICTS)
def test_every_verdict_is_documented(verdict, doc_text):
    assert f"`{verdict}`" in doc_text


@pytest.mark.parametrize("invariant", INVARIANT_IDS)
def test_every_invariant_id_is_documented(invariant, doc_text):
    assert f"| {invariant} |" in doc_text


def test_the_config_file_and_its_modes_are_documented(doc_text):
    # Where a store lives is now a decision the reader makes, so the names they
    # would type belong to the contract too.
    assert f"`{CONFIG_FILENAME}`" in doc_text
    for mode in MODES:
        assert f"`{mode}`" in doc_text


def test_the_resolution_order_is_stated(doc_text):
    assert "인자 > 설정 > 기본" in doc_text
    korean = {"argument": "인자", "config": "설정", "default": "기본"}
    for source in SOURCES:
        assert korean[source] in doc_text


def test_the_origin_record_is_documented_with_its_own_schema(doc_text):
    assert f"`{ORIGIN_SCHEMA}`" in doc_text
    for kind in ("document", "directory"):
        assert f"`{kind}`" in doc_text


def test_every_payload_field_the_code_knows_is_documented(doc_text):
    from specround.events import _ENVELOPE, _PAYLOAD

    names = set(_ENVELOPE)
    for required, optional in _PAYLOAD.values():
        names |= set(required) | set(optional)
    undocumented = sorted(name for name in names if f"`{name}`" not in doc_text)
    assert undocumented == []


def test_the_worked_example_is_a_valid_ledger(doc_text):
    blocks = jsonl_blocks(doc_text)
    assert len(blocks) == 1, "the document should carry exactly one example ledger"
    records = [json.loads(line) for line in blocks[0]]
    for record in records:
        validate_event(record)
    assert [r["seq"] for r in records] == list(range(len(records)))


def test_the_worked_example_folds_to_what_the_document_claims(doc_text):
    records = [json.loads(line) for line in jsonl_blocks(doc_text)[0]]
    state = fold(records)
    # The prose says: no open rounds, one unresolved comment, deferred.
    assert state.open_rounds == []
    assert [(c.id, c.state) for c in state.unresolved] == [("c-7863abd8f91e", "deferred")]
    assert len(state.comments) == 3
    assert sorted(c.state for c in state.comments.values()) == [
        "applied",
        "deferred",
        "rejected",
    ]
    closed = next(iter(state.rounds.values()))
    assert closed.unresolved_at_close == ["c-7863abd8f91e"]
    # And: two orphans after the third revision, one of them already settled —
    # the prose makes a point of the axes being independent.
    assert [c.id for c in state.orphans] == ["c-d35c1ebd2b14", "s-086c5beb81f0"]
    assert state.comments["c-d35c1ebd2b14"].state == "applied"
    # The thread axis is the third one: one conversation closed, one closed and
    # re-opened, so the default listing shows two of the three comments.
    assert [c.id for c in state.resolved_threads] == ["c-d35c1ebd2b14"]
    assert [c.id for c in state.threads()] == ["s-086c5beb81f0", "c-7863abd8f91e"]
    assert [c.id for c in state.threads(include_resolved=True)] == list(state.comments)
    assert [r.actor for r in state.comments["s-086c5beb81f0"].resolutions] == [
        "human",
        "human",
    ]
    assert state.comments["c-d35c1ebd2b14"].resolution.actor == "agent"


def test_the_worked_example_shows_every_event_type(doc_text):
    """The example is the doc's only executable part — it should cover the format."""
    records = [json.loads(line) for line in jsonl_blocks(doc_text)[0]]
    assert {r["type"] for r in records} == set(EVENT_TYPES)


def test_the_worked_example_anchors_hold_against_the_snapshots_it_names(doc_text):
    """Every anchor in the example is consistent with the text it claims.

    The example is generated, not hand written, and this is what keeps it that
    way: a snapshot digest edited by hand stops matching its anchor here.
    """
    from specround.snapshots import digest_text

    revisions = {
        digest_text(text): text
        for text in (
            "# Widget protocol\n\nTimeouts are 30 seconds. Retries are not specified yet.\n",
            "# Widget protocol\n\nTimeouts are 60 seconds. Retries are not specified yet.\n",
            "# Widget protocol\n\nRetries are not specified yet.\n",
        )
    }
    records = [json.loads(line) for line in jsonl_blocks(doc_text)[0]]
    rounds = {r["id"]: r["base"] for r in records if r["type"] == "round.open"}
    checked = 0
    for record in records:
        if "anchor" not in record:
            continue
        base = record.get("base") or rounds[record["round"]]
        assert base in revisions, f"{record['id']} names a snapshot the example never shows"
        Anchor.from_json(record["anchor"]).verify(revisions[base])
        checked += 1
    assert checked == 4  # two originals, two re-anchored


def test_the_worked_example_lines_are_canonical(doc_text):
    from specround.events import canonical_json

    for line in jsonl_blocks(doc_text)[0]:
        assert line == canonical_json(json.loads(line))
