# specround

Spec review rounds for humans and AI agents — comments that survive revisions,
edits that become suggestion diffs, and every disposition recorded in an
append-only ledger the tool keeps for you. No server, no git required.

The ledger lives in a **central home store**, keyed by document path, so a
review leaves nothing behind in the document's folder — no untracked noise,
no gitignore homework. A team that wants shared history can opt the store back
into the repo with `.specround.json`. Committing the ledger is an optional
layer for sharing and durability, never a precondition for the tool to work.

**Status: ledger core, CLI, and the local web view landed.** The contract is
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
files. The spec itself is being migrated.
