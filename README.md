# specround

Spec review rounds for humans and AI agents — comments that survive revisions,
edits that become suggestion diffs, and every disposition recorded in an
append-only ledger the tool keeps for you. No server, no git required.

The ledger lives in a **central home store**, keyed by document path, so a
review leaves nothing behind in the document's folder — no untracked noise,
no gitignore homework. A team that wants shared history can opt the store back
into the repo with `.specround.json`. Committing the ledger is an optional
layer for sharing and durability, never a precondition for the tool to work.

**Status: ledger core + CLI landed · web view not started.** The contract is
[SPEC.md](SPEC.md) (guarantees G1–G11); the ledger format and store location
live in [docs/ledger-format.md](docs/ledger-format.md). This tool's first
customer is the review of its own spec.

## One round

Inside this repo use `uv run specround …`; once installed, plain `specround …`.

```bash
# Freeze the document as this round's base. Nothing is staged, nothing is committed.
specround round open SPEC.md --title "first pass"

# Comment on a span of that base. A quote that repeats asks which one you mean.
specround comment SPEC.md --quote "30 seconds" --body "too short for the proxy"
specround comment SPEC.md --body-file - <<< "the whole retry section is missing"

specround comments SPEC.md            # a table; --json on any verb for an agent
specround round status SPEC.md        # rounds, counts, what is still outstanding

# Every comment gets a verdict and a reason: applied · rejected · answered · deferred.
specround dispose SPEC.md --comment c-d35c --as applied --why "raised to 60"
specround round close SPEC.md --allow-unresolved --note "retries move to round 2"

# After a revision: carry the comments over, and say which ones lost their text.
specround reanchor SPEC.md
```

`--author` (or `$SPECROUND_AUTHOR`) says who is speaking — a person or
`agent:reviewer`, same field, same commands (G4). Exit codes are the verdict:
`0` success · `2` fix your command (repeated quote → `--occurrence`) ·
`3` the history refuses (no open round → `round open`) · `1` anything else.
Success goes to stdout and errors to stderr, so `--json | jq` never receives
an error object as a result.

Reading a ledger works anywhere; **appending needs POSIX**. Assigning `seq`
spans a read and a write, so it is done under an exclusive file lock, and an
interpreter without one (Windows, no `fcntl`) is told no rather than handed a
ledger two writers can put the same position in.

```bash
uv run pytest
```

Docs default to English; Korean guides may appear later as separate `-ko`
files. The spec itself is being migrated.
