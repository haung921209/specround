"""The shell surface (G4, G7).

Three things are under test here and they are not the same thing.

**The loop works from a shell.** Every verb gets a happy path, because a review
someone can only drive from Python is not the guarantee G7 makes.

**The exit code is the verdict.** Each code has a case that produces it, and the
2/3 split is tested where it actually decides something: a caller that gets 3
has to change the history, a caller that gets 2 has to change the command. If
those collapse into one code the CLI stops being usable by an agent, which is
the whole point of G4.

**The ``--json`` shape is stable.** The field sets are asserted with ``==``,
not ``<=``. A test that only checks the fields it needs lets a field appear or
vanish without anyone noticing, and the consumers of this output are programs.
"""

import getpass
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from specround.cli import CLI_SCHEMA, main
from specround.store import ReviewStore

REVISED = """# Widget protocol

An introductory line that was not here before.

The client sends a hello frame. The server answers with a hello frame.

Timeouts are 60 seconds. Retries are not specified yet.
"""

#: A revision that leaves nothing for the anchor to land on. It has to be this
#: blunt: with the original prose still around, the fuzzy rung finds a partial
#: match and the comment moves instead of orphaning.
REWRITTEN = """# Widget protocol

This page was replaced wholesale.
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

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


@pytest.fixture
def run(capsys):
    """Invoke the CLI the way a shell would, and hand back the whole result.

    ``main`` returns the code rather than raising ``SystemExit`` so a test can
    assert on the code, stdout, and stderr together — the three halves of the
    contract are only meaningful next to each other.
    """

    def invoke(*argv) -> Result:
        code = main([str(arg) for arg in argv])
        captured = capsys.readouterr()
        return Result(code=code, out=captured.out, err=captured.err)

    return invoke


@pytest.fixture
def opened(run, doc):
    """A document with a round already open — the state most verbs need."""
    result = run("round", "open", doc, "--author", "alice", "--title", "first pass", "--json")
    assert result.code == 0
    return result.json["round"]["id"]


def a_comment(run, doc, *, quote="30 seconds", body="too short for the proxy") -> str:
    argv = ["comment", doc, "--author", "bob", "--body", body, "--json"]
    if quote is not None:
        argv += ["--quote", quote]
    result = run(*argv)
    assert result.code == 0, result.err
    return result.json["comment"]["id"]


# -- the loop, one verb at a time ----------------------------------------


def test_round_open_freezes_the_document_and_names_the_round(run, doc, doc_text):
    result = run("round", "open", doc, "--author", "alice", "--title", "first pass", "--json")
    assert result.code == 0
    round_ = result.json["round"]
    assert round_["id"].startswith("r-")
    assert round_["status"] == "open"
    assert round_["title"] == "first pass"
    # The base is the snapshot, not a commit: the document was never staged.
    store = ReviewStore.open(Path(result.json["store"]))
    assert store.snapshots.get_text(round_["base"]) == doc_text


def test_round_open_leaves_the_documents_folder_alone(run, doc, tmp_path):
    assert run("round", "open", doc, "--author", "alice").code == 0
    assert [p.name for p in tmp_path.iterdir()] == [doc.name]


def test_round_status_counts_what_is_outstanding(run, doc, opened):
    a_comment(run, doc)
    a_comment(run, doc, quote=None, body="retry policy is missing")
    result = run("round", "status", doc, "--json")
    assert result.code == 0
    assert result.json["counts"] == {
        "rounds": 1,
        "comments": 2,
        "unresolved": 2,
        "orphans": 0,
        "events": 3,
    }
    assert result.json["open"] == [opened]


def test_round_status_on_a_fresh_document_is_not_an_error(run, doc):
    result = run("round", "status", doc)
    assert result.code == 0
    assert "no rounds yet" in result.out


def test_comment_anchors_to_the_quoted_span(run, doc, opened):
    result = run(
        "comment", doc, "--author", "bob", "--quote", "30 seconds",
        "--body", "too short for the proxy", "--json",
    )
    assert result.code == 0
    comment = result.json["comment"]
    assert comment["anchor"]["exact"] == "30 seconds"
    assert comment["round"] == opened
    assert comment["state"] == "open"


def test_comment_without_a_quote_lands_on_the_document(run, doc, opened):
    result = run("comment", doc, "--author", "bob", "--body", "the whole thing is vague", "--json")
    assert result.code == 0
    assert result.json["comment"]["anchor"] is None


def test_comments_lists_the_round_and_the_disposition(run, doc, opened):
    first = a_comment(run, doc)
    a_comment(run, doc, quote=None, body="retry policy is missing")
    assert run("dispose", doc, "--comment", first, "--as", "applied",
               "--why", "raised to 60", "--author", "alice").code == 0

    result = run("comments", doc, "--json")
    assert result.code == 0
    states = {c["id"]: c["state"] for c in result.json["comments"]}
    assert states[first] == "applied"
    assert all(c["round"] == opened for c in result.json["comments"])


def test_comments_can_be_narrowed_to_the_unresolved(run, doc, opened):
    first = a_comment(run, doc)
    second = a_comment(run, doc, quote=None, body="retry policy is missing")
    run("dispose", doc, "--comment", first, "--as", "applied", "--why", "done", "--author", "alice")

    result = run("comments", doc, "--unresolved", "--json")
    assert [c["id"] for c in result.json["comments"]] == [second]


def test_reanchor_reports_what_moved(run, doc, opened):
    comment = a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")

    result = run("reanchor", doc, "--author", "agent:reanchor", "--json")
    assert result.code == 0
    assert result.json["rebound"] == [comment]
    assert result.json["orphaned"] == []
    assert result.json["changed"] is True
    # Which rung matched is the difference between a comment that was pushed
    # down the page and one whose sentence was rewritten under it.
    assert result.json["strategies"][comment] in {"quote", "normalized", "fuzzy"}


def test_reanchor_reports_what_was_lost(run, doc, opened):
    comment = a_comment(run, doc)
    doc.write_text(REWRITTEN, encoding="utf-8")

    result = run("reanchor", doc, "--author", "agent:reanchor", "--json")
    assert result.code == 0
    assert result.json["orphaned"] == [comment]
    assert result.json["reasons"][comment]
    # An orphan is not a disposition: nobody answered it, it just cannot be
    # placed on the page any more.
    assert run("comments", doc, "--json").json["comments"][0]["state"] == "open"


def test_reanchor_a_second_time_says_nothing_new(run, doc, opened):
    a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")
    assert run("reanchor", doc, "--author", "agent:reanchor").code == 0

    again = run("reanchor", doc, "--author", "agent:reanchor", "--json")
    assert again.json["changed"] is False
    assert again.json["rebound"] == []


def test_dispose_settles_a_comment_with_its_reason(run, doc, opened):
    comment = a_comment(run, doc)
    result = run(
        "dispose", doc, "--comment", comment, "--as", "applied",
        "--why", "raised to 60 in revision 2", "--author", "alice", "--json",
    )
    assert result.code == 0
    assert result.json["disposition"]["verdict"] == "applied"
    assert result.json["disposition"]["reason"] == "raised to 60 in revision 2"
    assert result.json["comment"]["unresolved"] is False


def test_dispose_takes_a_prefix_of_the_id(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("dispose", doc, "--comment", comment[:6], "--as", "answered",
                 "--why", "the proxy caps at 45s", "--author", "alice", "--json")
    assert result.code == 0
    assert result.json["comment"]["id"] == comment


def test_round_close_records_what_it_left_open(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("round", "close", doc, "--author", "alice", "--allow-unresolved",
                 "--note", "retries move to round 2", "--json")
    assert result.code == 0
    assert result.json["unresolved"] == [comment]
    assert result.json["round"]["status"] == "closed"
    assert result.json["round"]["close_note"] == "retries move to round 2"


# -- the exit code contract ----------------------------------------------


def test_an_unknown_verb_is_a_usage_error(run):
    assert run("frobnicate", "x.md").code == 2


def test_no_verb_at_all_is_a_usage_error(run):
    assert run().code == 2


def test_help_and_version_are_not_failures(run):
    # They leave through the same SystemExit path argparse's errors do, and a
    # code of None there has to read as success rather than as a falsy 2.
    assert run("--version").code == 0
    assert run("--help").code == 0


def test_a_missing_document_is_a_usage_error(run, tmp_path):
    # Not an empty listing: the store is keyed by path, so a typo would address
    # a different (empty) history and the answer would read as a fact.
    result = run("comments", tmp_path / "typo.md")
    assert result.code == 2
    assert "not a file" in result.err


def test_commenting_without_an_open_round_is_a_state_error(run, doc):
    result = run("comment", doc, "--author", "bob", "--body", "早い")
    assert result.code == 3
    assert "round open" in result.err


def test_commenting_after_the_round_closed_is_a_state_error(run, doc, opened):
    assert run("round", "close", doc, "--author", "alice").code == 0
    assert run("comment", doc, "--author", "bob", "--body", "late").code == 3


def test_a_second_open_round_is_refused(run, doc, opened):
    result = run("round", "open", doc, "--author", "alice")
    assert result.code == 3
    assert opened in result.err


def test_a_repeated_quote_refuses_to_pick(run, doc, opened):
    result = run("comment", doc, "--author", "bob", "--quote", "hello frame", "--body", "x")
    assert result.code == 2
    assert "--occurrence" in result.err


def test_an_overlapping_repeat_still_refuses_to_pick(run, doc, tmp_path):
    # str.count would call this unique and let the tool pick silently, while
    # the anchor indexer walks overlaps and can address both.
    overlapping = tmp_path / "aaa.md"
    overlapping.write_text("aaa\n", encoding="utf-8")
    assert run("round", "open", overlapping, "--author", "alice").code == 0
    result = run("comment", overlapping, "--author", "bob", "--quote", "aa", "--body", "x")
    assert result.code == 2
    assert "--occurrence 0..1" in result.err


def test_an_occurrence_resolves_the_repeat(run, doc, opened):
    result = run("comment", doc, "--author", "bob", "--quote", "hello frame",
                 "--occurrence", "1", "--body", "which one", "--json")
    assert result.code == 0
    assert result.json["comment"]["anchor"]["exact"] == "hello frame"


def test_a_quote_that_is_not_in_the_base_is_a_usage_error(run, doc, opened):
    doc.write_text(REVISED, encoding="utf-8")
    # The round's base is frozen; quoting the file as it is now must fail loudly
    # rather than land on whatever those offsets point at today.
    result = run("comment", doc, "--author", "bob", "--quote", "introductory line", "--body", "x")
    assert result.code == 2
    assert "snapshot" in result.err


def test_re_disposing_a_settled_comment_is_a_state_error(run, doc, opened):
    comment = a_comment(run, doc)
    run("dispose", doc, "--comment", comment, "--as", "applied", "--why", "done", "--author", "alice")
    result = run("dispose", doc, "--comment", comment, "--as", "rejected",
                 "--why", "changed my mind", "--author", "alice")
    assert result.code == 3


def test_a_deferred_comment_can_be_disposed_again(run, doc, opened):
    comment = a_comment(run, doc)
    assert run("dispose", doc, "--comment", comment, "--as", "held",
               "--why", "waiting on the proxy team", "--author", "alice").code == 0
    assert run("dispose", doc, "--comment", comment, "--as", "applied",
               "--why", "raised to 60", "--author", "alice").code == 0


def test_closing_over_unresolved_comments_is_a_state_error(run, doc, opened):
    a_comment(run, doc)
    result = run("round", "close", doc, "--author", "alice")
    assert result.code == 3
    # The message names the flag this surface has, not the library keyword.
    assert "--allow-unresolved" in result.err


def test_an_unknown_comment_id_is_a_usage_error(run, doc, opened):
    a_comment(run, doc)
    result = run("dispose", doc, "--comment", "c-nosuchthing", "--as", "applied",
                 "--why", "x", "--author", "alice")
    assert result.code == 2


def test_an_ambiguous_comment_prefix_is_a_usage_error(run, doc, opened):
    a_comment(run, doc)
    a_comment(run, doc, quote=None, body="second")
    result = run("dispose", doc, "--comment", "c-", "--as", "applied",
                 "--why", "x", "--author", "alice")
    assert result.code == 2
    assert "give more of the id" in result.err


def test_an_unknown_verdict_is_a_usage_error(run, doc, opened):
    comment = a_comment(run, doc)
    assert run("dispose", doc, "--comment", comment, "--as", "wontfix",
               "--why", "x", "--author", "alice").code == 2


def test_a_missing_reason_is_a_usage_error(run, doc, opened):
    comment = a_comment(run, doc)
    assert run("dispose", doc, "--comment", comment, "--as", "applied", "--author", "alice").code == 2


def test_a_comment_needs_a_body(run, doc, opened):
    result = run("comment", doc, "--author", "bob", "--quote", "30 seconds")
    assert result.code == 2
    assert "--body" in result.err


def test_body_and_body_file_together_are_a_usage_error(run, doc, opened, tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("from a file", encoding="utf-8")
    result = run("comment", doc, "--author", "bob", "--body", "inline", "--body-file", note)
    assert result.code == 2


def test_a_broken_ledger_is_neither_usage_nor_state(run, doc, opened):
    store = Path(run("round", "status", doc, "--json").json["store"])
    (store / "ledger.jsonl").write_text("this is not json\n", encoding="utf-8")
    # Corruption is not the history refusing a well formed request, and it is
    # not something a different command line would fix.
    assert run("comments", doc).code == 1


def test_errors_go_to_stderr_so_json_stdout_stays_parseable(run, doc):
    result = run("comment", doc, "--author", "bob", "--body", "x", "--json")
    assert result.code == 3
    assert result.out == ""
    assert result.error["kind"] == "state"


# -- the --json shape ----------------------------------------------------


def test_every_payload_names_its_schema_verb_and_subject(run, doc, opened):
    a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")
    verbs = {
        "round.status": ("round", "status", doc),
        "comments": ("comments", doc),
        "reanchor": ("reanchor", doc, "--author", "agent:reanchor"),
    }
    for verb, argv in verbs.items():
        payload = run(*argv, "--json").json
        assert payload["schema"] == CLI_SCHEMA
        assert payload["verb"] == verb
        assert payload["doc"] == doc.name
        assert payload["path"] == str(doc)
        assert payload["store"]


def test_the_comment_object_field_set_is_closed(run, doc, opened):
    comment = a_comment(run, doc)
    run("dispose", doc, "--comment", comment, "--as", "held", "--why", "later", "--author", "alice")
    payload = run("comments", doc, "--json").json["comments"][0]
    assert set(payload) == {
        "ambiguous",
        "anchor",
        "anchorings",
        "author",
        "body",
        "current_anchor",
        "dispositions",
        "id",
        "kind",
        "orphaned",
        "patch",
        "replies",
        "round",
        "state",
        "strategy",
        "ts",
        "unresolved",
    }
    assert set(payload["dispositions"][0]) == {"author", "id", "reason", "ts", "verdict"}


def test_the_anchoring_object_field_set_is_closed(run, doc, opened):
    a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")
    run("reanchor", doc, "--author", "agent:reanchor")
    payload = run("comments", doc, "--json").json["comments"][0]["anchorings"][0]
    assert set(payload) == {
        "ambiguous",
        "anchor",
        "author",
        "base",
        "id",
        "reason",
        "strategy",
        "ts",
    }


# -- how a comment got where it is, after the fact ------------------------


def test_a_comment_carries_how_it_was_re_anchored(run, doc, opened):
    """The strategy has to outlive the ``reanchor`` run that produced it.

    ``fuzzy`` is the ledger's word for "the quoted text was rewritten, a person
    should look" (§4). If it only ever appears in the output of the run that
    moved the comment, the reviewer who reads the list afterwards — the person it
    was written for — never sees it.
    """
    comment = a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")
    run("reanchor", doc, "--author", "agent:reanchor")

    payload = run("comments", doc, "--json").json["comments"][0]
    assert payload["id"] == comment
    assert payload["strategy"] == "fuzzy"  # 30 seconds became 60 seconds
    assert payload["ambiguous"] is False
    assert payload["orphaned"] is False
    assert [a["strategy"] for a in payload["anchorings"]] == ["fuzzy"]


def test_a_comment_that_never_moved_says_so(run, doc, opened):
    a_comment(run, doc)
    payload = run("comments", doc, "--json").json["comments"][0]
    assert payload["strategy"] is None
    assert payload["ambiguous"] is False
    assert payload["anchorings"] == []


def test_an_orphan_still_reports_how_it_reached_the_anchor_it_keeps(run, doc, opened):
    """Orphaning does not erase the history that placed it — nor should the view."""
    a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")
    run("reanchor", doc, "--author", "agent:reanchor")
    doc.write_text(REWRITTEN, encoding="utf-8")
    run("reanchor", doc, "--author", "agent:reanchor")

    payload = run("comments", doc, "--json").json["comments"][0]
    assert payload["orphaned"] is True
    assert payload["strategy"] == "fuzzy"  # how it got to the anchor it is keeping
    assert [a["strategy"] for a in payload["anchorings"]] == ["fuzzy", None]
    assert payload["anchorings"][-1]["reason"]


def test_the_listing_flags_a_comment_that_moved_onto_rewritten_text(run, doc, opened):
    comment = a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")
    run("reanchor", doc, "--author", "agent:reanchor")

    result = run("comments", doc)
    assert result.code == 0
    assert any("worth a look" in line and comment in line for line in result.lines)
    assert any("fuzzy" in line for line in result.lines)


def test_the_listing_stays_quiet_about_an_ordinary_move(run, doc, opened, doc_text):
    """A comment pushed down the page matched verbatim. Nobody needs telling."""
    comment = a_comment(run, doc)
    doc.write_text("> Draft, do not circulate.\n\n" + doc_text, encoding="utf-8")
    run("reanchor", doc, "--author", "agent:reanchor")

    result = run("comments", doc)
    assert run("comments", doc, "--json").json["comments"][0]["strategy"] == "quote"
    assert not any("worth a look" in line for line in result.lines)
    assert any(comment in line for line in result.lines)


def test_the_round_object_field_set_is_closed(run, doc, opened):
    payload = run("round", "status", doc, "--json").json["rounds"][0]
    assert set(payload) == {
        "author",
        "base",
        "close_note",
        "closed_by",
        "closed_ts",
        "comment_count",
        "doc",
        "id",
        "status",
        "title",
        "ts",
        "unresolved_at_close",
        "unresolved_count",
    }


def test_the_status_payload_field_set_is_closed(run, doc, opened):
    payload = run("round", "status", doc, "--json").json
    assert set(payload) == {
        "counts", "doc", "open", "orphans", "path", "rounds", "schema", "store",
        "unresolved", "verb",
    }
    assert set(payload["counts"]) == {"comments", "events", "orphans", "rounds", "unresolved"}


def test_the_reanchor_payload_field_set_is_closed(run, doc, opened):
    a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")
    payload = run("reanchor", doc, "--author", "agent:reanchor", "--json").json
    assert set(payload) == {
        "ambiguous", "base", "changed", "doc", "orphaned", "path", "reasons",
        "rebound", "schema", "skipped", "store", "strategies", "unchanged", "verb",
    }


def test_an_error_payload_says_which_kind_and_which_code(run, doc):
    result = run("comment", doc, "--author", "bob", "--body", "x", "--json")
    envelope = json.loads(result.err)
    assert envelope["schema"] == CLI_SCHEMA
    assert envelope["verb"] == "comment"
    assert set(envelope["error"]) == {"exit", "kind", "message"}
    assert envelope["error"]["exit"] == result.code


def test_korean_survives_the_json_round_trip(run, doc, opened):
    # The ledger does not escape Korean so that cat is a valid reader; the CLI
    # would undo that promise if it re-escaped on the way out.
    body = "이 값은 프록시 기준으로 너무 짧다"
    result = run("comment", doc, "--author", "bob", "--body", body, "--json")
    assert result.json["comment"]["body"] == body
    assert body in result.out


# -- who is speaking (G4) ------------------------------------------------


def test_the_author_flag_wins_over_the_environment(run, doc, monkeypatch):
    monkeypatch.setenv("SPECROUND_AUTHOR", "agent:reviewer")
    result = run("round", "open", doc, "--author", "alice", "--json")
    assert result.json["round"]["author"] == "alice"


def test_the_environment_names_the_agent(run, doc, monkeypatch):
    # An agent sets this once and then uses the same commands a person uses.
    monkeypatch.setenv("SPECROUND_AUTHOR", "agent:reviewer")
    result = run("round", "open", doc, "--json")
    assert result.json["round"]["author"] == "agent:reviewer"


def test_with_no_name_at_all_the_cli_asks_for_one(run, doc, monkeypatch):
    monkeypatch.delenv("SPECROUND_AUTHOR", raising=False)
    monkeypatch.setattr(getpass, "getuser", lambda: (_ for _ in ()).throw(OSError("no user")))
    result = run("round", "open", doc)
    assert result.code == 2
    assert "--author" in result.err


def test_held_is_recorded_by_the_ledgers_name(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("dispose", doc, "--comment", comment, "--as", "held",
                 "--why", "waiting on the proxy team", "--author", "alice", "--json")
    # One vocabulary comes out, whichever word went in — the ledger's.
    assert result.json["disposition"]["verdict"] == "deferred"


def test_deferred_is_accepted_by_its_own_name(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("dispose", doc, "--comment", comment, "--as", "deferred",
                 "--why", "waiting", "--author", "alice", "--json")
    assert result.json["disposition"]["verdict"] == "deferred"


# -- the human surface ---------------------------------------------------


def test_the_table_shows_the_quote_and_the_state(run, doc, opened):
    comment = a_comment(run, doc)
    run("dispose", doc, "--comment", comment, "--as", "held", "--why", "later", "--author", "alice")
    lines = run("comments", doc).lines
    assert lines[0].split() == ["ID", "KIND", "STATE", "AUTHOR", "ANCHOR", "BODY"]
    assert comment in lines[1]
    assert "deferred" in lines[1]
    assert "30 seconds" in lines[1]


def test_an_empty_list_says_so_rather_than_printing_a_header(run, doc, opened):
    result = run("comments", doc)
    assert result.code == 0
    assert result.out.strip() == f"no comments on {doc.name}"


def test_orphans_are_reported_under_the_table(run, doc, opened):
    comment = a_comment(run, doc)
    doc.write_text(REWRITTEN, encoding="utf-8")
    run("reanchor", doc, "--author", "agent:reanchor")
    out = run("comments", doc).out
    assert "1 orphaned" in out
    assert comment in out


def test_the_body_can_come_from_stdin(run, doc, opened, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped in from an agent\n"))
    result = run("comment", doc, "--author", "agent:reviewer", "--body-file", "-", "--json")
    assert result.code == 0
    assert result.json["comment"]["body"] == "piped in from an agent"


def test_an_empty_stdin_body_is_a_usage_error(run, doc, opened, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("   \n"))
    assert run("comment", doc, "--author", "agent:reviewer", "--body-file", "-").code == 2


def test_the_body_can_come_from_a_file(run, doc, opened, tmp_path):
    note = tmp_path / "note.md"
    note.write_text("a longer review note\nover two lines\n", encoding="utf-8")
    result = run("comment", doc, "--author", "bob", "--body-file", note, "--json")
    assert result.json["comment"]["body"] == "a longer review note\nover two lines"


# -- pointing at a store by hand -----------------------------------------


def test_the_store_flag_overrides_the_resolved_location(run, doc, tmp_path):
    chosen = tmp_path / "review"
    result = run("round", "open", doc, "--author", "alice", "--store", chosen, "--json")
    assert result.code == 0
    assert result.json["store"] == str(chosen)
    assert (chosen / "ledger.jsonl").is_file()
    # And the default store stayed empty — the flag really decided.
    assert run("round", "status", doc, "--json").json["counts"]["rounds"] == 0
