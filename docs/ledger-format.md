# Ledger format — `specround.ledger/v0`

> This format is the contract. The core, the CLI, and the web view are
> implementations we can swap out; what remains is this file's schema
> (SPEC.md G5). That is why the format document sits above the implementation
> documents.

The contract's minimum sentence: **one line = one event = one JSON object.**
Anything that can write these lines is a participant — any language, any editor,
any agent — and with no tool to read them, `cat` is a valid reader.

## 1. Where it lives

The ledger, the snapshots, and `origin` live together in **one store
directory**. Its default place is not beside the document but the **central
home**.

```
$XDG_DATA_HOME/specround/docs/5a/cdd8cb…/
  origin                   ← what this store was made for (one plain-text line)
  ledger.jsonl             ← the event log (append-only)
  objects/35/c081dd…       ← document snapshots (content-addressed, sha256)
```

- **The key = sha256 of the document's absolute path (after normalization).**
  A relative spelling and a symlink both land in the same store — one document,
  one history. Because the key is the path, **editing** the document leaves the
  history where it was. Normalization goes one step past `resolve()`: **each
  segment of the path is replaced with the spelling written on disk.**
  `resolve()` only unfolds links and `..` and leaves case alone, so on a
  case-insensitive filesystem (the macOS default) `Real.md` and `real.md` are
  **one file whose key split in two** — one tab completion, or one `cd` typed in
  another case, and a new empty history appears while the tool answers "round 0"
  as though it were a fact. The rule is one line and is right on both kinds of
  filesystem: **the spelling on disk is the correct one, and an exactly matching
  entry wins when there is one** (on a case-sensitive filesystem the two really
  are different files).
- **The same key decides the web view's default port.** `specround view` folds
  that digest into the dynamic range (49152–65535), so a document comes back on
  the address an embedded browser pane is already holding. It is a *consumer* of
  the rule above, not a second one: normalize differently and the port moves with
  the store, which is the point — a port derived some other way would address one
  document while the ledger under it belonged to another. (A directory view keys
  off the directory, by the same rule.)
- **The data home** = `$XDG_DATA_HOME` (only when absolute), otherwise
  `~/.local/share`. A store is data — a cache may be evicted and config is what
  you hand-edit, so it is neither. Instead of the per-platform convention (macOS
  `~/Library/Application Support`) it uses one rule: "`cat` is a valid reader"
  only holds if the path is one a person can type from memory.
- **A `.specround/` beside the document is not the default.** Inside a git tree
  it becomes untracked noise and creates gitignore homework, which makes "it
  runs without git" (G5·G10) a lie in exactly that place.
- **Zero git calls.** The ledger and the snapshots are plain files. Everything
  works whether the document is untracked or outside a repo. Committing the
  ledger is an optional layer for sharing and durability, never a precondition
  for the record (G5·G10).

### 1.1 Putting the store in the repo (opt-in)

A team that wants to share history reverses this. The **nearest**
`.specround.json` found while walking up from the document's directory decides
(the default applies when there is none).

```json
{"store": {"mode": "beside"}}
{"store": {"mode": "path", "path": "review"}}
{"store": {"mode": "central"}}
```

| mode | store location | base for the `doc` key |
|---|---|---|
| `central` | central (same as the default) | that document's folder |
| `beside` | `<document folder>/.specround` | the document's folder |
| `path` | `<config file's folder>/<path>` (used as-is when absolute) | the config file's folder |

`path` is the real answer for sharing in a repo — documents in several folders
use one ledger, and because the key is relative to the config file, a clone
reads the same file with the same meaning.

Changing the setting only stacks **records made after it** in the new place. The
history that exists stays in the old store and does not follow automatically —
moving history is the same family of work as H10 and does not exist yet.

The config is JSON, not TOML: `tomllib` arrived in 3.11 while this package
supports 3.10 with no dependencies, so TOML costs either a dependency or a
feature that vanishes on a supported interpreter. **Unknown keys are refused** —
for the ledger's reason, a setting that is ignored quietly is a setting you
believe is in effect.

### 1.2 Resolution order — argument > config > default

| layer | what it decides | base |
|---|---|---|
| argument | the store path the caller gave | the base given with it → the `origin` the store wrote down → **the store's parent** |
| config | the nearest `.specround.json` | the §1.1 table |
| default | the central store | that document's folder |

`<folder>/.specround` is a special case of the argument rule (the parent is the
base) — it is set up that way to keep the rule from splitting in two.

The three rungs inside the argument tier **put the guess last**. The parent is a
guess and `origin` is the store's own record (§1.3), so reading an existing
store over the parent **splits one document across two keys** — the flag's side
answers "round 0" as if it were a fact about history it cannot see, and then
writes a second round on top of it into the same ledger. So when the caller
passes `base` alongside, that wins (the tier order holds); when they do not, the
store's `origin` is read; and only in a store with no `origin` does it fall back
to the parent.

### 1.3 The `doc` key and `origin`

- An event's `doc` is a **relative POSIX path against the store's base**. Being
  relative is what keeps an in-tree store's ledger valid after the folder is
  moved or cloned.
- `origin` records **what the store was made for**, in one plain-text line. A
  central store's name is a digest and a hash is one-way, so without this line a
  history directory cannot say who it belongs to.

  ```json
  {"kind":"document","path":"/home/me/docs/spec.md","schema":"specround.origin/v0"}
  ```

  `kind` is `document` (central — one document) or `directory` (in-tree — every
  document in that folder). **It is written once and never rewritten** — the
  original path is the only place a later re-binding (H10) can start from. A
  store with no `origin` is taken to predate this file and read as covering its
  parent folder.
- Moving or renaming a document splits the key, so a **new store** appears (H10,
  unimplemented). The old history does not disappear: it stays in the old store,
  and `origin` goes on naming the original path.

## 2. Schema version and the compatibility rule

Every line carries a `schema` field. The form is `<name>/v<major>` and the
current value is **`specround.ledger/v0`**.

| situation | what the reader does |
|---|---|
| no `schema`, or a malformed one | refuse |
| a different name (`other.tool/v0`) | refuse — it is somebody else's ledger |
| a different major (`…/v1`) | **refuse. No guessing** |
| an unknown top-level key within the major | refuse |

Within a major the field set is **closed**. Letting an unknown key through
quietly is the moment it stops being a contract, so experiments that add fields
go in the reserved field **`ext`** (an object). The reader does not inspect
inside `ext`, only preserves it. When something proven in `ext` gets promoted,
major goes up.

Three things use `ext` today. The first two are **input surfaces where the
anchor was not cut from the base directly**. Both record the same fact — "this
anchor was cut from text that is not the base and carried over by the ladder,
and it came through this rung". If it could not be told apart from a comment
picked on the base directly, the reason §4 keeps `fuzzy` would be gone from the
comment.add axis. When the rung is `position` nothing moved, so nothing is
recorded.

| key | what writes it | `space` | the text that was cut |
|---|---|---|---|
| `ext.view` | the web view's diff mode | `revision` | the document on disk (the revision) |
| `ext.harvest` | the inline-annotation harvester (`harvest`) | `clean` | the document **with the annotation syntax removed** |

```json
{"strategy":"quote","ambiguous":false,"space":"clean"}
```

The inside shape is the same and there are two keys because there are two
surfaces; `space` has three values because there are three texts that get cut
(the harvester's is neither the revision nor the base — it is a third string,
the revision with only the markers removed). **Merging them into one is work for
the promotion**, and promotion bumps major. The reader does not inspect inside
`ext`, so merging now buys nothing under the contract, and promoting a name that
has not been run in its merged form is the very thing `ext` exists to prevent.

The harvester mostly does not use `ext` — annotate on top of a round's base
(removing the markers brings the base back) or open a round on an
already-annotated document (the base holds the markers, so the span sits inside
one) and the offsets are **exact by arithmetic**, with no ladder run. Only when
the prose moved as well does the ladder run, and only then does this line
appear.

The third is **absorbing outside comments** (H9). When a comment made in another
tool is brought in, its origin is recorded as
`ext.import = {"source": …, "id": …, "ts": …}`. That pair is the idempotency key
for re-importing, so putting the same file in twice puts it in once. The
boundary itself is not this ledger but a separate format,
`specround.import/v0` (`import-format.md`), and an imported comment is just a
`comment.add` here — neither a kind nor a field was added.

All three are promotion candidates, and candidates is all they are — promotion
bumps major.

**Event kinds are closed in the same sense** — an unknown `type` is refused. So
adding a kind is in principle a major-bumping change, and the arrival of
`anchor.reanchor` and `anchor.orphan` in v0 is not an exception but **filling a
place §10 had left empty** (that entry said in advance, "when re-anchoring
arrives, record the anchor update as a new event"). v0 is not frozen yet, and no
v0 ledger predates these two kinds. **Once v0 is frozen, adding a kind bumps
major** — an older reader refuses the whole file at that line.

`thread.resolve` and `thread.reopen` came in through the same window. Their
place was held not by §10 but by **the SPEC's guarantee G11** — "a conversation
that is over is closed with resolve" was declared from the start, and what was
added is that guarantee's implementation, not a new idea. The condition for
using the window is the same as before: v0 is not frozen, and no v0 ledger
exists outside this tool's own dogfooding. **The window closes at the first
outside user**, and adding a kind after that bumps major.

**Why the revision that moved the store to the central home did not bump
major**: `doc`'s rule was **generalized** from "relative to `.specround`'s
parent" to "relative to the store's base", and in an in-tree store the base *is*
`.specround`'s parent, so every line of an existing v0 ledger reads with
**literally the same meaning**. No field was added either. Major goes up when
"what you were reading starts reading differently", and where the store lives is
a fact **outside** the ledger. (`origin` is a separate file, not the ledger, and
carries its own schema `specround.origin/v0`.)

## 3. The common envelope

Every event carries these. All required.

| field | type | meaning |
|---|---|---|
| `schema` | string | `specround.ledger/v0` |
| `seq` | integer ≥ 0 | **the 0-based position in the file.** It must equal the line number |
| `ts` | string | UTC ISO8601 to the second (`2026-02-01T09:00:00Z`). **Not used for ordering** |
| `type` | string | one of the ten kinds below |
| `id` | string | this event's identifier. A kind prefix + 12 digest characters |
| `author` | string | who is speaking. A person and an agent share the field (`alice`, `agent:reviewer`) — G4 |

`author` says only **who**, never whether that is a person or an agent. The
`agent:` prefix is a convention, so a reader cannot check it and it is
indistinguishable from somebody who named themselves that way. Only where that
distinction bears on a judgement (closing a thread) is a separate closed
vocabulary, `actor`, required — an asymmetry kept to the place that needs it,
and lifting it into the envelope is a candidate for when major goes up (§10).

`id` is derived by the tool when the caller does not supply one: the prefix
(`r` round · `c` comment · `s` suggestion · `p` reply · `d` disposition ·
`x` round close · `a` re-anchor · `o` orphan · `v` resol**v**e ·
`n` reope**n**) plus the first 12 characters of `sha256(everything but the id)`.
Because the digest covers `seq`, **two comments with the same content still get
different ids**, and replaying in the same order produces the same ids.

## 4. The ten event kinds

### `round.open` — open a round

**Freezes** the document's current content and takes that snapshot as the base.
This is the heart of G2: review's reference is not a commit but a snapshot the
tool froze, so opening a review never involves staging or committing (G10).

| field | required | meaning |
|---|---|---|
| `doc` | ✓ | the document's path (relative POSIX) |
| `base` | ✓ | the snapshot reference `sha256:<64 hex>` |
| `title` | | the round's name (an empty string is allowed) |

**Opening a round is the only event that makes a new anchor space, so it is
also what carries the comments into one** (§5.2). The `anchor.reanchor` and
`anchor.orphan` records the carry appends follow this record and name this
`base`; a document that did not change is addressed to the same snapshot, so
nothing is appended. This is a rule about the tool rather than the format — the
format's part of it is I12.

### `comment.add` — a comment

| field | required | meaning |
|---|---|---|
| `round` | ✓ | the target round's id (**it must be open**) |
| `body` | ✓ | the text (an empty string is not allowed) |
| `anchor` | | the document anchor (§5). Without it, the comment is about the whole document |

### `suggestion.add` — a suggestion (G8)

A comment whose body is a patch. The disposition axis is a comment's
(apply/reject); the fields differ because the substance is a diff.

| field | required | meaning |
|---|---|---|
| `round` | ✓ | the target round's id (it must be open) |
| `patch` | ✓ | the patch text |
| `body` | | the reason for the suggestion (optional — the patch is the substance) |
| `anchor` | | the document anchor |

### `reply` — a reply

| field | required | meaning |
|---|---|---|
| `target` | ✓ | a comment or suggestion id. **A flat structure** — there is no replying to a reply |
| `body` | ✓ | the text |

A comment in a closed round can still be replied to (answering late is normal).
**A closed thread cannot be** — resolved is hidden from the default view
(§7.1), so a reply underneath lands where nobody looks. An answer that got
recorded and does not get read is exactly the loss G3 prevents, so the reader
refuses it and asks for `thread.reopen` first (I11).

### `disposition` — a disposition (G3)

| field | required | meaning |
|---|---|---|
| `target` | ✓ | a comment or suggestion id |
| `verdict` | ✓ | `applied` · `rejected` · `answered` · `deferred` |
| `reason` | ✓ | the reason (an empty string is not allowed) — required on all four verdicts |

The vocabulary is **closed**. An arbitrary value such as `wontfix` is refused.

### `round.close` — close a round

| field | required | meaning |
|---|---|---|
| `round` | ✓ | the target round's id (it must be open) |
| `unresolved` | conditional | the list of comment ids left undisposed (sorted). **Required** when any are undisposed |
| `note` | | a closing note |

**The field is spelled `unresolved` and means undisposed.** That is the one
place the two axes still share a word, and it stays because the field set is
closed within a major (§2): renaming it would make every ledger that used it
unreadable rather than just differently spelled. Everywhere above the ledger —
the fold, `--json`, the page — the disposition axis is `undisposed` and
"unresolved" is the thread axis (§7.2).

Closing with things left undisposed is not blocked. What is blocked is
**closing while hiding them** — when the list left behind is not exactly the
real undisposed set, the reader refuses (I6). So even a hand-written
`round.close` cannot walk past an open comment quietly.

### `anchor.reanchor` — bind an anchor to a new snapshot (G1)

When the document is revised, a comment's anchor is found again in the revision
and re-attached. **Past lines are not edited** — the anchor as of the comment's
creation stays where it was, and "where is it now" is this event's latest value.

| field | required | meaning |
|---|---|---|
| `target` | ✓ | a comment or suggestion id. It must be one **that has an anchor** (I9) |
| `base` | ✓ | the snapshot reference this anchor is consistent with, `sha256:<64 hex>` |
| `anchor` | ✓ | the new anchor (§5). It was cut out of that snapshot |
| `strategy` | ✓ | which rung found it — `position` · `quote` · `normalized` · `fuzzy` |
| `ambiguous` | | `true` when two or more places scored the same and the position hint chose |

The `strategy` vocabulary is **closed**. This value is what lets the reading side
tell "a comment that merely shifted" from "a comment whose text was rewritten" —
the latter is something a person should look at once, the former is not.
`ambiguous` serves the same purpose: **nothing is chosen quietly**.

**Scores (similarity numbers) are not carried.** A line has to be the same bytes
regardless of language (id derivation depends on it), and floating-point
notation is the classic place that promise breaks. When finer measurement is
needed, it goes in `ext`.

The round need not be open. Comments outlive rounds, and the revision that moves
prose usually comes **after** a round is closed.

### `anchor.orphan` — the anchor was not found (G1 × G3)

When re-anchoring fails, the comment is not dropped quietly: **the fact that it
was not found is recorded**. This is what zero loss means on the revision axis.

| field | required | meaning |
|---|---|---|
| `target` | ✓ | a comment or suggestion id. It must be one that has an anchor (I9) |
| `base` | ✓ | the snapshot it was not found in |
| `reason` | ✓ | why it was not found (an empty string is not allowed) |

An orphan is **not a disposition**. A disposition is "what was done about this
point" and an orphan is "can this comment still be placed on the document" —
different axes. An applied comment can be an orphan (rather the common case,
since applying it deleted the prose), and a comment can be both orphaned and
undisposed.

**An orphan does not lose its anchor.** The last anchor that succeeded is still
the current one, and if that prose returns in a later revision
`anchor.reanchor` attaches again (with append-only, coming back is natural).

### `thread.resolve` — this conversation is over (G11)

**A thread = one comment (or suggestion) + the chain of replies under it.**
Replies are flat (§`reply`), so a thread has no object of its own and **the root
comment's id is the thread's id** — which is why `target` names a comment.

| field | required | meaning |
|---|---|---|
| `target` | ✓ | the thread's root comment or suggestion id |
| `actor` | ✓ | `human` · `agent`. **A closed vocabulary** |
| `note` | | a note left while closing (an empty string is allowed) |

`actor` is required because G11 is "a person and an agent can both resolve" — to
the reading side, "an agent judged this discussion over" and "a person did" are
different facts that lead to different next actions, and the `agent:` prefix in
the author string is a convention a reader cannot check (§3).

The round need not be open. Threads outlive rounds, and a conversation usually
wraps up after the round is closed.

**Resolve is not a disposition.** A disposition (§`disposition`) is what was
done about one comment, and resolve is whether that conversation is over —
different axes (§7). So a thread that is resolved with no disposition (simply
agreed) is normal, and so is a thread that is settled but still open (applied,
discussion continuing). **`round.close`'s undisposed count does not look at
resolve** — if it did, closing a thread would have become a way to walk past an
undisposed comment quietly (exactly what I6 prevents).

### `thread.reopen` — it was closed by mistake (G11)

| field | required | meaning |
|---|---|---|
| `target` | ✓ | the thread's root comment or suggestion id |
| `actor` | ✓ | `human` · `agent` |
| `reason` | ✓ | why it is being reopened (an empty string is not allowed) |

Resolve takes an optional note while reopen requires a reason. **An event that
overturns a judgement already in the ledger owes a reason** — the same rule as
`disposition` and `anchor.orphan`. The closing side is not held to it because
the conversation itself is the record of the reason.

Undoing is **an append too.** Past lines are not edited, so "closed → reopened"
reads as history, and **whether it is closed now** is the latest value of these
two kinds.

## 5. Anchors (G1)

A pair of W3C Web Annotation selectors — the quote (`TextQuoteSelector`) and the
position (`TextPositionSelector`), carried **together**. Either alone is too
thin: a position dies when one character above it changes, and a quote does not
know which place it means when the passage repeats.

| field | required | meaning |
|---|---|---|
| `exact` | ✓ | the quoted string (an empty string = an insertion point) |
| `start` / `end` | ✓ | character offsets. `end - start == len(exact)` |
| `prefix` / `suffix` | | 32 characters of context on each side (clipped at the document's edges) |

**The write-time consistency invariant**: `text[start:end] == exact`, and the
context has to match at that position too. An anchor that disagrees cannot be
recovered later, so it is refused before the append.

The text it is checked against is **the snapshot that anchor names**. For
`comment.add` and `suggestion.add` that is the round's base (the text the
reviewer read); for `anchor.reanchor` it is that event's `base`.

Offsets are in **characters**, not bytes, and a snapshot preserves the original
bytes with no normalization (CRLF and a trailing newline included) — touch the
snapshot and the anchors shift quietly.

### 5.1 Re-anchoring across a revision (H4)

The rule for finding an anchor again in the revision. Four rungs are tried in
order, and **whichever rung answers, the result is cut out of the revision
again** — which is why a recorded anchor is always consistent with the snapshot
it names. The old quote and the old context are not carried along.

| rung | `strategy` | what it catches |
|---|---|---|
| 1 | `position` | the offsets still match — nothing above moved |
| 2 | `quote` | the quote is still there — a line inserted above, a paragraph moved |
| 3 | `normalized` | folding quotes, dashes, whitespace runs, and Unicode composition makes them equal — a reflow, a typographic fix |
| 4 | `fuzzy` | the quote itself was edited — approximate alignment plus a similarity floor |

When the same quote appears several times, **the context and the old position**
choose (which is why an anchor carries context). When two or more places score
the same, position decides but it is marked `ambiguous`. When all four rungs
fail to clear the floor it is an `anchor.orphan` — **nothing is guessed into
place.**

**The floor applies on all four rungs.** A quote being there literally means
"the same sentence", not "the same place" — prose repeats, and the same sentence
under a different section is a different sentence. So rungs 2 and 3 also look at
how much of the anchor's context survived, and **refuse** a place that does not
clear it (they do not merely rank). Measuring by what survives is right because
a revision usually deletes on one side only — delete the paragraph above and the
prefix dies while the suffix is untouched. When neither side survives, it is not
"the thing that moved" but "a different place". An orphan with no place to
attach is a visible failure, while a comment attached confidently to the wrong
place is a quiet wrong answer.

The cost is bounded by constants rather than by document size (a candidate cap
and a fixed alignment window). Approximate matching is quadratic, so without
that cap a short common quote in a long document stalls for seconds — the
failure mode Hypothesis measured. **That cap cuts around the old position, not
from the top of the document** — cutting from the top would let the cap decide
not just the candidate set but the answer (the true place never even gets
scored, and `ambiguous` is not raised either).

This rule belongs **to the tool, not to the format**. All the ledger requires is
the `strategy` vocabulary and "a recorded anchor is consistent with its own
`base`" (I7); changing the floor or how candidates are generated is not a schema
change. That is why scores are not carried — a tuning value that leaks into the
contract cannot be changed.

### 5.2 Which text a comment is carried *into* (I12)

I7 asks whether an anchor agrees with the snapshot **it names**. That is not the
same question as whether it agrees with the snapshot it is **shown on**, and the
gap between the two is a real failure with a measurement behind it.

Every surface draws a comment over a round's base — the view renders that
snapshot in all three modes (SPEC §3 ①), the CLI quotes against it. So an
anchor's offsets only mean anything in a round's base. An older re-anchor pass
took its target from the **document on disk** instead: it froze whatever the
file happened to be, cut anchors from that, and recorded them. Each record was
self-consistent, so I7 passed every one of them, and every surface went on
painting them over the round's base. On one real review, **12 of 17 comments
were drawn on sentences they were not about**, and clicking a mark opened an
unrelated thread. The two definitions of "the anchor's space" — the round's base
and the current file — were living in one field.

The rule that closes it has two halves.

- **An anchoring's `base` is a round's base.** New space is made by `round.open`
  and by nothing else, and opening carries the comments into it (§4). There is
  no verb that freezes a text for anchors without freezing it for the review.
- **I12: a comment's `current_anchor` holds in the base it is painted on.**
  `current_anchor` is the last anchor bound to it, or the one it was made with.

I12 is **reported, not refused**, and that is deliberate: ledgers written before
this rule exist, and refusing to fold one would take away the only way to read
or repair it. A comment that fails it is marked and **not drawn** — a mark on
the wrong sentence reads like a correct answer, while a missing mark with a
badge beside it does not. `specround doctor` repairs those records the way this
format repairs anything: the quote is re-read in the right base and the
correction is **appended** (I1), leaving the original where it is.

Like §5.1 this is the tool's rule. What the format fixes is only that an
anchoring names a `base` and that the reader may check it.

## 6. Invariants

The reader enforces them. A violation is an exception, and there is no leniency
such as "skip just that line".

| id | invariant | when broken |
|---|---|---|
| I1 | **append-only.** There is no update or delete operation. Only new lines are added | — |
| I2 | `seq` == the line's position in the file | deleting or reordering by hand is an **error** (not a silent wrong answer) |
| I3 | `id` is unique across the whole ledger | refused |
| I4 | a comment or suggestion names an **open** round | refused (open a new round) |
| I5 | a settled comment cannot be re-disposed | refused |
| I6 | `round.close`'s `unresolved` field == the real undisposed set (the disposition axis — resolving a thread does not take a comment out of it) | refused |
| I7 | an anchor is consistent with the snapshot it names (comment = the round's base · re-anchor = that event's `base`) | refused |
| I8 | the `target` of a reply, disposition, resolve, or reopen is an existing comment or suggestion | refused |
| I9 | the `target` of a re-anchor or an orphan is a comment or suggestion **that has an anchor** | refused (a whole-document comment has nowhere to move) |
| I10 | a resolve or reopen on a thread already in that state **does not change the state** | not refused — idempotent |
| I11 | `reply`'s `target` is an **open** thread | refused (`thread.reopen` first) |
| I12 | a comment's `current_anchor` is consistent with the base it is **painted on** — the latest round's, not merely the one the anchor names (§5.2) | **reported, not refused**: the comment is marked and no surface draws it, and `specround doctor` appends the correction |

**The reading code is the writing gate.** A write folds `prior + the new record`
and appends to the file only if that passes. So what arrives through the API and
what was written by hand meet **the same oracle** — two copies of the checking
logic would inevitably diverge.

**An append with no exclusive lock is refused.** `seq` is taken from the current
length, so without a lock two writers get the same `seq`, and a ledger with
overlapping positions is refused whole by the reader (I2) — the path by which
the loss this format prevents enters at the file layer instead of the anchor
layer. So on an interpreter without the locking primitive (POSIX `fcntl`),
**writing is refused and reading is left alone**: the promise that `cat` is a
valid reader holds on every platform, and only appending requires POSIX.

**The newline at the end of a line is a separator, not part of the record.** A
file whose last line has no newline is accepted by the reader — the record in it
is intact, and an editor stripping the final newline is a common thing. Instead,
**the append fills in the missing separator before writing.** Without that, a
new record joins the tail of the last line, one physical line holds two objects,
and from then on no reader — `cat` included — can read that ledger (with no way
to recover). Restoring the separator is not an update (I1): not one byte of any
record changes.

**Only I7 and I12 are enforced by the store rather than by the fold.** Everything
else is decided from the lines alone, while these two require opening a snapshot
— and the fold is a pure function that does not look at the filesystem (§8).
Rather than give up one of the two, **the enforcement layer moved one level out**:
the store
is what holds the objects, so **every read that goes through the store** checks
the ledger's anchors against their own base snapshots. There is one
implementation of the rule (`ReviewStore._check_anchor`) and the write path
calls the same one — down to the same exception type, because the "two copies of
the checking logic" §6 warned about also holds as **two exceptions for one
condition**. When a snapshot **cannot be opened** (a missing object, a digest
mismatch), the ledger's claim is not wrong — the object store cannot answer — so
that is recorded as a separate error.

## 7. The disposition state model

```
(no disposition) ──deferred──► deferred ──applied/rejected/answered──► settled
      │                          │
      └──applied/rejected/answered─────────────────────► settled (re-disposition refused)
```

**Only `deferred` is non-terminal.** If deferring were terminal, a deferred item
would drop out of the "things to look at" list and there would be no reason for
the verdict to exist. So:

- **undisposed** = has no disposition ∪ the latest disposition is `deferred`
- `applied` · `rejected` · `answered` are final. Overturning one means a new
  comment in a new round (the past is not changed by editing the ledger)
- a disposition can be appended more than once and **the latest is the current
  state**. The whole history stays

### 7.1 The thread state model (G11)

```
(open) ──────resolve──────► resolved ──reopen──► (open) ──resolve──► resolved …
   ▲                            │                                        │
   └── reopen (no effect) ──────┘                   resolve (no effect) ─┘
```

The rule is the exact opposite of dispositions'. **A disposition refuses
re-disposition once settled (I5), and a thread does not refuse a re-declaration
of the same state (I10).** The reason is that the two events say different
things — handing a settled comment a different verdict **contradicts** a
recorded decision, while closing a closed thread says the same thing twice,
which is **agreement**. So a retry is not an error, and a misjudgement does not
become an accident.

**A duplicate line does not change the state but does stay in the history** —
whether from a hand-written ledger or two participants closing at once, the
reader accepts it, and who judged so and when reads back as written. The tool
side **does not make** that line (the same reason as §`anchor.reanchor`'s "no
change is not recorded"): when it is already in that state, it passes without
appending. The contract is "it is accepted" and the tool's choice is "it is not
written" — the two do not disagree.

**What resolved actually blocks is one thing, the reply** (I11). A disposition, a
re-anchor, and one more resolve all attach to a closed thread as before — those
are records about a comment, so even hidden from the view they still count in
the totals (`undisposed`, `orphans`). Only a reply is different: a reply is
written **to be read**, and under a hidden thread it loses that purpose. So
carrying the conversation on means opening it first with `thread.reopen` —
resolve is a conversation's ending, not a substitute for answering.

### 7.2 The three axes are independent, and so is their vocabulary

One comment can be in any combination of the three. **This table is the only
place the words are defined; everything that shows a number takes its wording
from here.**

| axis | what it asks | the word | fold | on the wire |
|---|---|---|---|---|
| disposition | has anyone decided this | **undisposed** | `State.undisposed`, `Comment.undisposed`, `State.undisposed_in()` | `undisposed`, `undisposed_count`, `undisposed_at_close` |
| anchor | can it still be placed on the document | **orphaned** | `State.orphans`, `Comment.orphaned` | `orphaned`, `orphans` |
| thread | is this conversation over | **resolved** / **unresolved** | `State.resolved_threads`, `State.active_threads`, `Comment.resolved` | `resolved`, `unresolved_threads`, `unresolved_thread_count` |

**"Unresolved" belongs to the thread axis and to nothing else.** It named the
disposition axis until 2026-08-08, one word away from the `resolve` verb, and
the collision cost exactly what a shared word costs: resolve a thread, watch
`round status` still say `1 unresolved`, and the tool reads as having ignored
the command when it was answering a different question. The count was right. The
word was borrowed.

Nothing kept the old spelling as an alias. `Comment.unresolved` and the wire's
`unresolved` key were removed rather than redefined, and `--allow-unresolved`
and `comments --unresolved` are refused rather than accepted — a reader still
using them gets an error it can see, instead of a number that quietly changed
which question it answers. There is **one exception, and it is on disk**: the
`round.close` record's own `unresolved` field (§4). Within a major the field set
is closed and an unknown key is refused whole (§2), so renaming it would not be
a rename — every ledger that used it would stop being readable. It keeps its v0
spelling and holds the undisposed set; the fold reads it into
`Round.undisposed_at_close`, which is what every reader above the ledger sees.
A v1 that bumps major is where the two agree again (§10).

**The default view hides resolved** (G11). Hiding is the view's job and not a
deletion — the ledger keeps everything, and so does the fold's `comments`. It
only drops out of listings, and a toggle brings it back.

## 8. Fold determinism

`fold` reads only the ledger to compute the present — the open rounds, the
undisposed comments. There is no copy of this state anywhere else (no second
copy to disagree with).

- It is a **pure function**. It does not look at the clock, at randomness, or at
  the filesystem. The same line order → the same state
- **Order is `seq`, `ts` is data.** A clock that jumps or runs backwards does not
  change the result
- The lines are authoritative, so state is not cached. Recomputing is always the
  answer
- Which is why **I7 is the store's business, not the fold's** (§6). Instead of
  shaving off purity to fit an invariant, the invariant moves up to the layer
  that has the objects — the fold folds a line it cannot judge as it is, and the
  read going through the store refuses. Snapshots are content-addressed and so
  immutable, which means that layer may remember the snapshots it read (caching
  a fact, not caching state).

## 9. A real ledger

Below is output the tool actually produced (not a hand-written example). In one
round it leaves a comment · a suggestion · a reply · an applied · a deferred · a
rejected · one undisposed, then closes and crosses two revisions — re-anchored
at the second, orphaned at the third. Finally two conversations are closed and
one of them reopened.

```jsonl
{"author":"alice","base":"sha256:35c081dd8b8aea1c491c9b6e76eb6ae8e7675e7cfceb679fc5ca2652ba8ff8e5","doc":"protocol.md","id":"r-59add8920c91","schema":"specround.ledger/v0","seq":0,"title":"round 1","ts":"2026-02-01T09:00:00Z","type":"round.open"}
{"anchor":{"end":42,"exact":"30 seconds","prefix":"# Widget protocol\n\nTimeouts are ","start":32,"suffix":". Retries are not specified yet."},"author":"bob","body":"too short for the proxy","id":"c-d35c1ebd2b14","round":"r-59add8920c91","schema":"specround.ledger/v0","seq":1,"ts":"2026-02-01T09:03:00Z","type":"comment.add"}
{"anchor":{"end":42,"exact":"30 seconds","prefix":"# Widget protocol\n\nTimeouts are ","start":32,"suffix":". Retries are not specified yet."},"author":"agent:reviewer","id":"s-086c5beb81f0","patch":"@@\n-Timeouts are 30 seconds.\n+Timeouts are 60 seconds.\n","round":"r-59add8920c91","schema":"specround.ledger/v0","seq":2,"ts":"2026-02-01T09:06:00Z","type":"suggestion.add"}
{"author":"alice","body":"the proxy caps at 45s","id":"p-1f41667adcfb","schema":"specround.ledger/v0","seq":3,"target":"c-d35c1ebd2b14","ts":"2026-02-01T09:09:00Z","type":"reply"}
{"author":"alice","id":"d-41174ba7d147","reason":"raised to 60 in revision 2","schema":"specround.ledger/v0","seq":4,"target":"c-d35c1ebd2b14","ts":"2026-02-01T09:12:00Z","type":"disposition","verdict":"applied"}
{"author":"bob","body":"retry policy is still missing","id":"c-7863abd8f91e","round":"r-59add8920c91","schema":"specround.ledger/v0","seq":5,"ts":"2026-02-01T09:15:00Z","type":"comment.add"}
{"author":"alice","id":"d-9fcf73b0ed21","reason":"waiting on the retry spec","schema":"specround.ledger/v0","seq":6,"target":"c-7863abd8f91e","ts":"2026-02-01T09:18:00Z","type":"disposition","verdict":"deferred"}
{"author":"alice","id":"d-5fa8d28ebf7d","reason":"superseded by the comment above","schema":"specround.ledger/v0","seq":7,"target":"s-086c5beb81f0","ts":"2026-02-01T09:21:00Z","type":"disposition","verdict":"rejected"}
{"author":"alice","id":"x-f0c5b47ca4e9","note":"retries move to round 2","round":"r-59add8920c91","schema":"specround.ledger/v0","seq":8,"ts":"2026-02-01T09:24:00Z","type":"round.close","unresolved":["c-7863abd8f91e"]}
{"anchor":{"end":42,"exact":"60 seconds","prefix":"# Widget protocol\n\nTimeouts are ","start":32,"suffix":". Retries are not specified yet."},"author":"agent:reanchor","base":"sha256:3c4fa2b65eaa07b84f7d1892bd487159215dd7bfd89171c8b3f0e518bd3dc7c9","id":"a-b06a968813bd","schema":"specround.ledger/v0","seq":9,"strategy":"fuzzy","target":"c-d35c1ebd2b14","ts":"2026-02-01T09:30:00Z","type":"anchor.reanchor"}
{"anchor":{"end":42,"exact":"60 seconds","prefix":"# Widget protocol\n\nTimeouts are ","start":32,"suffix":". Retries are not specified yet."},"author":"agent:reanchor","base":"sha256:3c4fa2b65eaa07b84f7d1892bd487159215dd7bfd89171c8b3f0e518bd3dc7c9","id":"a-03e8060eff2c","schema":"specround.ledger/v0","seq":10,"strategy":"fuzzy","target":"s-086c5beb81f0","ts":"2026-02-01T09:30:00Z","type":"anchor.reanchor"}
{"author":"agent:reanchor","base":"sha256:934db591c7f03e4dd37fca8750eb99403e89debcc09730413c7e7aba38e1f80e","id":"o-7762e917b458","reason":"quote '60 seconds' is not in the revised text, and no span reaches 0.70 similarity","schema":"specround.ledger/v0","seq":11,"target":"c-d35c1ebd2b14","ts":"2026-02-01T10:00:00Z","type":"anchor.orphan"}
{"author":"agent:reanchor","base":"sha256:934db591c7f03e4dd37fca8750eb99403e89debcc09730413c7e7aba38e1f80e","id":"o-c0c577e2d9db","reason":"quote '60 seconds' is not in the revised text, and no span reaches 0.70 similarity","schema":"specround.ledger/v0","seq":12,"target":"s-086c5beb81f0","ts":"2026-02-01T10:00:00Z","type":"anchor.orphan"}
{"actor":"agent","author":"agent:reviewer","id":"v-08bdcb2d3b60","note":"raised to 60 in revision 2, nothing left to discuss","schema":"specround.ledger/v0","seq":13,"target":"c-d35c1ebd2b14","ts":"2026-02-01T10:05:00Z","type":"thread.resolve"}
{"actor":"human","author":"alice","id":"v-6e5c72132f69","schema":"specround.ledger/v0","seq":14,"target":"s-086c5beb81f0","ts":"2026-02-01T10:06:00Z","type":"thread.resolve"}
{"actor":"human","author":"bob","id":"n-58caec7c2b7b","reason":"the patch still reads on the new wording","schema":"specround.ledger/v0","seq":15,"target":"s-086c5beb81f0","ts":"2026-02-01T10:07:00Z","type":"thread.reopen"}
```

Folding this ledger gives: 0 open rounds, 1 undisposed (`c-7863abd8f91e`,
`deferred`), 2 orphans (`c-d35c1ebd2b14`·`s-086c5beb81f0`), 1 closed thread
(`c-d35c1ebd2b14`). A deferred comment surviving past its round, and a comment
whose prose is gone staying as an orphan instead of quietly disappearing, is the
zero loss G3 talks about.

The three axes being independent is in this example too. `c-d35c1ebd2b14` is
**settled** as `applied`, is an **orphan** at the third revision (naturally —
applying it deleted the sentence), and its conversation is **closed** as well —
a case where all three axes happened to go the same way. `s-086c5beb81f0` is
settled as `rejected` and is an orphan, but its conversation is **open** (it was
closed and bob reopened it). `c-7863abd8f91e` is undisposed, has no anchor, and
its conversation is open. The default listing shows the latter two.

Who did the closing is in here as well: `v-08bdcb2d3b60` was closed by an agent
(`actor: agent`) and `v-6e5c72132f69` by a person. Going by `author` alone, one
is merely a name that reads `agent:reviewer`, so the two cannot be told apart.

The `strategy` on seq 9 and 10 being `fuzzy` is worth reading too — "30 seconds"
became "60 seconds", so the quote is not still there: **the prose was edited**,
which means it is worth a person's look.

Lines are canonical JSON, **key-sorted and without whitespace** (the same record
→ always the same bytes, which is what makes id derivation work and file
comparison meaningful). Non-ASCII text is not `\u`-escaped — a person has to be
able to read it with `cat` (G4).

## 10. What this format has not settled

Things with a place in the format but no rule. They are not dug on prediction;
they get filled when a decision is blocked.

- **H8, stale suggestions** — re-anchoring arrived, so **the machinery is
  there**: a suggestion's anchor crosses a revision exactly as a comment's does
  (§5.1). What is left is policy, not format — whether an old patch may be
  applied as-is to an anchor that moved, and whether a suggestion carried by
  `fuzzy` needs a person's confirmation. There is precedent for solving this
  with the re-anchor family rather than by freezing the round (Gerrit 3.11+).
- **The handling policy for orphans and ambiguous re-anchors** — the ledger only
  **reports**, through `anchor.orphan` and `ambiguous`. "Who looks, and when"
  now has an answer: the tail of the CLI's `comments` and the web view's thread
  list both flag orphans, `fuzzy`, and `ambiguous`, and whoever reads the list
  is that person. What is unsettled is **what to do** — whether to collect
  orphans somewhere, or to ask again in the next round. Flagging only, as it
  stands, is not a decision but something not yet done.
- ~~**H9, absorbing outside comments**~~ — **closed** (2026-08-07). The boundary
  is a separate format, `specround.import/v0` (`import-format.md`), and the only
  thing that grew on this ledger's side is `ext.import`: the origin is recorded
  as `{"source": …, "id": …, "ts": …}`, which makes re-importing idempotent. It
  is the second place to use the `ext` §2 reserved, and a promotion candidate —
  promotion bumps major. Neither a kind nor a field was added (an outside
  comment is just a `comment.add`).
- **Renaming `round.close`'s `unresolved` field to `undisposed`** — the word
  moved axes everywhere else on 2026-08-08 (§7.2), and this field is the last
  place where the spelling and the meaning disagree. It is deliberately left
  alone at v0: the field set is closed within a major (§2), so a rename here is
  not a rename but a break of every ledger that used it. It is a **v1
  candidate**, and a cheap one — a major bump already invalidates existing
  lines, so this costs nothing on top of whatever else bumps it.
- **Merging ledgers** — the rule for combining ledgers two people appended to
  separately (today it is one file · one directory · a lock). Sharing over git
  makes a sort-and-dedupe merge necessary.
- **Whether to lift `actor` into the envelope** — today it is on the two thread
  events only (§3). If the person/agent distinction bears on judgements in other
  events too then it belongs as a required envelope field, and since that
  invalidates every existing line it bumps major. What is needed first is not a
  prediction but a measurement of **which view actually uses the distinction**.
- **How a round closes a thread that is resolved but undisposed** — the format
  settled it (resolve does not enter `round.close`'s count, §4). What is
  unsettled is the flow: whether a thread that ended in agreement should be made
  to take an `answered` disposition, or whether close should show such threads
  separately, belongs to the CLI and the view.
