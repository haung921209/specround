"""Zero git (G5, G10).

The point of the tool owning its own files is that review does not wait on a
repository — and does not leave one anything to clean up. These tests assert it
structurally (the package imports no process-spawning module at all) and
behaviourally: the full loop runs with every subprocess entry point
booby-trapped, in a directory with no repository in sight, and the document's
folder is exactly as it was afterwards.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import specround
from specround.locations import STORE_DIRNAME, central_store_dir
from specround.store import ReviewStore

PACKAGE_DIR = Path(specround.__file__).parent

DOC = """# Widget protocol

Timeouts are 30 seconds. Retries are not specified yet.
"""

#: Modules that could reach a git binary, directly or by wrapping one.
FORBIDDEN_IMPORTS = {
    "subprocess",
    "os.system",
    "commands",
    "pty",
    "sh",
    "git",
    "pygit2",
    "dulwich",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_module_in_the_package_imports_a_process_spawner():
    offenders = {}
    for source in sorted(PACKAGE_DIR.rglob("*.py")):
        found = _imported_modules(source) & FORBIDDEN_IMPORTS
        if found:
            offenders[source.name] = sorted(found)
    assert offenders == {}


def test_the_import_detector_actually_bites():
    # A guard that cannot fail is not a guard: this file imports subprocess, so
    # the detector must see it here while seeing nothing in the package.
    assert _imported_modules(Path(__file__)) & FORBIDDEN_IMPORTS == {"subprocess"}
    assert PACKAGE_DIR.is_dir() and list(PACKAGE_DIR.rglob("*.py"))


def test_the_package_declares_no_dependencies():
    # A git dependency cannot sneak in through a third party either.
    pyproject = PACKAGE_DIR.parents[1] / "pyproject.toml"
    if not pyproject.is_file():  # installed without the sdist layout
        pytest.skip("pyproject.toml is not next to the installed package")
    text = pyproject.read_text(encoding="utf-8")
    assert "dependencies = []" in text


@pytest.fixture
def no_subprocess(monkeypatch):
    """Make any attempt to spawn a process fail loudly."""

    def forbidden(*args, **kwargs):
        raise AssertionError(f"the ledger spawned a process: {args!r}")

    for name in (
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    ):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)
    if hasattr(os, "posix_spawn"):
        monkeypatch.setattr(os, "posix_spawn", forbidden)
    if hasattr(os, "fork"):
        monkeypatch.setattr(os, "fork", forbidden)
    return forbidden


@pytest.fixture
def hostile_path(tmp_path, monkeypatch):
    """A PATH whose only ``git`` records the fact that it was called."""
    bin_dir = tmp_path / "hostile-bin"
    bin_dir.mkdir()
    marker = tmp_path / "git-was-called"
    shim = bin_dir / "git"
    shim.write_text(f'#!/bin/sh\necho called >> "{marker}"\nexit 1\n', encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    return marker


def test_the_subprocess_trap_is_armed(no_subprocess, hostile_path):
    # Same reason: prove the booby trap fires, so the loop tests below mean
    # "nothing spawned" rather than "nothing was watching".
    with pytest.raises(AssertionError, match="spawned a process"):
        subprocess.run(["git", "status"])
    with pytest.raises(AssertionError, match="spawned a process"):
        os.system("git status")
    assert not hostile_path.exists()


def run_full_loop(doc: Path, clock) -> ReviewStore:
    """Open a round, comment, suggest, reply, dispose, close."""
    store = ReviewStore.for_document(doc, clock=clock)
    round_id = store.open_round(doc, author="alice", title="round one")

    anchor = store.anchor_in_round(round_id, "30 seconds")
    comment_id = store.add_comment(round_id, author="bob", body="too short", anchor=anchor)
    suggestion_id = store.add_suggestion(
        round_id,
        author="agent:reviewer",
        patch="-Timeouts are 30 seconds.\n+Timeouts are 60 seconds.\n",
        anchor=anchor,
    )
    store.reply(comment_id, author="alice", body="agreed")
    store.dispose(comment_id, author="alice", verdict="applied", reason="raised to 60")
    store.dispose(suggestion_id, author="alice", verdict="applied", reason="patch taken")
    store.close_round(round_id, author="alice")
    return store


def assert_loop_landed(store: ReviewStore, doc: Path) -> None:
    state = store.fold()
    assert len(state.rounds) == 1
    assert len(state.comments) == 2
    assert state.open_rounds == []
    assert state.unresolved == []
    assert {c.state for c in state.comments.values()} == {"applied"}
    round_ = next(iter(state.rounds.values()))
    assert store.snapshots.get_text(round_.base) == DOC
    # Everything the loop produced lives in the central store, and the document's
    # folder holds exactly what it held before: the document.
    assert (central_store_dir(doc) / "ledger.jsonl").is_file()
    assert [p.name for p in doc.parent.iterdir()] == [doc.name]


def test_full_loop_in_a_directory_with_no_repository(tmp_path, clock, no_subprocess, hostile_path):
    workdir = tmp_path / "plain"
    workdir.mkdir()
    doc = workdir / "spec.md"
    doc.write_text(DOC, encoding="utf-8")
    # No .git here, none in any parent inside tmp_path.
    assert not any(p.name == ".git" for p in tmp_path.rglob("*"))

    store = run_full_loop(doc, clock)
    assert_loop_landed(store, doc)
    assert not hostile_path.exists(), "a git binary was invoked"


def test_full_loop_beside_an_untracked_document_in_a_repository(
    tmp_path, clock, no_subprocess, hostile_path
):
    """A repository being present changes nothing — the document need not be tracked."""
    workdir = tmp_path / "repo"
    (workdir / ".git").mkdir(parents=True)
    doc = workdir / "spec.md"
    doc.write_text(DOC, encoding="utf-8")

    store = run_full_loop(doc, clock)
    assert_loop_landed_in_repo(store, doc)
    assert not hostile_path.exists(), "a git binary was invoked"


def assert_loop_landed_in_repo(store: ReviewStore, doc: Path) -> None:
    state = store.fold()
    assert len(state.comments) == 2
    assert state.unresolved == []
    assert (central_store_dir(doc) / "ledger.jsonl").is_file()
    # The working tree gained nothing to gitignore — this is the whole reason the
    # default moved out of the document's folder.
    assert sorted(p.name for p in doc.parent.iterdir()) == [".git", doc.name]


def test_an_in_tree_store_is_still_git_free(tmp_path, clock, no_subprocess, hostile_path):
    """Opting the store back into the repository does not opt into git."""
    workdir = tmp_path / "repo"
    (workdir / ".git").mkdir(parents=True)
    doc = workdir / "spec.md"
    doc.write_text(DOC, encoding="utf-8")
    (workdir / ".specround.json").write_text('{"store": {"mode": "beside"}}', encoding="utf-8")

    store = run_full_loop(doc, clock)
    assert (workdir / STORE_DIRNAME / "ledger.jsonl").is_file()
    assert store.fold().unresolved == []
    assert not hostile_path.exists(), "a git binary was invoked"


def test_reading_history_back_needs_nothing_but_the_files(tmp_path, clock, no_subprocess):
    workdir = tmp_path / "plain"
    workdir.mkdir()
    doc = workdir / "spec.md"
    doc.write_text(DOC, encoding="utf-8")
    run_full_loop(doc, clock)

    # A second process, the document's path, no clock, no repository.
    state = ReviewStore.for_document(doc).fold()
    assert len(state.rounds) == 1
    assert len(state.comments) == 2
    assert [c.state for c in state.comments.values()] == ["applied", "applied"]
    # And from the store directory alone, which is all a listing would have.
    assert ReviewStore.open(central_store_dir(doc)).fold() == state


def test_the_ledger_is_readable_as_plain_text(tmp_path, clock, no_subprocess):
    workdir = tmp_path / "plain"
    workdir.mkdir()
    doc = workdir / "spec.md"
    doc.write_text(DOC, encoding="utf-8")
    store = run_full_loop(doc, clock)

    # G4: an agent with no library can cat the log and understand it.
    lines = (central_store_dir(doc) / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 7
    assert '"type":"round.open"' in lines[0]
    assert lines[-1].startswith('{"author":"alice"')
    assert "sha256:" in lines[0]


def test_a_document_outside_any_home_or_repository_works(tmp_path, clock, no_subprocess):
    # Deliberately deep and unremarkable: no marker file of any kind above it.
    workdir = tmp_path / "a" / "b" / "c"
    workdir.mkdir(parents=True)
    doc = workdir / "notes.md"
    doc.write_text(DOC, encoding="utf-8")
    store = run_full_loop(doc, clock)
    assert store.fold().count == 7


def test_sys_modules_never_gains_a_git_library(tmp_path, clock, no_subprocess):
    workdir = tmp_path / "plain"
    workdir.mkdir()
    doc = workdir / "spec.md"
    doc.write_text(DOC, encoding="utf-8")
    run_full_loop(doc, clock)
    assert {"git", "pygit2", "dulwich"} & set(sys.modules) == set()
