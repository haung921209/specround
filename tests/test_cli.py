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
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from specround.cli import CLI_SCHEMA, main
from specround.imports import IMPORT_SCHEMA
from specround.store import ReviewStore
from specround.webview import WebView

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
        "undisposed": 2,
        "unresolved_threads": 2,
        "orphans": 0,
        "events": 3,
    }
    assert result.json["open"] == [opened]


def test_round_status_on_a_fresh_document_is_not_an_error(run, doc):
    result = run("round", "status", doc)
    assert result.code == 0
    assert "no rounds yet" in result.out


def test_resolving_a_thread_clears_the_thread_axis_and_not_the_other(run, doc, opened):
    """The report that named this item: resolve the talk, and the count stays.

    It was right to stay — the comment has no verdict, and ``round.close`` has to
    account for it (I6). What was wrong was calling it *unresolved*, one word off
    the verb that had just been run. So the two questions are counted apart and
    spelled apart: ``undisposed`` asks whether anyone decided, ``unresolved``
    asks whether the conversation is over.
    """
    comment = a_comment(run, doc)
    run("resolve", doc, "--comment", comment, "--author", "alice", "--note", "agreed in chat")

    payload = run("round", "status", doc, "--json").json
    assert payload["undisposed"] == [comment]  # no verdict — close must still declare it
    assert payload["unresolved_threads"] == []  # the conversation is over
    assert payload["counts"]["undisposed"] == 1
    assert payload["counts"]["unresolved_threads"] == 0


def test_disposing_a_comment_clears_the_other_axis_and_not_the_thread(run, doc, opened):
    """The mirror. Deciding is not the same as ending the conversation."""
    comment = a_comment(run, doc)
    run("dispose", doc, "--comment", comment, "--as", "applied", "--why", "raised to 60",
        "--author", "alice")

    payload = run("round", "status", doc, "--json").json
    assert payload["undisposed"] == []
    assert payload["unresolved_threads"] == [comment]
    assert payload["counts"]["undisposed"] == 0
    assert payload["counts"]["unresolved_threads"] == 1


def test_round_status_prints_both_axes_in_words_that_do_not_collide(run, doc, opened):
    """A reader who ran ``resolve`` must be able to see which number it moved."""
    resolved_only = a_comment(run, doc)
    a_comment(run, doc, quote=None, body="retry policy is missing")
    run("resolve", doc, "--comment", resolved_only, "--author", "alice")

    result = run("round", "status", doc)
    assert result.code == 0
    assert "2 undisposed" in result.out
    assert "1 unresolved thread(s)" in result.out
    # And per round, side by side, so the two columns say they are two questions.
    header = next(line for line in result.lines if "ROUND" in line)
    assert "UNDISPOSED" in header and "UNRESOLVED" in header


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


def test_comments_can_be_narrowed_to_the_undisposed(run, doc, opened):
    first = a_comment(run, doc)
    second = a_comment(run, doc, quote=None, body="retry policy is missing")
    run("dispose", doc, "--comment", first, "--as", "applied", "--why", "done", "--author", "alice")

    result = run("comments", doc, "--undisposed", "--json")
    assert [c["id"] for c in result.json["comments"]] == [second]


def test_the_old_spelling_of_the_disposition_filter_is_refused(run, doc, opened):
    """Not accepted quietly as an alias: the word moved axes.

    ``--unresolved`` used to select comments with no verdict. Keeping it alive
    would leave a flag whose name says one axis and whose behaviour is the
    other, and a caller cannot see that. Argparse refuses the unknown flag (2),
    which the caller can.
    """
    a_comment(run, doc)
    assert run("comments", doc, "--unresolved", "--json").code == 2


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


def an_import_file(tmp_path, *comments, source="cmux", name="in.json") -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps({"schema": IMPORT_SCHEMA, "source": source, "comments": list(comments)}),
        encoding="utf-8",
    )
    return path


def test_import_is_a_dry_run_until_apply(run, doc, opened, tmp_path):
    incoming = an_import_file(
        tmp_path, {"id": "ext-1", "body": "too short for the proxy", "quote": "30 seconds"}
    )
    result = run("import", doc, "--file", incoming, "--author", "agent:importer", "--json")
    assert result.code == 0
    assert result.json["applied"] is False
    assert result.json["imported"] == []
    assert [entry["source_id"] for entry in result.json["planned"]] == ["ext-1"]
    # The point of the dry run: the plan is readable and the ledger is untouched.
    assert run("comments", doc, "--json").json["comments"] == []


def test_import_records_the_comments_and_where_they_came_from(run, doc, opened, tmp_path):
    incoming = an_import_file(
        tmp_path,
        {
            "id": "ext-1",
            "body": "too short for the proxy",
            "quote": "30 seconds",
            "author": "bob",
            "ts": "2026-08-06T06:07:16Z",
        },
    )
    result = run(
        "import", doc, "--file", incoming, "--apply", "--author", "agent:importer", "--json"
    )
    assert result.code == 0
    assert result.json["applied"] is True
    assert result.json["counts"] == {"total": 1, "planned": 1, "skipped": 0, "rejected": 0}

    listed = run("comments", doc, "--json").json["comments"]
    assert len(listed) == 1
    assert listed[0]["author"] == "bob"
    assert listed[0]["anchor"]["exact"] == "30 seconds"
    assert listed[0]["ext"] == {
        "import": {"source": "cmux", "id": "ext-1", "ts": "2026-08-06T06:07:16Z"}
    }


def test_importing_the_same_file_twice_records_once(run, doc, opened, tmp_path):
    incoming = an_import_file(
        tmp_path, {"id": "ext-1", "body": "too short", "quote": "30 seconds"}
    )
    run("import", doc, "--file", incoming, "--apply", "--author", "agent:importer")
    again = run(
        "import", doc, "--file", incoming, "--apply", "--author", "agent:importer", "--json"
    )
    assert again.code == 0
    assert again.json["planned"] == []
    assert [entry["source_id"] for entry in again.json["skipped"]] == ["ext-1"]
    assert len(run("comments", doc, "--json").json["comments"]) == 1


def test_import_refuses_one_item_without_dropping_the_rest(run, doc, opened, tmp_path):
    incoming = an_import_file(
        tmp_path,
        {"id": "ext-1", "body": "lands", "quote": "30 seconds"},
        {"id": "ext-2", "body": "nowhere to go", "quote": "a line from another document"},
    )
    result = run(
        "import", doc, "--file", incoming, "--apply", "--author", "agent:importer", "--json"
    )
    # Exit 0 with the loss reported, the shape ``reanchor`` already has for a
    # comment it could not place — one moved paragraph is not a failed run.
    assert result.code == 0
    assert [entry["source_id"] for entry in result.json["imported"]] == ["ext-1"]
    assert result.json["rejected"][0]["source_id"] == "ext-2"
    assert "is not in the base" in result.json["rejected"][0]["reason"]
    assert len(run("comments", doc, "--json").json["comments"]) == 1


def test_import_prints_the_whole_reason_it_refused(run, doc, opened, tmp_path):
    incoming = an_import_file(
        tmp_path, {"id": "ext-1", "body": "b", "quote": "a line from another document"}
    )
    result = run("import", doc, "--file", incoming, "--author", "agent:importer")
    assert result.code == 0
    assert "nothing was guessed at" in result.out
    assert "is not in the base" in result.out


def test_import_reads_stdin(run, doc, opened, monkeypatch):
    payload = json.dumps(
        {
            "schema": IMPORT_SCHEMA,
            "source": "cmux",
            "comments": [{"id": "ext-1", "body": "b", "quote": "30 seconds"}],
        }
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    result = run("import", doc, "--file", "-", "--apply", "--author", "agent:importer", "--json")
    assert result.code == 0
    assert result.json["counts"]["planned"] == 1


def test_a_malformed_import_file_is_a_command_to_fix(run, doc, opened, tmp_path):
    path = tmp_path / "in.json"
    path.write_text('{"schema": "rdjson/v0", "source": "x", "comments": []}', encoding="utf-8")
    result = run("import", doc, "--file", path, "--author", "agent:importer", "--json")
    assert result.code == 2
    assert result.error["kind"] == "usage"


def test_importing_with_no_open_round_is_a_history_to_change(run, doc, tmp_path):
    incoming = an_import_file(tmp_path, {"id": "ext-1", "body": "b", "quote": "30 seconds"})
    result = run("import", doc, "--file", incoming, "--author", "agent:importer", "--json")
    assert result.code == 3
    assert result.error["kind"] == "state"


def test_dispose_settles_a_comment_with_its_reason(run, doc, opened):
    comment = a_comment(run, doc)
    result = run(
        "dispose", doc, "--comment", comment, "--as", "applied",
        "--why", "raised to 60 in revision 2", "--author", "alice", "--json",
    )
    assert result.code == 0
    assert result.json["disposition"]["verdict"] == "applied"
    assert result.json["disposition"]["reason"] == "raised to 60 in revision 2"
    assert result.json["comment"]["undisposed"] is False


def test_dispose_takes_a_prefix_of_the_id(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("dispose", doc, "--comment", comment[:6], "--as", "answered",
                 "--why", "the proxy caps at 45s", "--author", "alice", "--json")
    assert result.code == 0
    assert result.json["comment"]["id"] == comment


def test_round_close_records_what_it_left_open(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("round", "close", doc, "--author", "alice", "--allow-undisposed",
                 "--note", "retries move to round 2", "--json")
    assert result.code == 0
    assert result.json["undisposed"] == [comment]
    assert result.json["round"]["status"] == "closed"
    assert result.json["round"]["close_note"] == "retries move to round 2"


# -- threads: reply, resolve, reopen (G4 × G11) --------------------------


def test_a_person_and_an_agent_answer_through_the_same_verb(run, doc, opened):
    """G4 at the surface: one channel, two participants, one thread."""
    comment = a_comment(run, doc)
    first = run("reply", doc, "--comment", comment, "--author", "agent:reviewer",
                "--body", "the proxy caps it at 60")
    assert first.code == 0
    second = run("reply", doc, "--comment", comment, "--author", "bob",
                 "--body", "then say 60 in the spec", "--json")
    assert second.code == 0
    assert [r["author"] for r in second.json["comment"]["replies"]] == [
        "agent:reviewer",
        "bob",
    ]
    assert second.json["reply"]["body"] == "then say 60 in the spec"


def test_a_reply_can_be_addressed_by_an_id_prefix(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("reply", doc, "--comment", comment[:6], "--author", "alice", "--body", "ok", "--json")
    assert result.code == 0
    assert result.json["comment"]["id"] == comment


def test_a_reply_body_can_come_from_stdin(run, doc, opened, monkeypatch):
    comment = a_comment(run, doc)
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped in from an agent\n"))
    result = run("reply", doc, "--comment", comment, "--author", "agent:reviewer",
                 "--body-file", "-", "--json")
    assert result.json["reply"]["body"] == "piped in from an agent"


def test_a_reply_needs_a_body(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("reply", doc, "--comment", comment, "--author", "alice")
    assert result.code == 2
    assert "a reply needs a body" in result.err


def test_replying_to_a_resolved_thread_is_a_state_error(run, doc, opened):
    """3, not 2: the command is fine, the history is what refuses it."""
    comment = a_comment(run, doc)
    assert run("resolve", doc, "--comment", comment, "--author", "alice").code == 0
    result = run("reply", doc, "--comment", comment, "--author", "bob", "--body", "one more thing")
    assert result.code == 3
    assert "reopen" in result.err


def test_reopening_makes_the_reply_land(run, doc, opened):
    comment = a_comment(run, doc)
    run("resolve", doc, "--comment", comment, "--author", "alice")
    assert run("reopen", doc, "--comment", comment, "--author", "bob",
               "--why", "it came back in revision 3").code == 0
    result = run("reply", doc, "--comment", comment, "--author", "bob", "--body", "still wrong", "--json")
    assert result.code == 0
    assert [r["body"] for r in result.json["comment"]["replies"]] == ["still wrong"]


def test_resolving_records_who_and_which_kind(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("resolve", doc, "--comment", comment, "--author", "agent:reviewer",
                 "--actor", "agent", "--note", "applied upstream", "--json")
    assert result.code == 0
    assert result.json["resolved"] is True
    assert result.json["changed"] is True
    assert result.json["event"].startswith("v-")
    resolution = result.json["comment"]["resolutions"][-1]
    assert (resolution["author"], resolution["actor"]) == ("agent:reviewer", "agent")
    assert resolution["note"] == "applied upstream"


def test_the_actor_defaults_to_human_rather_than_reading_the_author(run, doc, opened):
    """``agent:`` in a name is a convention; the CLI does not promote it to a fact."""
    comment = a_comment(run, doc)
    result = run("resolve", doc, "--comment", comment, "--author", "agent:reviewer", "--json")
    assert result.json["comment"]["resolutions"][-1]["actor"] == "human"


def test_the_environment_can_name_the_actor(run, doc, opened, monkeypatch):
    monkeypatch.setenv("SPECROUND_ACTOR", "agent")
    comment = a_comment(run, doc)
    result = run("resolve", doc, "--comment", comment, "--author", "agent:reviewer", "--json")
    assert result.json["comment"]["resolutions"][-1]["actor"] == "agent"


def test_a_bad_actor_in_the_environment_is_a_usage_error(run, doc, opened, monkeypatch):
    monkeypatch.setenv("SPECROUND_ACTOR", "robot")
    comment = a_comment(run, doc)
    result = run("resolve", doc, "--comment", comment, "--author", "alice")
    assert result.code == 2
    assert "unknown actor" in result.err


def test_resolving_an_already_resolved_thread_succeeds_and_says_nothing_changed(run, doc, opened):
    """I10 reaching the shell: a retry is safe, so an agent can just retry."""
    comment = a_comment(run, doc)
    run("resolve", doc, "--comment", comment, "--author", "alice")
    result = run("resolve", doc, "--comment", comment, "--author", "carol", "--json")
    assert result.code == 0
    assert result.json["changed"] is False
    assert result.json["event"] is None
    assert result.json["resolved"] is True
    assert len(result.json["comment"]["resolutions"]) == 1

    human = run("resolve", doc, "--comment", comment, "--author", "carol")
    assert "already resolved" in human.out


def test_reopening_an_open_thread_succeeds_and_says_nothing_changed(run, doc, opened):
    comment = a_comment(run, doc)
    result = run("reopen", doc, "--comment", comment, "--author", "alice",
                 "--why", "was never closed", "--json")
    assert result.code == 0
    assert result.json["changed"] is False
    assert result.json["resolved"] is False


def test_reopening_requires_a_reason(run, doc, opened):
    comment = a_comment(run, doc)
    run("resolve", doc, "--comment", comment, "--author", "alice")
    assert run("reopen", doc, "--comment", comment, "--author", "bob").code == 2


def test_resolving_leaves_the_disposition_axis_alone(run, doc, opened):
    """Closing the talk is not deciding the comment — and round.close still counts it."""
    comment = a_comment(run, doc)
    run("resolve", doc, "--comment", comment, "--author", "alice", "--note", "agreed in chat")
    status = run("round", "status", doc, "--json").json
    assert status["undisposed"] == [comment]
    assert run("round", "close", doc, "--author", "alice").code == 3


def test_a_thread_can_be_closed_after_its_round(run, doc, opened):
    comment = a_comment(run, doc)
    run("dispose", doc, "--comment", comment, "--as", "answered", "--why", "see the reply",
        "--author", "alice")
    assert run("round", "close", doc, "--author", "alice").code == 0
    assert run("resolve", doc, "--comment", comment, "--author", "alice").code == 0


def test_an_unknown_thread_id_is_a_usage_error(run, doc, opened):
    a_comment(run, doc)
    assert run("resolve", doc, "--comment", "c-nope", "--author", "alice").code == 2
    assert run("reply", doc, "--comment", "c-nope", "--author", "alice", "--body", "x").code == 2


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


def test_closing_over_undisposed_comments_is_a_state_error(run, doc, opened):
    a_comment(run, doc)
    result = run("round", "close", doc, "--author", "alice")
    assert result.code == 3
    # The message names the flag this surface has, not the library keyword.
    assert "--allow-undisposed" in result.err
    # And it says which axis it means, because the reader who lands here has
    # often just resolved the thread and is asking why that did not count.
    assert "Resolving the thread does not count" in result.err


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
    run("reply", doc, "--comment", comment, "--body", "the proxy caps it", "--author", "alice")
    run("resolve", doc, "--comment", comment, "--author", "alice", "--note", "settled")
    payload = run("comments", doc, "--all", "--json").json["comments"][0]
    assert set(payload) == {
        "ambiguous",
        "anchor",
        "anchoring",
        "anchorings",
        "author",
        "body",
        "current_anchor",
        "dispositions",
        "ext",
        "id",
        "kind",
        "orphaned",
        "patch",
        "replies",
        "resolutions",
        "resolved",
        "round",
        "state",
        "strategy",
        "ts",
        "undisposed",
    }
    # The thread axis is `resolved` and only `resolved`. A consumer reaching for
    # the old key gets nothing back rather than a boolean that changed meaning.
    assert "unresolved" not in payload
    assert set(payload["dispositions"][0]) == {"author", "id", "reason", "ts", "verdict"}
    assert set(payload["replies"][0]) == {"author", "body", "id", "ts"}
    assert set(payload["resolutions"][0]) == {
        "actor", "author", "id", "note", "resolved", "ts",
    }


def test_the_comments_payload_says_which_view_it_is_and_what_it_left_out(run, doc, opened):
    closed = a_comment(run, doc)
    a_comment(run, doc, quote=None, body="retries are missing")
    run("resolve", doc, "--comment", closed, "--author", "alice")

    default = run("comments", doc, "--json").json
    assert set(default) == {
        "comments", "doc", "hidden", "include_resolved", "path", "schema", "store", "verb",
    }
    assert default["include_resolved"] is False
    assert default["hidden"] == [closed]
    assert closed not in [c["id"] for c in default["comments"]]

    every = run("comments", doc, "--all", "--json").json
    assert every["include_resolved"] is True
    assert every["hidden"] == []
    assert closed in [c["id"] for c in every["comments"]]


def test_the_json_listing_nests_replies_under_their_thread(run, doc, opened):
    first = a_comment(run, doc)
    second = a_comment(run, doc, quote=None, body="retries are missing")
    run("reply", doc, "--comment", first, "--author", "alice", "--body", "the proxy caps it")
    threads = {c["id"]: c for c in run("comments", doc, "--json").json["comments"]}
    # Nested, not a flat list a consumer would have to re-associate by target.
    assert [r["body"] for r in threads[first]["replies"]] == ["the proxy caps it"]
    assert threads[second]["replies"] == []


def test_the_thread_payload_field_set_is_closed(run, doc, opened):
    comment = a_comment(run, doc)
    for argv in (
        ("resolve", doc, "--comment", comment, "--author", "alice"),
        ("reopen", doc, "--comment", comment, "--author", "alice", "--why", "again"),
    ):
        payload = run(*argv, "--json").json
        assert set(payload) == {
            "changed", "comment", "doc", "event", "path", "resolved", "schema", "store", "verb",
        }


def test_the_reply_payload_field_set_is_closed(run, doc, opened):
    comment = a_comment(run, doc)
    payload = run("reply", doc, "--comment", comment, "--author", "alice",
                  "--body", "because of the proxy", "--json").json
    assert set(payload) == {"comment", "doc", "path", "reply", "schema", "store", "verb"}
    assert payload["reply"]["id"].startswith("p-")


def test_the_anchoring_object_field_set_is_closed(run, doc, opened):
    a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")
    run("reanchor", doc, "--author", "agent:reanchor")
    payload = run("comments", doc, "--json").json["comments"][0]["anchorings"][0]
    assert set(payload) == {
        "ambiguous",
        "orphaned",
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
        "ext",
        "id",
        "status",
        "title",
        "ts",
        "undisposed_at_close",
        "undisposed_count",
        "unresolved_thread_count",
    }


def test_the_status_payload_field_set_is_closed(run, doc, opened):
    payload = run("round", "status", doc, "--json").json
    assert set(payload) == {
        "counts", "doc", "open", "orphans", "path", "rounds", "schema", "store",
        "undisposed", "unresolved_threads", "verb",
    }
    assert set(payload["counts"]) == {
        "comments", "events", "orphans", "rounds", "undisposed", "unresolved_threads",
    }
    # Never the bare word on its own: `unresolved` alone was the disposition
    # axis, and a key that kept the spelling while changing the question is the
    # one failure a consumer cannot detect.
    assert "unresolved" not in payload and "unresolved" not in payload["counts"]


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


def test_the_table_indents_replies_under_their_thread(run, doc, opened):
    comment = a_comment(run, doc)
    run("reply", doc, "--comment", comment, "--author", "alice", "--body", "the proxy caps it")
    run("reply", doc, "--comment", comment, "--author", "bob", "--body", "then say so")
    lines = run("comments", doc).lines
    assert lines[1].startswith(comment)  # the root is flush left
    assert lines[2].startswith(" ") and lines[3].startswith(" ")

    # A reply belongs to the thread above it: no state and no anchor of its own,
    # so those columns are empty and the row reads as part of the one before it.
    marker, reply_id, kind, author, *body = lines[2].split()
    assert (marker, kind, author) == ("└", "reply", "alice")
    assert reply_id.startswith("p-")
    assert " ".join(body) == "the proxy caps it"
    assert lines[3].split()[3] == "bob"


def test_resolved_threads_are_hidden_from_the_default_listing(run, doc, opened):
    closed = a_comment(run, doc)
    living = a_comment(run, doc, quote=None, body="retries are missing")
    run("resolve", doc, "--comment", closed, "--author", "alice")
    out = run("comments", doc).out
    assert living in out
    assert closed not in out
    # Hidden, and the view says so — silence would read as "there is nothing else".
    assert "1 resolved and hidden" in out


def test_the_all_flag_brings_resolved_threads_back(run, doc, opened):
    closed = a_comment(run, doc)
    a_comment(run, doc, quote=None, body="retries are missing")
    run("resolve", doc, "--comment", closed, "--author", "alice")
    out = run("comments", doc, "--all").out
    assert closed in out
    assert f"1 resolved: {closed}" in out


def test_a_listing_with_nothing_but_resolved_threads_does_not_claim_emptiness(run, doc, opened):
    comment = a_comment(run, doc)
    run("resolve", doc, "--comment", comment, "--author", "alice")
    out = run("comments", doc).out
    assert "no open threads" in out and "1 resolved" in out
    assert "no comments" not in out


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


# -- the file layer ------------------------------------------------------


def test_a_stripped_final_newline_does_not_kill_the_ledger(run, doc, opened):
    """The reviewer's scenario, end to end: an editor takes the last newline.

    Before the fix the next comment landed on the end of that line and every
    later verb exited 1 on a physical line holding two records — a ledger the
    tool had no way to repair.
    """
    a_comment(run, doc, body="first")
    store = ReviewStore.for_document(doc)
    ledger = store.ledger.path
    ledger.write_bytes(ledger.read_bytes().rstrip(b"\n"))
    assert run("round", "status", doc).code == 0

    assert run("comment", doc, "--author", "bob", "--body", "second").code == 0

    result = run("round", "status", doc, "--json")
    assert result.code == 0
    assert result.json["counts"]["comments"] == 2


def test_a_reordered_ledger_is_a_state_failure_not_a_crash(run, doc, opened):
    """One rule, one exit code.

    ``seq`` disagreeing with its position was checked twice — once while
    reading (exit 1) and once while folding (exit 3) — so which code a caller
    got depended on which path reached the file first. It is an invariant (I2),
    and every invariant the recorded history refuses is a 3.
    """
    a_comment(run, doc, body="first")
    ledger = ReviewStore.for_document(doc).ledger.path
    lines = ledger.read_text(encoding="utf-8").splitlines()
    ledger.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    result = run("round", "status", doc, "--json")
    assert result.code == 3
    assert result.error["kind"] == "state"
    assert "reordered or truncated" in result.error["message"]


def test_naming_a_store_by_path_sees_the_history_it_holds(run, tmp_path):
    """The reviewer's scenario: ``--store`` used to guess past the origin.

    The store already recorded what it serves. Guessing its parent instead
    keyed the same document a second way, reported "no rounds yet" as a fact,
    and then opened a second round in the same ledger — one document, two
    histories, in one file.
    """
    repo = tmp_path / "repo"
    (repo / "sub" / "docs").mkdir(parents=True)
    (repo / ".specround.json").write_text(
        '{"store": {"mode": "path", "path": "sub/store"}}\n', encoding="utf-8"
    )
    doc = repo / "sub" / "docs" / "s.md"
    doc.write_text("# S\n\nalpha beta\n", encoding="utf-8")
    assert run("round", "open", doc, "--author", "alice", "--title", "via config").code == 0
    store = repo / "sub" / "store"

    status = run("round", "status", doc, "--store", store, "--json")
    assert status.code == 0
    assert status.json["counts"]["rounds"] == 1
    assert status.json["doc"] == "sub/docs/s.md"

    # And the CLI's one-open-round rule is not routed around by the flag.
    second = run("round", "open", doc, "--author", "alice", "--store", store, "--title", "via flag")
    assert second.code == 3


def test_a_case_only_difference_does_not_report_an_empty_history(run, tmp_path):
    """The failure ``_document`` was written to prevent, arriving by case."""
    doc = tmp_path / "real.md"
    doc.write_text("# L\n\nlinked content\n", encoding="utf-8")
    other = tmp_path / "Real.md"
    if not other.is_file():
        pytest.skip("case-sensitive filesystem: the two spellings are two documents")
    assert run("round", "open", doc, "--author", "alice", "--title", "via real").code == 0

    result = run("round", "status", other, "--json")
    assert result.code == 0
    assert result.json["counts"]["rounds"] == 1


# -- a document that is no longer there ----------------------------------


def test_the_history_of_a_moved_document_is_still_readable(run, doc, opened, tmp_path):
    """The store outlives the file, and the CLI is the way in (G7).

    Renaming the document made ``specround comments <old path>`` exit 2 while
    the ledger sat there intact — the format says the old history stays in the
    old store and ``origin`` keeps naming it, and that was true only for
    ``cat``.
    """
    comment_id = a_comment(run, doc, body="outlives the file")
    doc.rename(tmp_path / "renamed.md")

    listed = run("comments", doc, "--json")
    assert listed.code == 0
    assert [c["id"] for c in listed.json["comments"]] == [comment_id]
    assert run("round", "status", doc, "--json").json["counts"]["comments"] == 1


def test_a_mistyped_path_is_still_refused(run, tmp_path):
    """The reason the check was there in the first place stays covered.

    A path with no history behind it is a typo, and answering "no comments"
    would be a wrong answer that looks like a fact. Only a path the store
    already knows is addressable once the file is gone.
    """
    result = run("comments", tmp_path / "nope.md")
    assert result.code == 2
    assert "no history" in result.err


def test_a_writing_verb_still_needs_the_document(run, doc, opened, tmp_path):
    """Reading history is one thing; commenting on text that is gone is not."""
    doc.rename(tmp_path / "renamed.md")
    assert run("comment", doc, "--author", "bob", "--body", "on what?").code == 2


# -- the invocation axis -------------------------------------------------


def test_an_occurrence_without_a_quote_is_refused(run, doc, opened):
    """Every other argument mistake is a 2; this one used to pass silently.

    ``--occurrence`` picks between appearances of ``--quote``. Without one it
    was dropped on the floor and the comment landed on the whole document —
    exit 0, and a caller who thought they had anchored something.
    """
    result = run("comment", doc, "--author", "bob", "--body", "which one?", "--occurrence", "1")
    assert result.code == 2
    assert "--quote" in result.err


def test_an_argument_error_is_structured_when_json_was_asked_for(run, doc):
    """G4's structured output cannot stop at the argument parser.

    An agent that gets plain usage text on stderr where every other failure is
    a JSON envelope has to parse two shapes to find out what went wrong.
    """
    result = run("comment", doc, "--occurrence", "not-a-number", "--json")
    assert result.code == 2
    assert result.error == {
        "kind": "usage",
        "exit": 2,
        "message": "argument --occurrence: invalid int value: 'not-a-number'",
    }
    # The verb is known here — the subparser is the one that refused.
    assert json.loads(result.err)["verb"] == "comment"


def test_an_argument_error_before_a_verb_says_so_rather_than_guessing(run):
    result = run("--nonexistent-flag", "--json")
    assert result.code == 2
    assert json.loads(result.err)["verb"] is None
    assert result.error["kind"] == "usage"


def test_an_argument_error_without_json_still_reads_like_argparse(run, doc):
    result = run("comment", doc, "--occurrence", "not-a-number")
    assert result.code == 2
    assert result.err.startswith("usage: specround comment")
    assert "invalid int value" in result.err


# -- what the listing carries --------------------------------------------


def test_ext_written_by_an_agent_can_be_read_back(run, doc):
    """``ext`` is preserved on disk and was invisible to every verb.

    The field exists so an agent can carry data the schema has no place for
    yet. One that can write it and not read it back has to parse the ledger by
    hand, which is the thing the CLI is for (G4).
    """
    store = ReviewStore.for_document(doc)
    round_id = store.open_round(doc, author="alice", ext={"harness": "probe-7"})
    store.add_comment(round_id, author="agent:reviewer", body="a note", ext={"confidence": "low"})

    assert run("round", "status", doc, "--json").json["rounds"][0]["ext"] == {"harness": "probe-7"}
    assert run("comments", doc, "--json").json["comments"][0]["ext"] == {"confidence": "low"}


def test_a_record_without_ext_says_so_rather_than_omitting_the_key(run, doc, opened):
    a_comment(run, doc)
    assert run("comments", doc, "--json").json["comments"][0]["ext"] is None
    assert run("round", "status", doc, "--json").json["rounds"][0]["ext"] is None


def test_the_listing_carries_the_re_anchoring_that_moved_a_comment(run, doc, opened):
    """Which comments moved, and how, outlived the pass that moved them.

    ``reanchor`` reported ``strategy`` and ``ambiguous`` in the moment and
    nowhere else, so a reviewer reading the list later could not tell a comment
    whose sentence was rewritten under it — the one a person is supposed to
    look at — from one that was merely pushed down the page.
    """
    comment = a_comment(run, doc)
    doc.write_text(REVISED, encoding="utf-8")
    moved = run("reanchor", doc, "--author", "agent:reanchor", "--json").json

    payload = run("comments", doc, "--json").json["comments"][0]
    assert payload["anchoring"]["strategy"] == moved["strategies"][comment]
    assert payload["anchoring"]["base"] == moved["base"]
    assert payload["anchoring"]["orphaned"] is False
    assert payload["anchoring"]["ambiguous"] is False


def test_an_orphan_carries_the_reason_it_could_not_be_placed(run, doc, opened):
    a_comment(run, doc)
    doc.write_text(REWRITTEN, encoding="utf-8")
    run("reanchor", doc, "--author", "agent:reanchor")

    anchoring = run("comments", doc, "--json").json["comments"][0]["anchoring"]
    assert anchoring["orphaned"] is True
    assert anchoring["reason"]
    assert anchoring["strategy"] is None


def test_a_comment_that_never_moved_has_no_anchoring(run, doc, opened):
    a_comment(run, doc)
    assert run("comments", doc, "--json").json["comments"][0]["anchoring"] is None


def test_the_table_names_the_comments_a_person_should_look_at(run, doc, opened):
    """The human listing keeps the signal, not the whole re-anchoring log.

    ``fuzzy`` and ``ambiguous`` are the two the format says a person has to
    check; a rebind that merely followed its quote is not news, and a footer
    listing every move would bury the two that matter.
    """
    comment = a_comment(run, doc, quote="The client sends a hello frame", body="which frame?")
    doc.write_text(
        "# Widget protocol\n\n"
        "The client sends a hell0 frame. The server answers with a hello frame.\n\n"
        "Timeouts are 30 seconds. Retries are not specified yet.\n",
        encoding="utf-8",
    )
    moved = run("reanchor", doc, "--author", "agent:reanchor", "--json").json
    assert moved["strategies"].get(comment) == "fuzzy", moved

    listing = run("comments", doc)
    assert listing.code == 0
    assert "1 moved on rewritten text — worth a look" in listing.out
    assert comment in listing.out.splitlines()[-1]


def test_a_negative_occurrence_is_refused_by_the_one_rule_that_owns_it(run, doc, opened):
    """The anchor layer already says occurrences count from 0 — no second copy."""
    result = run(
        "comment", doc, "--author", "bob", "--body", "which one?",
        "--quote", "30 seconds", "--occurrence", "-1",
    )
    assert result.code == 2
    assert "negative" in result.err


# -- the view verb -------------------------------------------------------
#
# The routes have their own tests over a real socket (test_webview.py). What is
# under test here is the shell contract: the URL is the first line of stdout, the
# payload names what an embedder needs, and nothing opens a browser uninvited.


@pytest.fixture
def served(monkeypatch):
    """Keep ``view`` from blocking, and record that it would have served.

    The sockets get closed on the way out. ``serve_forever`` releases the port in
    its own ``finally``, so standing in for it means standing in for that too — a
    test that left a listener behind would walk off with a port.
    """
    urls: list[str] = []
    views: list[WebView] = []

    def record(self) -> None:
        urls.append(self.url)
        views.append(self)

    monkeypatch.setattr(WebView, "serve_forever", record)
    yield urls
    for view in views:
        view.shutdown()


def test_view_prints_the_url_as_the_first_line_and_then_serves(run, doc, opened, served):
    """An embedder reads one line. It has to be the first one, and only the URL."""
    result = run("view", doc, "--author", "alice")
    assert result.code == 0
    assert result.lines[0].startswith("http://127.0.0.1:")
    assert served == [result.lines[0]]
    assert opened in result.out


def test_view_json_names_what_an_embedder_needs(run, doc, opened, served):
    result = run("view", doc, "--author", "alice", "--json")
    assert result.code == 0
    payload = result.json
    assert set(payload) == {
        "schema", "verb", "doc", "path", "store",
        "url", "host", "port", "token", "round", "commentable", "blocked",
    }
    assert payload["schema"] == CLI_SCHEMA
    assert payload["verb"] == "view"
    assert payload["port"] > 0
    assert payload["token"] and payload["token"] in payload["url"]
    assert payload["round"]["id"] == opened
    assert payload["commentable"] is True
    assert payload["blocked"] is None
    assert served == [payload["url"]]


def test_view_does_not_open_a_browser(run, doc, opened, served, monkeypatch):
    """Off by default: the URL goes to a pane, not to whatever `open` decides."""
    opens: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opens.append(url))
    assert run("view", doc, "--author", "alice").code == 0
    assert opens == []


def test_view_open_is_the_opt_in(run, doc, opened, served, monkeypatch):
    opens: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opens.append(url))
    result = run("view", doc, "--author", "alice", "--open")
    assert result.code == 0
    assert opens == [result.lines[0]]


def test_view_serves_a_document_with_no_round_and_says_what_to_do(run, doc, served):
    """A view is a reader first: nothing to write is not nothing to show."""
    result = run("view", doc, "--author", "alice", "--json")
    assert result.code == 0
    assert result.json["round"] is None
    assert result.json["commentable"] is False
    assert "specround round open" in result.json["blocked"]
    assert served


def test_view_takes_the_round_it_was_given(run, doc, opened, served):
    result = run("view", doc, "--author", "alice", "--round", opened, "--json")
    assert result.code == 0
    assert result.json["round"]["id"] == opened


def test_view_refuses_a_round_that_is_not_there(run, doc, opened, served):
    """The one ``2`` here: what was asked for does not exist."""
    result = run("view", doc, "--author", "alice", "--round", "r-000000000000")
    assert result.code == 2
    assert "r-000000000000" in result.err
    assert served == []


def test_view_on_a_closed_round_is_read_only_rather_than_refused(run, doc, opened, served):
    assert run("round", "close", doc, "--author", "alice").code == 0
    result = run("view", doc, "--author", "alice", "--json")
    assert result.code == 0
    assert result.json["commentable"] is False
    assert "reading only" in result.json["blocked"]


def test_view_pins_the_port_when_told_to(run, doc, opened, served):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    result = run("view", doc, "--author", "alice", "--port", free, "--json")
    assert result.code == 0
    assert result.json["port"] == free


# -- the view verb over a directory (H15) --------------------------------
#
# The tree's own behaviour is tested in test_workspace.py, over a socket. What
# is under test here is the shell contract again: one server, the URL still
# first and alone, and the two refusals that keep a directory from being served
# as something it is not.


@pytest.fixture
def tree(tmp_path, doc):
    """The fixture document, with two more beside it and one in a folder."""
    (tmp_path / "second.md").write_text("# Second\n\nAnother document.\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "third.md").write_text("# Third\n\nOne more.\n", encoding="utf-8")
    return tmp_path


def test_view_on_a_directory_serves_the_tree_from_one_server(run, tree, opened, served):
    """H15's whole promise: a spec is never one file, and this is one process."""
    result = run("view", str(tree), "--author", "alice", "--json")
    assert result.code == 0
    payload = result.json
    assert set(payload) == {
        "schema", "verb", "doc", "path", "store", "root", "url", "host", "port",
        "token", "workspace",
    }
    assert payload["root"] == str(tree)
    assert [d["key"] for d in payload["workspace"]["documents"]] == [
        "second.md", "spec.md", "sub/third.md"
    ]
    assert served == [payload["url"]]


def test_view_on_a_directory_still_prints_the_url_first_and_alone(run, tree, opened, served):
    """An embedder reads one line and does not learn a second shape from the argument."""
    result = run("view", str(tree), "--author", "alice")
    assert result.lines[0].startswith("http://127.0.0.1:")
    assert served == [result.lines[0]]
    assert "3 document(s) under" in result.out


def test_view_on_a_directory_opens_the_first_document_in_path_order(run, tree, opened, served):
    """Not the most recently active one: nothing here may rank by a timestamp."""
    payload = run("view", str(tree), "--author", "alice", "--json").json
    assert payload["workspace"]["selected"] == "second.md"
    assert payload["path"].endswith("second.md")


def test_view_on_a_directory_carries_the_badges_the_bar_shows(run, tree, opened, served):
    payload = run("view", str(tree), "--author", "alice", "--json").json
    listed = {d["key"]: d for d in payload["workspace"]["documents"]}
    assert listed["spec.md"]["active"] and listed["spec.md"]["open_rounds"] == 1
    assert not listed["second.md"]["active"]
    assert payload["workspace"]["counts"]["active"] == 1


def test_view_refuses_a_directory_with_nothing_to_review(run, tmp_path, served):
    """A tree with no markdown in it has nothing to serve — a ``2``, not an empty page."""
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("not a document\n", encoding="utf-8")
    result = run("view", str(empty), "--author", "alice")
    assert result.code == 2
    assert "no markdown documents" in result.err
    assert served == []


def test_view_refuses_round_on_a_directory(run, tree, opened, served):
    """A round belongs to one document; honouring the flag would pick one silently."""
    result = run("view", str(tree), "--author", "alice", "--round", opened)
    assert result.code == 2
    assert "belongs to one document" in result.err
    assert served == []


def test_view_on_a_directory_says_it_is_a_store_per_document(run, tree, opened, served):
    """The default layout gives each document its own; naming one would name the wrong one."""
    result = run("view", str(tree), "--author", "alice")
    assert "stores 3 — one per document" in result.out


def test_view_stopping_with_ctrl_c_is_a_clean_exit(run, doc, opened, monkeypatch):
    """Ctrl-c is how this verb ends. It is not a failure."""
    def interrupt(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(WebView, "serve_forever", interrupt)
    result = run("view", doc, "--author", "alice")
    assert result.code == 0
    assert "stopped" in result.err


def test_the_all_listing_marks_resolved_threads_in_their_rows(run, doc, opened):
    """A resolved thread shown by --all must not look like an open one.

    The report that forced the two-axis split (2026-08-08) hit this table:
    seventeen rows, some resolved, none marked — the reader had no way to see
    which conversations were over. The THREAD column appears exactly when the
    listing contains a resolved thread, so the default view (which hides them)
    keeps its width.
    """
    settled = a_comment(run, doc, quote="client sends")
    still_open = a_comment(run, doc, quote="30 seconds")
    run("resolve", doc, "--comment", settled, "--author", "alice", "--note", "done")

    every = run("comments", doc, "--all").out.splitlines()
    header = next(line for line in every if line.startswith("ID"))
    assert "THREAD" in header
    settled_row = next(line for line in every if line.startswith(settled))
    still_open_row = next(line for line in every if line.startswith(still_open))
    assert "resolved" in settled_row
    assert "resolved" not in still_open_row

    live_only = run("comments", doc).out.splitlines()
    live_header = next(line for line in live_only if line.startswith("ID"))
    assert "THREAD" not in live_header
