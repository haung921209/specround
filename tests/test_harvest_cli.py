"""``specround harvest`` from a shell (G6, G7).

Same three things ``test_cli.py`` tests, aimed at the one verb that writes to
the reviewer's file. The gate gets the most attention: a dry run is the default,
it has to compute everything the applied run would, and it has to refuse
everything the applied run would — a preview that is easier to pass tells you
nothing about what ``--apply`` will do.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from specround.cli import CLI_SCHEMA, main

ANNOTATED = """# Widget protocol

The client sends a hello frame. The server answers with a hello frame.

Timeouts are {~~30~>60~~} seconds.{>>the proxy caps at 45s<<} \
Retries are {--not --}specified yet.{++ See RFC 1.++}
"""

HARVESTED = """# Widget protocol

The client sends a hello frame. The server answers with a hello frame.

Timeouts are 30 seconds. Retries are not specified yet.
"""


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str

    @property
    def json(self) -> dict:
        return json.loads(self.out)

    @property
    def error(self) -> dict:
        return json.loads(self.err)["error"]


@pytest.fixture
def run(capsys):
    def invoke(*argv) -> Result:
        code = main([str(arg) for arg in argv])
        captured = capsys.readouterr()
        return Result(code=code, out=captured.out, err=captured.err)

    return invoke


@pytest.fixture
def opened(run, doc):
    result = run("round", "open", doc, "--author", "alice", "--json")
    assert result.code == 0
    return result.json["round"]["id"]


@pytest.fixture
def annotated(doc, opened):
    doc.write_text(ANNOTATED, encoding="utf-8")
    return doc


# -- the dry run is the default ------------------------------------------


def test_harvest_previews_without_touching_anything(run, annotated):
    before = annotated.read_bytes()
    result = run("harvest", annotated, "--author", "bob")
    assert result.code == 0
    assert "would harvest 1 comment(s) and 3 suggestion(s)" in result.out
    assert "re-run with --apply" in result.out
    assert annotated.read_bytes() == before


def test_the_preview_shows_what_each_marker_becomes(run, annotated):
    result = run("harvest", annotated, "--author", "bob")
    assert "substitute" in result.out
    assert "'30' → '60'" in result.out
    assert "remove 'not'" in result.out
    assert "the proxy caps at 45s" in result.out
    # A point form has no quote to show, and saying so beats an empty cell.
    assert "(point)" in result.out


def test_the_preview_records_no_events(run, annotated):
    run("harvest", annotated, "--author", "bob")
    listed = run("comments", annotated, "--json")
    assert listed.json["comments"] == []


# -- applying ------------------------------------------------------------


def test_apply_records_the_annotations_and_rewrites_the_file(run, annotated):
    result = run("harvest", annotated, "--author", "bob", "--apply")
    assert result.code == 0
    assert "harvested 1 comment(s) and 3 suggestion(s)" in result.out
    assert "rewrote" in result.out
    assert annotated.read_text(encoding="utf-8") == HARVESTED


def test_the_recorded_comments_show_up_in_the_listing(run, annotated):
    run("harvest", annotated, "--author", "bob", "--apply")
    listed = run("comments", annotated, "--json")
    kinds = sorted(c["kind"] for c in listed.json["comments"])
    assert kinds == ["comment", "suggestion", "suggestion", "suggestion"]
    assert {c["author"] for c in listed.json["comments"]} == {"bob"}


def test_a_second_apply_finds_nothing(run, annotated):
    run("harvest", annotated, "--author", "bob", "--apply")
    result = run("harvest", annotated, "--author", "bob", "--apply")
    assert result.code == 0
    assert "no inline annotations" in result.out
    assert annotated.read_text(encoding="utf-8") == HARVESTED


def test_harvest_can_name_its_round(run, annotated, opened):
    result = run("harvest", annotated, "--author", "bob", "--round", opened, "--json")
    assert result.code == 0
    assert result.json["round"]["id"] == opened


# -- markers left behind -------------------------------------------------


def test_markers_left_in_the_document_are_reported(run, doc, opened):
    doc.write_text(HARVESTED + "{--dangling\n{==highlight==}\n", encoding="utf-8")
    result = run("harvest", doc, "--author", "bob", "--apply")
    assert result.code == 0
    # Hiding these would read as "there was nothing else", which is the failure
    # the report-and-leave rule exists to avoid.
    assert "2 marker(s) left in the document" in result.out
    assert "unterminated" in result.out
    assert "unsupported" in result.out
    assert doc.read_text(encoding="utf-8").endswith("{--dangling\n{==highlight==}\n")


def test_a_document_with_nothing_in_it_says_so(run, doc, opened):
    result = run("harvest", doc, "--author", "bob")
    assert result.code == 0
    assert f"no inline annotations in {doc.name}" in result.out


# -- the exit codes ------------------------------------------------------


def test_no_open_round_is_a_three(run, doc):
    doc.write_text(ANNOTATED, encoding="utf-8")
    result = run("harvest", doc, "--author", "bob", "--json")
    # The command is fine and the history refuses it: open a round.
    assert result.code == 3
    assert result.error["kind"] == "state"
    assert "no open round" in result.error["message"]


def test_a_closed_round_named_explicitly_is_a_three(run, annotated, opened):
    assert run("round", "close", annotated, "--author", "alice", "--allow-unresolved").code == 0
    result = run("harvest", annotated, "--author", "bob", "--round", opened, "--json")
    assert result.code == 3
    assert "closed" in result.error["message"]


def test_a_marker_that_cannot_be_harvested_as_written_is_a_two(run, doc, opened):
    doc.write_text(HARVESTED + "\nAnd {>><<} nothing.\n", encoding="utf-8")
    before = doc.read_bytes()
    result = run("harvest", doc, "--author", "bob", "--json")
    # The document is what has to change, and 2 is the code that says "fix your
    # input and run it again".
    assert result.code == 2
    assert result.error["kind"] == "usage"
    assert "nothing in it" in result.error["message"]
    assert doc.read_bytes() == before


def test_a_marker_with_no_place_in_the_base_is_a_three(run, doc, opened):
    doc.write_text(
        "# Widget protocol\n\nThis page was replaced {--wholesale --}entirely.\n",
        encoding="utf-8",
    )
    result = run("harvest", doc, "--author", "bob", "--json")
    assert result.code == 3
    assert "close this round and open a new one" in result.error["message"]


def test_a_missing_document_is_a_two(run, tmp_path):
    result = run("harvest", tmp_path / "gone.md", "--author", "bob", "--json")
    assert result.code == 2
    assert result.error["kind"] == "usage"


# -- the JSON shape ------------------------------------------------------


def test_the_json_envelope_is_the_shape_a_consumer_parses(run, annotated):
    result = run("harvest", annotated, "--author", "bob", "--json")
    assert result.code == 0
    payload = result.json
    assert set(payload) == {
        "schema",
        "verb",
        "doc",
        "path",
        "store",
        "round",
        "base",
        "applied",
        "rewrite",
        "annotations",
        "skipped",
        "counts",
    }
    assert payload["schema"] == CLI_SCHEMA
    assert payload["verb"] == "harvest"
    assert payload["applied"] is False
    assert payload["rewrite"] is True
    assert payload["counts"] == {"comments": 1, "suggestions": 3, "skipped": 0}


def test_each_annotation_carries_its_anchor_and_its_provenance(run, annotated):
    payload = run("harvest", annotated, "--author", "bob", "--json").json
    for annotation in payload["annotations"]:
        assert set(annotation) == {
            "kind",
            "event",
            "body",
            "removed",
            "added",
            "anchor",
            "strategy",
            "ambiguous",
            "line",
        }
        assert annotation["event"] is None  # a dry run records nothing
        assert annotation["strategy"] is None  # the ordinary case is an exact cut
    kinds = [a["kind"] for a in payload["annotations"]]
    assert kinds == ["substitute", "comment", "delete", "insert"]
    # The exact text of an insertion survives in JSON even though the table clips
    # it — the machine-readable surface is the one an agent applies from.
    (insertion,) = [a for a in payload["annotations"] if a["kind"] == "insert"]
    assert insertion["added"] == " See RFC 1."
    assert insertion["anchor"]["exact"] == ""


def test_applying_fills_in_the_event_ids(run, annotated):
    payload = run("harvest", annotated, "--author", "bob", "--apply", "--json").json
    assert payload["applied"] is True
    ids = [a["event"] for a in payload["annotations"]]
    assert all(ids)
    assert ids[1].startswith("c-")  # the comment
    assert [i[0] for i in ids] == ["s", "c", "s", "s"]


def test_skipped_markers_are_in_the_json_too(run, doc, opened):
    doc.write_text(HARVESTED + "{--dangling\n", encoding="utf-8")
    payload = run("harvest", doc, "--author", "bob", "--json").json
    (skipped,) = payload["skipped"]
    assert set(skipped) == {"reason", "opener", "start", "line", "text"}
    assert skipped["reason"] == "unterminated"
    assert skipped["opener"] == "{--"
    assert payload["counts"]["skipped"] == 1
    assert payload["rewrite"] is False  # nothing harvestable, nothing to write


# -- provenance surfaces when the ladder had to work ---------------------


def test_a_carried_anchor_is_flagged_for_a_person(run, doc, opened, doc_text):
    doc.write_text("> Draft.\n\n" + ANNOTATED, encoding="utf-8")
    result = run("harvest", doc, "--author", "bob", "--json")
    assert result.code == 0
    carried = [a for a in result.json["annotations"] if a["strategy"] is not None]
    assert carried
    human = run("harvest", doc, "--author", "bob")
    assert "carried into the base by the ladder — worth a look" in human.out


def test_help_names_the_four_forms(run):
    result = run("harvest", "--help")
    assert result.code == 0
    for form in ("{>>", "{++", "{--", "{~~"):
        assert form in result.out
