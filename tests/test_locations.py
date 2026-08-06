"""Where the store lands, and who decided (G5, G10).

Three properties carry the decision: the key a document hashes to is the same
every time and everywhere; the default writes nothing near the document; and an
opt-in wins over the default in a stated order. Everything else here is a way of
pinning one of those three down.
"""

import hashlib
import json
from pathlib import Path

import pytest

from specround.errors import ConfigError
from specround.locations import (
    CONFIG_FILENAME,
    DIRECTORY,
    DOCUMENT,
    ORIGIN_SCHEMA,
    STORE_DIRNAME,
    Origin,
    central_root,
    central_store_dir,
    data_home,
    find_config,
    path_key,
    read_config,
    resolve_location,
)

#: A path that exists nowhere, so ``resolve()`` has no symlink to follow and the
#: digest below is a constant rather than a restatement of the code.
FIXED_PATH = "/specround-test-root/docs/spec.md"
FIXED_KEY = "5acdd8cb2411aa3bacbd93e411a4972338360ace1a7fe6ea514e0ce5fe6f34ac"


@pytest.fixture
def workdir(tmp_path):
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


@pytest.fixture
def document(workdir):
    doc = workdir / "spec.md"
    doc.write_text("# spec\n", encoding="utf-8")
    return doc


def write_config(directory: Path, store: dict | None) -> Path:
    payload = {} if store is None else {"store": store}
    path = directory / CONFIG_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# -- the key -------------------------------------------------------------


def test_the_key_is_a_constant_for_a_given_path():
    # Hard-coded so a change to the derivation shows up as a failing test rather
    # than as histories that quietly stop being found.
    assert path_key(Path(FIXED_PATH)) == FIXED_KEY
    assert FIXED_KEY == hashlib.sha256(FIXED_PATH.encode("utf-8")).hexdigest()


def test_the_key_does_not_depend_on_how_the_path_was_spelled(document, monkeypatch):
    monkeypatch.chdir(document.parent)
    assert path_key(Path("spec.md")) == path_key(document)
    assert path_key(Path("./sub/../spec.md")) == path_key(document)


def test_a_symlink_and_its_target_are_one_document(tmp_path, document):
    link = tmp_path / "alias.md"
    link.symlink_to(document)
    # One document, one history — following the link is the whole reason the key
    # hashes the resolved path.
    assert path_key(link) == path_key(document)


def test_different_documents_get_different_stores(workdir, document):
    other = workdir / "other.md"
    other.write_text("# other\n", encoding="utf-8")
    assert central_store_dir(other) != central_store_dir(document)


def test_the_central_store_is_sharded_under_the_data_home(document):
    key = path_key(document)
    assert central_store_dir(document) == central_root() / "docs" / key[:2] / key[2:]
    assert central_root() == data_home() / "specround"


# -- the data home -------------------------------------------------------


def test_xdg_data_home_moves_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "elsewhere"))
    assert data_home() == tmp_path / "elsewhere"


def test_a_relative_xdg_data_home_is_ignored(tmp_path, monkeypatch):
    # The basedir spec says a relative value is invalid and must be ignored.
    monkeypatch.setenv("XDG_DATA_HOME", "relative/share")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert data_home() == tmp_path / ".local" / "share"


def test_an_unset_xdg_data_home_falls_back_to_local_share(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert data_home() == tmp_path / ".local" / "share"


# -- the default ---------------------------------------------------------


def test_the_default_is_central_and_names_the_document(document):
    location = resolve_location(document)
    assert location.source == "default"
    assert location.root == central_store_dir(document)
    assert location.origin == Origin(DOCUMENT, document.resolve())
    assert location.is_central


def test_resolving_writes_nothing_anywhere(workdir, document):
    before = sorted(p.name for p in workdir.iterdir())
    location = resolve_location(document)
    # Resolution is arithmetic on paths. Directories appear when history does.
    assert sorted(p.name for p in workdir.iterdir()) == before
    assert not location.root.exists()


def test_the_default_leaves_the_documents_folder_alone(workdir, document):
    resolve_location(document)
    assert not (workdir / STORE_DIRNAME).exists()


def test_a_document_key_counts_from_its_own_folder_when_central(document):
    origin = resolve_location(document).origin
    assert origin.base_dir == document.parent.resolve()


# -- the config tier -----------------------------------------------------


def test_beside_puts_the_store_in_the_documents_folder(workdir, document):
    config = write_config(workdir, {"mode": "beside"})
    location = resolve_location(document)
    assert location.source == "config"
    assert location.config == config
    assert location.root == workdir / STORE_DIRNAME
    assert location.origin == Origin(DIRECTORY, workdir.resolve())


def test_path_puts_the_store_where_the_config_says(tmp_path):
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    doc = docs / "spec.md"
    doc.write_text("# spec\n", encoding="utf-8")
    write_config(repo, {"mode": "path", "path": "review-store"})

    location = resolve_location(doc)
    assert location.root == repo / "review-store"
    # Keys count from the config file, so one shared store serves every document
    # in the repository and the ledger reads the same after a clone.
    assert location.origin == Origin(DIRECTORY, repo.resolve())


def test_an_absolute_config_path_is_taken_as_written(tmp_path, workdir, document):
    elsewhere = tmp_path / "shared-store"
    write_config(workdir, {"mode": "path", "path": str(elsewhere)})
    assert resolve_location(document).root == elsewhere


def test_central_in_a_config_restates_the_default(workdir, document):
    write_config(workdir, {"mode": "central"})
    location = resolve_location(document)
    assert location.source == "config"
    assert location.root == central_store_dir(document)
    assert location.origin.kind == DOCUMENT


def test_a_config_without_a_store_section_is_no_opinion(workdir, document):
    write_config(workdir, None)
    assert resolve_location(document).source == "default"


def test_the_nearest_config_wins(tmp_path):
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    doc = docs / "spec.md"
    doc.write_text("# spec\n", encoding="utf-8")
    write_config(repo, {"mode": "path", "path": "far"})
    write_config(docs, {"mode": "beside"})
    assert resolve_location(doc).root == docs / STORE_DIRNAME


def test_a_config_above_the_document_still_applies(tmp_path):
    repo = tmp_path / "repo"
    deep = repo / "a" / "b" / "c"
    deep.mkdir(parents=True)
    doc = deep / "spec.md"
    doc.write_text("# spec\n", encoding="utf-8")
    write_config(repo, {"mode": "beside"})
    assert resolve_location(doc).root == deep / STORE_DIRNAME
    assert find_config(deep) == repo / CONFIG_FILENAME


def test_no_config_anywhere_reads_as_none(workdir):
    assert find_config(workdir) is None


# -- the argument tier ---------------------------------------------------


def test_an_explicit_store_beats_a_config(workdir, document, tmp_path):
    write_config(workdir, {"mode": "beside"})
    chosen = tmp_path / "chosen"
    location = resolve_location(document, store=chosen)
    assert location.source == "argument"
    assert location.root == chosen


def test_an_explicit_store_counts_keys_from_its_parent(workdir, document):
    location = resolve_location(document, store=workdir / "store")
    # ``<dir>/.specround`` is this rule, not a second one.
    assert location.origin == Origin(DIRECTORY, workdir.resolve())


def test_an_explicit_base_overrides_the_parent(tmp_path, workdir, document):
    location = resolve_location(document, store=tmp_path / "far" / "store", base=workdir)
    assert location.origin == Origin(DIRECTORY, workdir.resolve())


# -- refusing bad settings -----------------------------------------------


@pytest.mark.parametrize(
    "payload, message",
    [
        ('{"store": {"mode": "elsewhere"}}', "store.mode"),
        ('{"store": {"mode": "path"}}', "non-empty"),
        ('{"store": {"mode": "path", "path": ""}}', "non-empty"),
        ('{"store": {"mode": "beside", "path": "x"}}', "only applies"),
        ('{"store": {"mode": "beside", "extra": 1}}', "unknown key"),
        ('{"store": "beside"}', "must be an object"),
        ('{"stores": {"mode": "beside"}}', "unknown top-level key"),
        ('["beside"]', "top level must be a JSON object"),
        ("{oops", "not valid JSON"),
        ('{"store": {}}', "store.mode"),
    ],
)
def test_a_config_that_cannot_be_obeyed_is_refused(workdir, document, payload, message):
    (workdir / CONFIG_FILENAME).write_text(payload, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        resolve_location(document)


def test_reading_a_missing_config_is_an_error_not_a_shrug(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        read_config(tmp_path / "absent.json")


# -- the origin record ---------------------------------------------------


def test_an_origin_round_trips_through_its_line():
    origin = Origin(DOCUMENT, Path(FIXED_PATH))
    line = origin.encode()
    assert line.endswith("\n")
    assert json.loads(line) == {
        "schema": ORIGIN_SCHEMA,
        "kind": DOCUMENT,
        "path": FIXED_PATH,
    }
    assert Origin.decode(line) == origin


def test_the_origin_carries_the_documents_path_in_plain_text():
    # H10 is not implemented, but re-binding a moved document has to stay
    # possible: the path a key hashes cannot be recovered from the key.
    assert FIXED_PATH in Origin(DOCUMENT, Path(FIXED_PATH)).encode()


def test_a_directory_origin_is_its_own_base():
    origin = Origin(DIRECTORY, Path("/specround-test-root/docs"))
    assert origin.base_dir == Path("/specround-test-root/docs")


@pytest.mark.parametrize(
    "line, message",
    [
        ('{"schema": "other/v0", "kind": "document", "path": "/x"}', "unsupported origin schema"),
        (f'{{"schema": "{ORIGIN_SCHEMA}", "kind": "folder", "path": "/x"}}', "unknown origin kind"),
        (f'{{"schema": "{ORIGIN_SCHEMA}", "kind": "document"}}', "non-empty 'path'"),
        (f'{{"schema": "{ORIGIN_SCHEMA}", "kind": "document", "path": "x"}}', "must be absolute"),
        (f'{{"schema": "{ORIGIN_SCHEMA}", "kind": "document", "path": "/x", "z": 1}}', "unknown key"),
        ("not json", "not valid JSON"),
    ],
)
def test_a_broken_origin_is_refused(line, message):
    with pytest.raises(ConfigError, match=message):
        Origin.decode(line)
