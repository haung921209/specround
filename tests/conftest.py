"""Shared fixtures.

The clock is injected everywhere so records are reproducible: a test that
appends the same events twice gets the same bytes, which is what lets the
determinism tests compare states instead of eyeballing them.

The data home is redirected for every test, without asking. The default store
is central, so a test that opens a round writes under ``$XDG_DATA_HOME`` — and a
suite that forgets to say so writes into the history of whoever ran it.
"""

import pytest

from specround.store import ReviewStore

DOC_NAME = "spec.md"
DOC_TEXT = """# Widget protocol

The client sends a hello frame. The server answers with a hello frame.

Timeouts are 30 seconds. Retries are not specified yet.
"""


class FixedClock:
    """A counting clock — deterministic, and obviously not wall time."""

    def __init__(self, start: int = 0) -> None:
        self.calls = start

    def __call__(self) -> str:
        self.calls += 1
        return f"2020-01-01T00:00:{self.calls:02d}Z"


@pytest.fixture(autouse=True)
def isolated_data_home(tmp_path_factory, monkeypatch):
    """Point every store at a throwaway data home.

    Autouse on purpose: "remember to redirect this" is not a guarantee, and the
    failure it prevents is silent — a passing suite that quietly appended to the
    developer's own review history.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.delenv("USERPROFILE", raising=False)
    return home


@pytest.fixture
def clock():
    return FixedClock()


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / DOC_NAME
    path.write_text(DOC_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def store(doc, clock):
    return ReviewStore.for_document(doc, clock=clock)


@pytest.fixture
def round_id(store, doc):
    return store.open_round(doc, author="alice", title="first pass")
