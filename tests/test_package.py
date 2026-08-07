"""The skeleton itself: the package imports and its errors form one hierarchy.

The second half is the packaging metadata, which is a contract with installers
rather than with callers. A Homebrew formula builds a virtualenv from the sdist,
links whatever ``[project.scripts]`` names, resolves the version from the tarball,
and then runs ``specround --version`` as its test. Every one of those four is a
fact about the metadata that no other test in this suite would notice breaking.
"""

from importlib import metadata

import specround
from specround.cli import main
from specround.errors import (
    AnchorError,
    ConfigError,
    InvariantError,
    LedgerError,
    SchemaError,
    SnapshotError,
    SpecroundError,
)


def test_version_is_exposed():
    assert specround.__version__


def test_every_error_descends_from_the_base():
    for err in (LedgerError, SchemaError, InvariantError, SnapshotError, AnchorError, ConfigError):
        assert issubclass(err, SpecroundError)


def test_a_config_error_is_not_a_ledger_error():
    # It fires before any file is touched: nothing was read, nothing rejected.
    assert not issubclass(ConfigError, LedgerError)


def test_the_public_names_are_importable_and_sorted():
    assert [name for name in specround.__all__ if not hasattr(specround, name)] == []
    assert specround.__all__ == sorted(specround.__all__)


def test_schema_and_invariant_errors_are_ledger_errors():
    # Callers catch LedgerError to mean "this log rejected the record".
    assert issubclass(SchemaError, LedgerError)
    assert issubclass(InvariantError, LedgerError)


def test_the_view_page_ships_with_the_package():
    """The page is package data, not a file beside the source tree.

    ``specround view`` reads it through the package, so a wheel that left it out
    would answer the first route a browser asks for with a 500 — and the source
    checkout would keep working, which is the worst place for that to hide.
    """
    from specround.webview import page

    assert page().startswith(b"<!doctype html>")


# -- what an installer is promised ---------------------------------------


def test_the_console_script_is_declared():
    """``specround`` on the PATH is the whole point of installing it.

    A packager links the console scripts the metadata declares and nothing
    else, so a renamed or dropped entry point produces an install that
    succeeds and then has no command in it.
    """
    scripts = metadata.entry_points(group="console_scripts")
    assert [(ep.name, ep.value) for ep in scripts if ep.name == "specround"] == [
        ("specround", "specround.cli:main")
    ]


def test_the_distribution_version_is_the_package_version():
    """One version, two readers — they have to agree.

    ``pyproject.toml`` reads ``__version__`` out of the package, so this is a
    guard on that wiring rather than on somebody's discipline: a build that
    stopped reading it would ship metadata saying one thing and a ``--version``
    saying another, and the formula's test block is what would find out.
    """
    assert metadata.version("specround") == specround.__version__


def test_the_version_flag_prints_what_a_formula_asserts(capsys):
    # `assert_match "specround #{version}"` in the Homebrew formula's test
    # block is this string. Reword it here and the brew install fails there.
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"specround {specround.__version__}"


def test_there_are_no_runtime_dependencies():
    """Standard library only, asserted rather than assumed.

    It is what keeps the formula a single ``url`` with no ``resource`` blocks:
    a virtualenv install with no network to do. The day a dependency arrives it
    has to be a deliberate act that updates the packaging, not a line added to
    an import block.
    """
    assert (metadata.requires("specround") or []) == []
