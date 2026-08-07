# specround

Spec review rounds for humans and AI agents — comments that survive revisions,
edits that become suggestion diffs, and every disposition recorded in an
append-only ledger the tool keeps for you. No server, no git required.

The ledger lives in a **central home store**, keyed by document path, so a
review leaves nothing behind in the document's folder — no untracked noise,
no gitignore homework. A team that wants shared history can opt the store back
into the repo with `.specround.json`. Committing the ledger is an optional
layer for sharing and durability, never a precondition for the tool to work.

**Status: ledger core, CLI, the local web view, and the inline-annotation
harvester landed.** The contract is
[SPEC.md](SPEC.md) (guarantees G1–G11); the ledger format and store location
live in [docs/ledger-format.md](docs/ledger-format.md). This tool's first
customer is the review of its own spec.

## Install

Python 3.10 or newer and nothing else — the package is standard library only,
so an install is one download with no dependency resolution to go wrong.

It is not on PyPI yet, so an install says where it comes from:

```bash
uv tool install git+https://github.com/haung921209/specround
pipx install git+https://github.com/haung921209/specround
```

From a checkout, `uv tool install --editable .` puts `specround` on your PATH
and leaves it pointing at the working tree.

Homebrew is prepared and not yet published: [`packaging/homebrew/`](packaging/homebrew)
holds the formula and the release checklist. Once a release exists,

```bash
brew tap haung921209/specround
brew install specround
```

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

# Answer a comment — a person and an agent use the same verb, same thread.
specround reply SPEC.md --comment c-d35c --body "the proxy caps it at 60"
specround reply SPEC.md --comment c-d35c --body-file - --author agent:reviewer

# Close the conversation once it is over. Resolved threads drop out of the
# default listing; --all brings them back, and nothing is ever deleted.
specround resolve SPEC.md --comment c-d35c --note "settled above"
specround comments SPEC.md --all
specround reopen SPEC.md --comment c-d35c --why "it came back in revision 3"

# Every comment gets a verdict and a reason: applied · rejected · answered · deferred.
specround dispose SPEC.md --comment c-d35c --as applied --why "raised to 60"
specround round close SPEC.md --allow-unresolved --note "retries move to round 2"

# After a revision: carry the comments over, and say which ones lost their text.
specround reanchor SPEC.md
```

## In your editor

Nothing has to be running. Open the document in whatever editor you have, type
[CriticMarkup](https://fletcher.github.io/MultiMarkdown-6/syntax/critic.html)
markers where they belong, and save.

| marker | becomes |
|---|---|
| `{>>the proxy caps at 45s<<}` | a comment at that point |
| `{++ See RFC 1.++}` | a suggestion that inserts the text |
| `{--not --}` | a suggestion that removes the marked text |
| `{~~30~>60~~}` | a suggestion that replaces the marked text |

```bash
specround harvest SPEC.md            # what it would record, and what the file becomes
specround harvest SPEC.md --apply    # record it, and rewrite the file without the markers
```

**The harvested text is the document, and a marker is a proposal about it.**
`{--x--}` leaves `x` in the file and records a suggestion to remove it;
`{++x++}` does not put `x` in the file at all, because it is not there yet.
Harvesting records proposals — carrying one out is a `dispose … --as applied`
somebody decides, never a side effect of reading the file.

A dry run is the default because this is the one verb that rewrites your
document, and it refuses everything `--apply` would. A marker with nothing in it
is a `2` with its line named. An opener with no closer, or the `{==highlight==}`
form this subset does not read, is left in the file and reported — nothing goes
missing quietly.

**Markers inside code are specimens, not instructions.** Fenced blocks and
inline backtick spans are skipped, which is what lets a document explain this
syntax — every table above is in this document, and harvesting it finds nothing.

Anchors count in the text the markers are gone from. If you annotated the
document after opening the round, or opened the round on the already-annotated
document, the spans are exact. If the prose moved as well, they are carried into
the round's base by the same ladder `reanchor` uses, and the ones that were
carried are flagged for you to look at.

## Comments made somewhere else

Review comments get left in other tools — a diff viewer with a line gutter, an
editor plugin. `import` brings them in, through a documented file format and
never through knowledge of the tool that made them.

```bash
adapters/cmux-diff-comments.py --doc SPEC.md > incoming.json
specround import SPEC.md --file incoming.json            # the plan; nothing written
specround import SPEC.md --file incoming.json --apply    # record it
```

The core reads [`specround.import/v0`](docs/import-format.md) and nothing else;
per-tool converters live in [`adapters/`](adapters) and import nothing from the
package. Each item quotes the text it is about, and an item whose quote is not
in the round's base is refused *by itself* with a reason — nothing is guessed
onto a neighbouring span. Where a comment came from is recorded, so importing
the same file twice imports it once and re-running after fixing one item is
safe.

## In a browser

```bash
specround view SPEC.md          # prints a URL; nothing opens
specround view SPEC.md --open   # ...unless you ask
```

The URL is the first line of stdout and no browser is opened, because the
first-class consumer is an embedder — a terminal multiplexer's browser pane takes
that line and places the view where you already are. `--open` is for when you are
the one at the shell.

One page, three modes, one anchor space: **render** (the markdown), **raw** (the
text), and **diff** (the document as it is now, against the snapshot this round
froze — not a git diff). Select text in any of them and the comment lands on the
same document anchor, so a comment made on the rendered prose is the comment the
CLI lists. Edit in raw mode and the submission is a suggestion diff (G8).
Threads carry their replies, verdicts, and resolve/reopen, and resolved ones are
hidden with a toggle rather than deleted (G11).

The anchor space is the round's base in every mode, because that snapshot is the
text the round is a review of (I7). In the diff, a line only the revision has has
no place in the base — selecting it carries the text back through the re-anchor
ladder, and when the ladder finds nothing the view says so and names the two ways
on (comment on the whole document, or open a new round on the revision). It never
guesses a nearby span.

It is a local process, not hosting: loopback only, a token in the URL, no state
of its own, and everything it records goes into the same ledger `specround
comments` reads. Closing the window loses nothing.

`--author` (or `$SPECROUND_AUTHOR`) says who is speaking — a person or
`agent:reviewer`, same field, same commands (G4). On `resolve` and `reopen`,
`--actor human|agent` (or `$SPECROUND_ACTOR`) records which *kind* of
participant decided; it defaults to `human` and is never guessed from the
author's name.

Exit codes are the verdict: `0` success · `2` fix your command (repeated
quote → `--occurrence`) · `3` the history refuses (no open round →
`round open`; a reply to a resolved thread → `reopen`) · `1` anything else.
Resolving a thread that is already resolved is a `0` that reports no change,
so a retry is always safe. Success goes to stdout and errors to stderr, so
`--json | jq` never receives an error object as a result.

Reading a ledger works anywhere; **appending needs POSIX**. Assigning `seq`
spans a read and a write, so it is done under an exclusive file lock, and an
interpreter without one (Windows, no `fcntl`) is told no rather than handed a
ledger two writers can put the same position in.

```bash
uv run pytest
```

Docs default to English; Korean guides may appear later as separate `-ko`
files.
