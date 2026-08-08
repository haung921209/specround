# specround — spec review rounds (human ↔ agent)

> A review loop between a person and an agent with a spec document between them.
> Comments survive revisions, edits become suggestion diffs, and every
> disposition lands in an append-only ledger. No server.

status: **implementation landed — ledger core + CLI + the three-mode web view
(654 green).** The first real review round ran on this document
(r-47878bb614f8, 2026-08-07 — three comments, thread replies, and resolve, all
through this tool).

## 1. Why — the friction, as measured

Two days of trading spec-document reviews with an agent produced this:

| friction | cause |
|---|---|
| every revision needs a fresh diff tab | a diff view is a snapshot, so it goes stale along with the revision |
| comments already dealt with get harvested again | harvesting does not consume — there is no disposition state |
| a new document cannot take line comments | comments are parasitic on the diff view (a staging trick is needed) |
| the answer to "what happened to that comment" lives only in the chat | there is no disposition ledger |
| a revision kills comment anchors | the anchor is a line number |
| getting one comment needs a staging/commit ceremony | review's version axis is coupled to git commits |

## 2. Guarantees

| id | guarantee |
|---|---|
| G1 | A comment lives on a **document anchor**, not on a diff snapshot — it survives revisions (re-anchor) |
| G2 | Review happens in **rounds** — the base is not a commit but a **document snapshot the tool froze**, and the round stays in the record |
| G3 | Every comment has a **disposition** (applied/rejected/answered/deferred) — zero loss, an append-only ledger |
| G4 | **An agent is a first-class participant** — harvesting, disposing, and replying are machine-doable (structured output), on the same channel a person uses |
| G5 | **No server, no git** — the ledger and the snapshots are plain append-only files in a **central home store** (the default). The document's repo stays uncontaminated — zero untracked noise, zero gitignore homework. A team that needs to share **opts in** to a store inside the repo |
| G6 | Input-surface neutral — a comment can be made in the rendered view or on the raw document, and both converge on the same ledger |
| G7 | One command from the shell opens it — review's entry point is the CLI |
| G8 | A reviewer can **edit**, not only comment — an edit is taken in as a **suggestion diff** attached to an anchor |
| G9 | A suggestion diff arrives on a **channel an agent can collect from** — apply or reject, plus a reason, lands in the disposition ledger |
| G10 | **Communicating does not force a commit** — no step of the review loop asks for a stage or a commit. A commit is where the result (the document) lands, never the ticket for exchanging feedback |
| G11 | **A conversation that is over is closed with resolve** — when a comment thread wraps up (applied, rejected, or simply agreed), a person and an agent can both resolve it, and resolved threads are **hidden from the default view**. Hiding is the view's job, not a deletion — the ledger keeps all of it (no contradiction with G3's zero loss) |

Non-goals: real-time co-editing · WYSIWYG · a hosted service · code review (the
target is spec and prose documents — PR tools already do code well).

## 3. Decided

- **License = MIT** (settled 2026-08-06)
- **Public default language = English** (user decision 2026-08-06, in a CLI
  review comment): the default for user-facing documents such as the README is
  English. Korean goes in `-ko` appendices. Translating this spec and the format
  docs was handed to the `docs-english` item (carried out 2026-08-07).
- **An unresolved thread carries on through reply** (user decision 2026-08-06,
  in a resolve review comment, **settled by the implementation 2026-08-06**):
  before resolve, a comment is a conversation a person and an agent go back and
  forth on with reply events — it is already in the ledger, and the CLI
  `reply`/`resolve`/`reopen` verbs plus the thread render are the `thread-reply`
  item. Resolve is that conversation's ending, not a substitute for answering.
  One thing the implementation settled: **a resolved thread cannot be replied
  to** (I11 — reopen comes first). Resolved is hidden from the default view, so
  a reply underneath it is an answer that got recorded and does not get read,
  and that is the loss G3 prevents. It is the thread model's only refusal
  (dispositions, re-anchors, and a repeated resolve all attach to a closed
  thread as before) and it is where the tool enforces "resolve is not a
  substitute for answering". Details in `docs/ledger-format.md` §4·§6 (I11).
- **Ledger = jsonl + document snapshots — no git (H3 closed, user decision
  2026-08-06)**. Opening a round freezes the document's current content into the
  ledger, and diff mode is computed **against that snapshot** rather than
  against git (the same family as an agent CLI that keeps its session record in
  jsonl — the tool owns the version axis).
- **Store location = central home, keyed by document path (user decision
  2026-08-06 — v0 review comment)**. A `.specround/` beside the document is
  rejected: inside a git tree it becomes untracked noise and the user has to
  handle gitignore one entry at a time, which makes "free of git" (G5·G10) a
  lie. The default is the home store, keyed by the document's absolute path
  (hashed) — the same model as an agent CLI's session storage. Only when a team
  needs to share does the store opt into the repo.
  git notes are out because they are git coupling. This is exactly where we part
  from the PR/Gerrit family: their review target is a commit, so coupling
  feedback to commits is inevitable, but ours is a document — and a document has
  to work fully outside git (untracked, non-repo).

- **Core + CLI = Python** (shipped with uv/uvx). The axis is that "the contract
  is in the **format**, not in the language" (G5 — the ledger schema is the
  contract), which makes the language a light decision we can change later.
- **View = an ephemeral local web view** — the browser is the GUI everybody
  already has. **Three modes: render · raw · round diff (against the base)**
  toggle on one page, and a comment made in any mode converges on the **same
  document anchor** (the diff-line → anchor conversion is the same re-anchor
  machine G1 needs). It is not hosting — it is a local process, and its state
  lives only in the ledger (no conflict with G5).
  Rationale: the combination "render + gutter comments + diff" is impossible in
  a native shell — each part alone works, one-screen toggling plus commenting
  from any view does not.
  **Settled by the implementation 2026-08-06.** Two things the implementation
  settled:
  **① The anchor space is the round's base in all three modes.** The text
  render and raw show is not the file on disk but the snapshot that round froze,
  and the fact that the file has moved ahead is what diff mode reveals. This is
  not a choice but what I7 decided — a comment's anchor is verified against its
  own round's base, because that snapshot is what this round is a review of.
  **② A line only the revision has is refused, not guessed at.** Selecting an
  added line in the diff runs the ladder **backwards** (cut from the revision,
  found in the base) to get a base anchor, and when nothing is found it is a
  **refusal**, not an orphan — a comment is about to be written, so there is no
  place to record an orphan. The two ways out go in the wording instead
  (comment on the whole document · open a new round on the revision). When
  `fuzzy` or `quote` carried it over, which rung it came through is recorded in
  `ext.view` — the field set is closed and has no room for it, and without it
  the comment is indistinguishable from one picked directly on the base (the
  reason §4 has `fuzzy` be something a person looks at).
  H8 (stale suggestions) **never became necessary** — the view only takes
  suggestions in, it does not apply them.
  **③ Clicking a line number = that line's span (came in from the first real
  round, 2026-08-07)**. Clicking a line number in raw or diff makes that line
  the span — the **same anchor** as dragging across it (one line is one run, so
  the arithmetic is the same). It creates no new space and no new refusal: a
  revision-only line goes to ②'s refusal above, and an empty line is a
  zero-width span = an insertion point (§5). It is the gesture the user reached
  for before span selection ("this line is off" makes dragging expensive), and
  render mode has no line numbers, so it is not a target.
  **④ A directory is one server, and the bar is navigation only (H15, landed
  2026-08-07)**. `view <dir>` serves the tree from one process, with a bar
  listing the markdown under it and what review each document has. Every
  request names the document it is about and gets back the *same per-document
  projection* a file view gives: no workspace round, no shared anchor space, no
  second definition of "undisposed". The selection is the **caller's, never the
  server's** — a process that remembered which document was open would be
  state, which the view does not have (that is also what lets two tabs on one
  port read two documents). One store per document throughout, so the bar reads
  each document's own history rather than merging them. What the walk holds
  back is counted and said; a mistyped or stale key is refused rather than
  resolved to some other file's history.
- **vim and other editors are first-class without a plugin** — fix the raw text
  in an editor and the working-tree diff is taken in as a suggestion (G8, "fix
  it and it is submitted"), and type inline annotations (the CriticMarkup
  family) into the raw text and the harvester absorbs them as comments (G6). A
  dedicated plugin is a later adapter.
  **Settled by the implementation 2026-08-07** (`specround harvest`, four forms
  `{>>…<<}`·`{++…++}`·`{--…--}`·`{~~…~>…~~}`). Three things the implementation
  settled:
  **① The harvested text is the document, and a marker is a proposal about it**
  — `{--x--}` leaves `x` in the file and records a removal proposal in the
  ledger, and `{++x++}` does not put it in the file (it is text that is not
  there yet). Reading a file must not quietly **apply** a proposal; applying is
  a disposition (`applied`).
  **② The anchor basis is the text with the markers gone**, and the two real
  workflows are exact by arithmetic — annotate after opening a round and
  removing the markers restores the base; open a round on an
  already-annotated document and the span sits inside the marker (three
  characters right of the opener). Only when the prose moved as well does the
  re-anchor ladder run backwards (§5.1 — no second matcher gets built). Why
  that second case is not an optimization: without it every insertion point
  with another marker near it becomes an orphan (the ordinary shape of a
  reviewed paragraph), and the way out the refusal names would not actually
  work.
  **③ One marker that cannot be placed refuses the whole harvest** (in a dry
  run too) — leaving one marker in shifts every offset after it, so "harvest
  the rest" is not a smaller version of this operation. An unclosed opener and
  the unsupported fifth form (`{==…==}`), by contrast, are **left in the file
  and reported**: dropping them quietly is the loss G3 prevents, and neither one
  is even certainly an annotation.
  A dry run is the default because this verb rewrites the file, and `--apply` is
  the gate.
  **④ Markers inside code are specimens, not instructions** — fenced blocks and
  inline backtick spans are not read (`markdown.code_spans`). This is not a
  syntax extension but a matter of **recognition scope**, and without it a
  document explaining its own syntax gets damaged by its own tool. The evidence
  is measured: before the rule, harvesting this repo's `SPEC.md` read eight of
  its own specimens as review comments and deleted that text from the prose,
  `README.md` six, and `docs/research/prior-art.md` **could not be harvested at
  all** because of the `{~~old~>new~~}` line in its syntax table (refused on the
  empty substitution). G5's "the first real customer is this tool's own spec
  review" needs this rule to hold. The test was narrowed to something a person
  can apply by eye ("is this between backticks on this line") — miss it and a
  specimen gets harvested but the dry run shows it, whereas over-catching
  swallows a real annotation quietly. Computing the scope belongs to the
  document type, so it reaches the parser as offsets (H11 puts a renderer in
  that place).
- **resolve / reopen = ledger events** (user decision 2026-08-06, **settled by
  the implementation 2026-08-06**). Ending a thread is a different axis from
  disposition (G3) — a disposition is one comment's outcome (applied/rejected/
  answered/deferred), and resolve is the thread state "this conversation is
  over". Who closed it (a person or an agent) is recorded on the event, and a
  wrong close is an appended reopen (natural, since this is append-only). Every
  view (the web view's three modes, the CLI listings) hides resolved by default
  and shows it on a toggle.
  Three things the implementation settled: **a thread = a root comment + the
  chain of replies**, and because replies are flat the root id is the thread id
  with no separate object · **re-declaring is idempotent** (a disposition
  refuses re-disposition, a thread does not — a different verdict is a
  contradiction, the same declaration is agreement) · **`round.close`'s
  undisposed count does not look at resolve** (if it did, closing a
  conversation would become a way to walk past an undisposed comment quietly).
  Details in `docs/ledger-format.md` §4·§7.1.
- **The two axes get two words: `undisposed` and `unresolved`** (settled by the
  implementation 2026-08-08, from a user report). The count above was right and
  its name was not: it was called "unresolved", one word away from the `resolve`
  verb, so resolving a thread and watching `round status` hold at `1 unresolved`
  read as the tool ignoring the command. The disposition axis is **undisposed**
  wherever it is shown or projected; **unresolved** means the thread axis and
  nothing else, and `round status` reports both numbers. Nothing keeps the old
  spelling as an alias — a stale reader gets a missing key or a refused flag,
  never a value that silently changed which question it answers. The one place
  the words still disagree is the `round.close` record's own `unresolved` field,
  which is frozen bytes at ledger/v0 and a v1 rename candidate.
  Vocabulary table in `docs/ledger-format.md` §7.2.
- **suggestion** = a comment whose body is a patch (`kind: comment |
  suggestion`). When an agent harvests, a comment gets answered or applied and a
  suggestion gets applied or rejected — either way the disposition and its
  reason are appended to the ledger.
- **Anchor survival = a four-rung ladder + orphans kept (H4 closed, settled by
  the implementation 2026-08-06)**. Search the revision in the order
  `position → quote → normalized → fuzzy`, and whichever rung answers, cut the
  result out of the revision again to verify it. When nothing is found, the
  comment is **recorded as an orphan rather than guessed into place** — that no
  comment disappears quietly is where G1 and G3 meet. Re-anchors and orphans do
  not overwrite the anchor: they stay as **new ledger events**
  (`anchor.reanchor`/`anchor.orphan`), so where a comment went in which revision
  reads as history. Details in `docs/ledger-format.md` §5.1.
- **Absorbing outside comments = the format is the boundary (H9 closed, settled
  by the implementation 2026-08-07)**. `specround import <doc> --file <json>`
  reads one **documented general contract** (`specround.import/v0`,
  `docs/import-format.md`) and nothing else — the core has no code that knows
  cmux, and a per-tool converter lives outside the package in `adapters/` and
  emits only that file. If the core knew one viewer's storage, the core would
  change every time a viewer was added. Three things the implementation settled:
  **an item quotes the text it is talking about** (offsets alone are not
  accepted — an offset with nothing to check comments on whatever is at that
  place now) · **the origin is recorded in `ext.import` and `(source, id)` is
  the idempotency key** (put the same file in twice and it goes in once) ·
  **a refusal is per item and the exit code is 0** (the shape `reanchor` already
  has for a comment it could not place — one paragraph moving does not make the
  other twenty a failure). A dry run is the default and `--apply` records.

## 4. Open (deepen when a decision is blocked — do not dig on prediction)

- H5 prior-art research: git-appraise · git-bug · reviewdog · CriticMarkup ·
  Hypothesis — **do not rebuild what exists.** The differentiator hypothesis =
  the combination "spec prose + agents first-class + anchor survival +
  render/raw/diff in three modes"
- H8 stale suggestion diffs — **the machinery is there via H4** (a suggestion's
  anchor crosses revisions along with the comments). What is left is policy:
  whether an old patch may be applied as-is to an anchor that moved, and whether
  a suggestion carried by `fuzzy` needs a person's confirmation. Round locking
  (freezing head while suggestions are open) is later for that reason
- H11 generalizing the file type (user suggestion 2026-08-06) — take the
  raw/render pair beyond markdown: put per-type renderers in a plugin layer
  (code, tabular data, notebooks, images…) and arbitrary artifacts such as
  onboarding docs or spec notes ride the same review loop. Anchors, the ledger,
  and rounds are type-agnostic, so the core is unchanged. Trigger = after the
  markdown loop has settled in real use (do not build it before)
- H10 a central store's path key orphans the history when a document is moved or
  renamed — **direction settled (first real round c-b5c77df9, 2026-08-07)**:
  re-binding in two layers. The authoritative oracle = **content-hash
  comparison** (if the store's snapshot matches the moved file's content it is
  the same document — works without git), the accelerator = **git rename
  detection** (inside a repo, `git log --follow` offers move candidates; an
  opt-in hint only, never authoritative — G5 holds). What is left: the verb name
  (`mv`/`relink`) and when to offer it. The live case = this very round, whose
  key is a worktree path that a worktree reclaim orphans
- H12 recording git state as an observation (first real round c-e725500f,
  2026-08-07) — when the document is inside a git repo, record {HEAD commit,
  dirty or not} on round open/close events as `ext`. **It is an observation, not
  a dependency** (G5 — without git it is simply a blank, and no judgement hangs
  on this field). "Was it committed later, after a reclaim" stays answerable
  after the fact by comparing the snapshot sha against the git blob sha
- H13 store lifetime management (first real round c-b04090ca, 2026-08-07) —
  unbounded accumulation is right for the ledger (tens of KB per document). The
  principle conflict: append-only vs GC → **archive, not delete**. ① `store
  status` surfaces size, documents, and last activity ② `store archive` folds
  only "path gone + N days idle" into a tar beside it (dry run by default,
  `--apply` gate). No automatic GC — history disappearing quietly is the very
  class this tool exists to prevent
- H14 extending the inline annotation syntax (harvester implementation
  2026-08-07) — four forms closed the loop and the rest gets dug **when it
  becomes necessary**. The places held open: **nesting** (today each opener
  closes at the first close of its own form, so a marker inside a comment body
  is a literal) · **`{==highlight==}`** (CriticMarkup's way of naming the span a
  comment points at — for now a comment is a zero-width anchor and the 32
  characters of leading context play that part) · **tying an adjacent comment to
  an edit as its reason** (`{--x--}{>>why<<}` is two events today — tying them
  is guessing at intent, so it is not done; the offsets reveal the adjacency) ·
  **merging `ext.view`/`ext.harvest`** (the inside shape is the same — merging
  is a field promotion and bumps major, `docs/ledger-format.md` §2)
- H15 directory view — **direction settled (user, 2026-08-07)**: a spec is
  never one file, and one server per file is a real burden, so `view` accepts
  a **directory** — one server for the tree, a navigation bar on the left
  listing the files, documents with review activity marked (open rounds,
  undisposed counts) and a filter that shows only them. Clicking a file swaps
  the main panel to the existing per-document three-mode view — the workspace
  layer is navigation only; rounds, anchors, and the ledger stay per-document
  axes and the core does not change. Several servers may overlap the same
  file (a directory server and a file server, say) — that is fine, because
  servers hold no state of their own and every write folds into the same
  ledger; what has to be managed is reclamation, and it already is (the
  state-file / process-group machinery).
  **Landed 2026-08-07** — the decisions the implementation settled are §3 ④.
  Two the direction did not name: the tree opens on the **first document in
  path order**, because the only thing that could rank "most recently active"
  is a timestamp and this project's timestamps order nothing; and the listing's
  limit holds back documents with **no** review activity first, never one that
  has some, or the filter would be lying about the set it was asked to find

## 5. Done-ness (of the spec stage)

Once H5 research disproves "it already exists", a comment round has run at least
once, and every item is attributed to G1–G11, the spec stage closes and
implementation starts. The first real customer is this tool's own spec review.
