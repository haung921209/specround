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
  **⑤ With no round there is no anchor space, and the document is still read
  (landed 2026-08-08)**. ① is what a round decides, and until one exists there is
  nothing frozen to anchor in — so render and raw read the file on disk, read
  only, and the diff has no base to compare against and is not offered. This is
  not a second anchor space: nothing can be written in it (I4 blocks the two
  verbs, as it already did), and the moment a round exists ① applies again
  unchanged. It is written down because the first browse of a tree found the
  opposite — thirteen documents nobody had opened a round on came up **blank**,
  the two modes having no text to reach for. Blocking a comment was never a
  reason to withhold the text; "read only" and "unreadable" are not one answer.
  **⑥ How the page is arranged is the reader's, and it is not review state
  (landed 2026-08-08)**. The columns are resizable at both seams, either side
  column folds away (the coarsest control, and the one that matters in a narrow
  pane), and the document and the threads are read at a size the toolbar
  changes. All of it is kept in the browser under **one `localStorage` key
  holding one JSON object** — which survives a restart because the origin does:
  the port is derived from the document's path (§3 above). **It is deliberately
  not in the ledger.** G5's "the state is in the ledger" is about *review* state
  — rounds, comments, dispositions, resolutions: the things another reviewer,
  another machine, or a later reader has to agree with. How wide a column is on
  this screen is none of those. Recording it would put an entry in a history
  whose whole value is that everything in it is a claim about the document, and
  it would travel to a teammate whose screen is a different size. The narrow
  default is a **default and never a clamp**: under about 1100px the two side
  columns start folded, because 260 and 380 of chrome leave a document no wider
  than the bar beside it (measured in a terminal multiplexer's browser pane,
  which is the first-class consumer) — and a reviewer who opens a column at that
  width has said what they want, so the stored answer outranks the guess from
  then on. That is the whole of the small-screen share here; the rest of mobile
  is H16's.
  **⑦ A document's own files are served beside it (landed 2026-08-09)**. A spec
  with a screen capture in it is reviewable *against the thing it describes* — in
  a real round a capture caught a sentence of prose that was simply wrong about
  the screen it claimed to describe — and a view that answered 404 for
  `![](img/shot.png)` threw that away. The `data:` alternative is worse: tens of
  kilobytes of base64 in the document breaks the one mode whose job is to be the
  text. So the file is served, **behind the same token as everything else** — an
  image request is a request to read a file off this machine, and a browser
  making it while drawing a page does not make it a smaller one. Two directories
  and not one: a reference resolves against the **document's own directory**
  (what a relative path means everywhere else that reads one) and may not leave
  the **root** — that directory for a file view, the whole tree for a directory
  view, so `../shared/img/x.png` works between two documents of one reviewed tree
  and stops at its edge. **Symlinks are followed and then judged on where they
  land**: refusing every link would be the easier rule and the wrong one (a tree
  that keeps its captures in a linked folder is an ordinary tree), and the real
  path is what closes `img -> /` as a way out. Types are a whitelist of what a
  browser draws in an `<img>`, and **SVG is out of v1 on purpose** — opened
  directly it is a document that can run scripts on *this* origin, the one
  holding the token, and the defence (`script-src 'none'` and a sandbox beside
  it) is one that has to be right in a place nothing else here depends on. The
  four refusals — missing · outside · unsupported · too large — share one status
  and **never one reason**: a silent 404 for the misspelt name, the climb out,
  the `.svg` and the 40 MB PNG is a debugging session spent guessing. What this
  also settled is that the renderer had **no image rule at all** (`![a](src)`
  came out as a literal `!` plus a link), so the picture had to become an `<img>`
  before a route could answer for it; its label becomes `alt` and therefore
  leaves the anchor space, which is the honest trade — the same characters are
  ordinary anchorable text in the raw mode, and ① makes that one space.
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
- **Overturning a settled verdict = `supersede`, and deferring never needed it**
  (settled by the implementation 2026-08-08, from a user report). The report was
  that parking a point as `deferred` and completing it later got refused as a
  re-disposition. It does not: `deferred` is the one non-terminal verdict, so
  that path always worked and still takes no flag — the queue workflow was
  running against a rule that was never in its way. What *was* refused with no
  way through is overturning a terminal verdict, where the format's only answer
  had been "raise it again in a new round". A disposition may now carry
  `supersede: true`, and I5 becomes: a settled comment takes a second verdict
  **only** from a record that declares it, and a record declaring it when
  nothing is settled is refused just as hard — a flag that passes while
  describing nothing is a flag the caller goes on believing is in effect. It is
  a field rather than an `ext` key because the fold has to read it: a permission
  the reader ignores would pass its own write gate and then fail the next read.
  Append-only is untouched — the overturned verdict stays where it was written
  and only which one is in force moves.
- **A comment's disposition on the wire is `verdict` + `settled`** (settled by
  the implementation 2026-08-08). `state` was one string carrying the verdict
  with `"open"` for "nobody has decided", which answered two questions at once
  and made `"open"` the fourth thing here wearing that name. `undisposed` left
  the comment payload with it, because `settled` is its exact negation and one
  bit gets one key. The counts keep the negative spelling — `undisposed_count`
  and `undisposed_at_close` answer I6's "how many are still owed" — so §7.2's
  word for the axis is unchanged; what moved is that a single comment states its
  disposition positively now, like `orphaned` and `resolved` beside it. Both
  keys were removed rather than aliased, and a page reading a key the wire no
  longer sends is a failing test rather than an `undefined` that renders as
  nothing.
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
- **An anchor space is a round's base, and only `round.open` makes one (settled
  by the implementation 2026-08-08, from a measured failure)**. The ladder above
  says *how* a comment is carried; this says *into what*, which turned out to be
  a second definition nobody had written down. Re-anchoring took its target from
  the **document on disk**: it froze whatever the file was, cut anchors from
  that, and recorded them — each self-consistent, so I7 passed all of them, while
  every surface went on painting them over the round's base (①). On one real
  review **12 of 17 comments were drawn on sentences they were not about**, and
  the verification read the same revised text, so it passed too. Two definitions
  of "the anchor's space" were living in one field.
  Three things settle it:
  **① Opening a round is what carries.** Freezing the revision is what makes the
  space, so it is what moves the comments into it. Not a step to remember, and
  therefore not one to skip — the ladder runs where the space is made or nowhere.
  **② `reanchor` may not invent a space.** It re-drives the carry onto the base
  the document is painted on, and is **refused** (exit 3) once the file has moved
  past it, naming the two ways out (open a round on the revision · nothing to
  move against this base). The old behaviour is unreachable rather than
  discouraged.
  **③ I12 is reported, not refused.** A comment whose `current_anchor` does not
  hold in the base it is painted on is marked and **not drawn**, because a mark
  on the wrong sentence reads like a correct answer while a missing one with a
  badge beside it does not. Refusing would cost more than the bug: ledgers
  written before this rule exist, and a fold that raised on one would take away
  the only way to read or repair it. `specround doctor` is the repair — the quote
  re-read in the right base, the correction **appended** (I1), the bad record
  left where it is.
  What was rejected: making the view follow the anchors instead. That is the
  second definition winning, and it breaks both I7 (a comment is a review of the
  text the reviewer read) and G2 (a round's base is frozen).
  Details in `docs/ledger-format.md` §5.2.
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
- **Integration surfaces are the formats, never the page's HTTP API (user
  decision 2026-08-09, contract-first)**. What an adapter may depend on is
  named in `docs/integrating.md`: the ledger and store layout (`ledger/v0`),
  the CLI `--json` envelope (`cli/v0`), the exit codes, `view`'s
  URL-first-line stdout, and `import/v0`. The web view's `/api/*` is internal
  and unversioned — a consumer that needs it promotes it to `api/v0` first.
  Splitting our own adapter into its own repository was considered and
  deferred: its body is machine-local glue, the interfaces are days old, and
  the trigger for extraction is a second independent consumer, not a
  prediction. The generic piece worth sharing goes to `adapters/` as a
  reference, the shape `cmux-diff-comments.py` already set.

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
- H11 generalizing the file type (user suggestion 2026-08-06; ladder assessed
  2026-08-10) — take the raw/render pair beyond markdown: per-type renderers
  in a plugin layer, and arbitrary artifacts ride the same review loop.
  Anchors, the ledger, rounds, and the CLI are already type-agnostic — a code
  file takes a round, comments, and line-gutter clicks today; only the
  **render view** varies by type. The difficulty ladder: **rung 0** any text
  via raw mode (already works) · **rung 1** code with offset-preserving
  syntax highlight (spans wrapped around unchanged text — the `_link`
  discipline, cheap) · **rung 2** a whitelist of inline HTML inside markdown
  (`<details>` and kin — small, separate) · **rung 3** HTML documents
  (source-offset-mapped render is the same craft as markdown.py; the real
  cost is **security** — foreign HTML in the token-holding origin is the
  class that kept SVG out of v1, so it needs iframe-sandbox/CSP isolation
  done precisely). This is not "become a PR tool" (the non-goal stands): it
  is "any artifact, same loop". Until rung 2 exists, documents reviewed here
  should read without folding — a `<details>` that hides a contract hides it
  from the review. Trigger per rung = real demand, not prediction
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
- H16 reaching the view from another device (user, thinking aloud 2026-08-08 —
  "edit and submit from a phone"). Three separable pieces, and only one of
  them is new. **Submitting from the page already exists** (G8 — a raw-mode
  edit is taken in as a suggestion; the gutter takes comments); what a phone
  lacks is *reach*, and reach is a **transport question, not a mode**: the
  server binds loopback by design, and letting one trusted device in is a
  `--host` opt-in on a private network (tailnet/LAN) with the token and the
  Origin check it already has. The non-goal stands — this is not hosting, no
  accounts, one reviewer's own devices. Held open until a second device is
  actually used: the exposure step deserves its own look (token lifetime,
  HTTPS or not on a tailnet), and mobile ergonomics (touch selection → gutter)
  is real work that should be pulled by use, not predicted.
  Axes named while thinking it through (2026-08-08): **getting the URL onto
  the phone is the real friction** — a `--qr` that prints a scannable code is
  cheap and enough; **server lifetime is the real decision** — phone-as-second-
  screen (the desk session serves, philosophy unchanged) versus an always-on
  daemon (reachable any time, but the ephemeral-process contract and its
  reclamation would have to be rewritten) — second screen first, daemon only
  if "no session was up and I reached for the phone" is actually observed;
  **line-number tap is the primary mobile gesture** (touch drag-selection is
  poor — the gutter tap already built carries mobile); and a passively
  updating thread view would add a polling/SSE question that does not exist
  on the desk
- H17 mapping a round to its consuming session (user, thinking aloud
  2026-08-08). Today the agent *pulls* ("collect when asked"); the idea is a
  submitted suggestion or comment finding the session that is working on that
  document. The unit, if this lands, is **round ↔ consuming session** — a
  round is one conversation, and its consumer is whoever opened it to collect.
  The core's share is at most an **observation** (an `ext` note on round.open
  naming the consumer, like H12 records git state — no dependency, no
  judgement on it); the *push* — noticing the event and nudging the mapped
  session — is the adapter layer's job (the same events-and-notify machinery
  that surfaces worker questions), because a channel to a live session is
  exactly what a serverless core does not have (G5). No separate "mode":
  modes multiply surfaces, and both halves are additions to surfaces that
  already exist

## 5. Done-ness (of the spec stage)

Once H5 research disproves "it already exists", a comment round has run at least
once, and every item is attributed to G1–G11, the spec stage closes and
implementation starts. The first real customer is this tool's own spec review.
