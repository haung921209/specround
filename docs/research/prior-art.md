# Prior-art research (H5) — trying to disprove "it already exists"

Surveyed 2026-08-06. The frame is disproof: we are already leaning toward
building, so the attempt was to prove that an existing tool already satisfies the
G1–G9 combination (SPEC.md §2). Generous reading — reachable by combination or
configuration counted as covered. Method: parallel web survey along four axes
(git-native distributed review / review platforms and bots / prose annotation and
anchoring standards / agent-first new tools), with activity measured against the
GitHub API and release pages. About 25 tools surveyed in depth plus a 15-tool
sweep around them.

Notation: **O** = covers · **△** = partial (including reachable by combination or
configuration) · **X** = does not cover.

## 0. The conclusion first

**Overall verdict = (c) partial.** Neither a single tool nor a realistic
combination covers the whole guarantee set — the disproof failed, and where it
failed is the differentiator:

- **The differentiator = G1 (prose re-anchor) × G2 (rounds against a base
  commit) × G3 (append-only disposition ledger) × G5 (no server, git-only) × G6
  (render/raw surfaces converging), satisfied at once.** No tool covers all five
  together, and any combination patched together splits the ledger (a sidecar
  JSON / inline marks / a platform server) and breaks G3's "zero loss".
- The remaining guarantees (G4·G7·G8·G9) all have precedent — this is not
  invention but **assembling parts** (§4.3).
- Taken one G at a time, every guarantee exists somewhere. G1 = Hypothesis/Gerrit
  ported comments, G2 = Gerrit patchsets, half of G3 = Reviewable's dispositions,
  G4 = md-redline/markdown-review MCP, G5 = git-appraise/git-bug, G8 =
  CriticMarkup/GitHub suggestions. **The combination is what is new.**
- **Risk (stated honestly)**: this category is filling up fast right now — nine
  new tools appeared between 2025-06 and 2026-05 (md-redline · Plannotator 7.5k★
  · spec-workflow-mcp 4.3k★). Within 6–12 months it is plausible that a tool
  above us absorbs the round and ledger axes — so build it soon, and make the
  ledger format (our decision that the contract is in the format) the axis of
  differentiation.

## 1. The verdict matrix

| tool | G1 anchor survival | G2 rounds | G3 disposition ledger | G4 agents | G5 git-only | G6 surface-neutral | G7 CLI | G8 suggestion diff | G9 collect + dispose | activity | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Gerrit** | O | O | △ | O | △ (data only) | X | △ | O | O | very active | partial component (the strongest disproof) |
| **GitHub PR (+gh/prr)** | △ | △ | △ | O | X | X | O | O | △ | continuous | partial substitute (the best off-the-shelf answer) |
| **Reviewable** | O | O | O (semantics) | O | X | △ | △ | △ | △ | very active | partial component |
| **md-redline** | △ | X | △ | O | O | △ | O | X | X | new · active | near substitute |
| **Plannotator** | △ | △ | △ | O | X | △ | O | O | △ | 7.5k★ · active | partial component |
| **spec-workflow-mcp** | △ | △ | △ | O | △ | X | △ | X | X | 4.3k★ · maintenance risk | partial component |
| **git-appraise** | X | △ | △ | O | O | X | O | X | X | dormant 3 years | partial component (ledger precedent) |
| **Radicle** | X | O | O/△ | O | △ | △ | O | △ | X | very active | partial component |
| **git-bug** | X | X | △ | O | O | △ | O | X | X | active | component (ledger format) |
| **prr** | X/△ | △ | △ | △ | X | △ | O | △/O | △ | low-intensity | component (raw-surface syntax) |
| **reviewdog** | X | △ | X | △ | X | X | △ | △ | △ | active | component (exchange format) |
| **CriticMarkup (+the Obsidian family)** | O | △ | △ | △ | O | X | X | O | △ | spec dormant · ecosystem alive | partial component (syntax) |
| **Hypothesis** | O (DOM) | X | X | △ | X | X | X | X | X | active | component (the authority for H4) |
| **W3C Web Annotation** | O (schema) | — | △ (schema) | O (schema) | compatible | — | — | — | — | REC settled · stable | component (anchor schema) |
| **Commentary** | O | X | X | △ | △ | X | X | X | X | new · very early | component (md anchor survival, demonstrated) |
| **markdown-review** | △ | X | △ | O | △ | X~△ | X | X | X | new · very early | partial substitute |
| **md-annotator** | X~△ | △ | X | O | X | X | O | △ | △ | new · very active | partial substitute |
| **md-review-tool** | X | △ | X | △ | O | X | X | O | △ | new · very early | component (an approximation of a recorded round) |
| **md-review-plus** | △ | △ | △ | O | X | X | O | △ | △ | new · very early | component (CLI contract) |
| **graft** | O | X | X | X | O | X | X | X | X | stalled · 5★ | component (the only re-anchor + git pairing) |
| **CodeRabbit/Greptile** | X | X | X | △ | X | X | △ | △ | △ | very active | unrelated (a participant, not infrastructure) |
| **spec-kit / OpenSpec** | X | X | X | △ | O | X | O | X | X | 125k★/64k★ | unrelated (no review loop) |

## 2. Tool by tool

### 2.1 Direct competition — human ↔ agent markdown review loops (new, 2025–2026)

**md-redline** (dejuknow/md-redline · 30★ · created 2026-03, pushed 2026-07-30)
Inline review for markdown only. Select and comment in a local web view, and the
comment is stored **in the md file itself** as a `<!-- @comment{...} -->` HTML
marker (no sidecar, no DB → G5 O). Four MCP tools
(`mdr_request_review`/`mdr_review`/`mdr_ask`/`mdr_wait`) let an agent request a
review and even ask a blocking question (G4 O). One shot with `mdr spec.md`
(G7 O).
What it does not cover: no rounds (G2 X); resolving deletes the marker, so the
history leaves the document — the ledger is only git commits (G3 △); no
suggestion diffs (G8/G9 X); no automatic re-anchoring (a manual drag handle,
G1 △).
Verdict: **near substitute — the closest single tool in this survey.** The core
loop, "an agent requests a human review over MCP and the comments live in the
file and survive in git", is already shipped. But three axes are empty: the
disposition ledger, rounds, and suggestion diffs.

**Plannotator** (backnotprop/plannotator · 7,542★ · created 2025-12, still being
pushed on 2026-08-06) A local review surface for annotating and approving agent
plans, markdown, and diffs in the browser. It steps in automatically through
Claude Code's `ExitPlanMode` hook, replies with approve/deny plus structured
feedback (G4 O), and does inline suggestion and redline markup on diffs (G8 O).
What it does not cover: **storage is `~/.plannotator` (outside the repo) — a
structural G5 failure** (a clone is not the review history); rounds are not
against a base commit; no append-only ledger.
Verdict: **partial component.** Evidence that this category's demand is
validated at 7.5k★, and the tool a newcomer has to explain itself against. The
design of the hook intervention point is worth borrowing.

**spec-workflow-mcp** (Pimzino · 4,275★ · GPL-3.0 · the README says the "author
[is] taking a break") A spec-driven MCP server plus a web dashboard. The agent
produces requirements/design/tasks and a person leaves **text-highlight anchored
comments** plus an Approve/Request Changes/Reject disposition (three, made
explicit). The data is files in `.spec-workflow/` inside the repo.
What it does not cover: the dashboard needs a server process (G5 △); approval is
per snapshot (no contract for surviving revisions); append-only is unclear
(approvals JSON looks overwritten); no reviewer editing (G8/G9 X).
Verdict: **partial component.** Existence proof for the combination "spec
approval rounds + anchored comments + storage inside the repo".

**md-annotator** (konradmichalik · created 2026-01, pushed 2026-08-04) — the loop
of annotate in the render view → agent applies → reopen the round is built into
the workflow (G4/G7 O). Storage is a local server session (outside the repo,
G5 X) and there is no disposition ledger. Verdict: partial substitute —
evidence that specround's UX core is already under active development.

**markdown-review** (jinqishen0725 · VS Code · 2★) — **commits a sidecar JSON to
the repo** and, with twelve Copilot tools plus MCP, lets an agent reply and
resolve on the same channel a person uses (a real case that satisfies G4 to the
letter). No rounds, no suggestion diffs, no CLI, and not append-only (JSON
read-modify-write). Verdict: partial substitute.

**Commentary** (jaredhughes · VS Code · 2★) — comments on the render preview,
with **three-layer anchor fallback (exact quote + 100 characters of context each
side → character offsets → nearest heading + fuzzy)** already implemented over a
markdown sidecar (G1 O — evidence that "the hard part is not hard"). No
dispositions, ledger, or rounds at all, and only one-way emission to an agent.
Verdict: component.

**md-review-tool** (LetitiaChan · VS Code · 4★) — on detecting a file change it
preserves review versions (v1/v2) in `.review/` automatically — **the only tool
in the whole survey where a round stays in the record** (though the base is a
file change, not a commit). Verdict: component (a design reference for G2).

**md-review-plus** (Seiraiyu · 2★) — an agent pipes md in and a person disposes
in the browser (approve/reject/pending), asks blocking questions, and edits
suggestions inline → **structured markdown on stdout, exit code 0/1**. Storage
is the session (G5 X). Verdict: component (a blocking CLI review contract).

### 2.2 Code review infrastructure — git-native and platforms

**Gerrit** (3.13.1, 2025-11 · very active · the 4.0 roadmap is under way) — **the
strongest disproof.** All review metadata (comments, votes, resolved) is stored
in NoteDb = **refs in the same git repo as the code**
(`refs/changes/{id}/meta`, JSON in a NoteMap keyed by patchset SHA) — the data
layer is 100% git (clone + fetch refs = the whole history). **"Ported comments"**
carries unresolved comments from an old patchset to the right place in a new
revision — exactly the re-anchoring specround intends to build (G1 O). A patchset
is a round (G2 O); "suggest fix" plus the structured `fix_suggestions` field plus
the REST "Apply Stored Fixes" (G8/G9 O); a bot is an ordinary account with the
whole REST surface (G4 O).
What it does not cover: **running it requires a Java server daemon** (oversized
for a personal spec review — it fails G5's "no server"); review is only for
source diff lines, with no rendered markdown surface (G6 X); disposition is a
binary resolve (no kinds).
Verdict: **partial component.** "The heart of the guarantees (G1 re-anchoring,
G2, and G8/G9 structured suggestions with machine apply) is all implemented by
Gerrit already, and stored in git at that. The only things left to invent are
removing the server and adding a prose surface" — that is the disproof side's
strongest argument, and those two things are precisely our G5 and G6.

**GitHub PR + gh CLI (+prr)** — the best off-the-shelf answer in practice.
Reviewing a markdown spec as a PR is standard industry practice: suggestion
blocks (G8 O), agent participation over gh/REST/GraphQL (G4 O — there is even a
dedicated `gh-pr-review` extension), submitting a review approximates a round
(G2 △), thread resolve (G3 △).
What it does not cover (fatally): **G5 — the authoritative copy is somebody
else's server** (no clone, no offline, no migration; GHES is also a server plus a
DB). **G6 — inline comments are impossible in the rich diff (the rendered
view)**, which the community patches over with browser extensions — itself proof
the demand is real. **G1 — prose revision is frequently a rewrite, and a rewrite
is exactly where the anchor is lost** (demoted to outdated; on a force-push the
context cannot be recovered). Applying a suggestion is web-UI only (no REST —
G9 △, though an agent can work around it by parsing the body and committing
directly). Comments can be edited and deleted, so it is not append-only (G3).
Verdict: **partial substitute.** "If the team already lives on GitHub, first
prove why G5 should stay a hard requirement" is the disproof side's argument —
our answer: G1 (prose rewrites) and G6 (the rendered surface) are structurally
unsolvable on GitHub, and a ledger you cannot migrate rubs against an agent loop
that runs locally.

**Reviewable** (SaaS · continuously updated through 2026-08) — comments survive
across revisions (G1 O), and the disposition model
(Blocking/Discussing/Working/Satisfied/Informing plus the resolution formula "0
blockers + at least 1 satisfied") is finer than our four dispositions (G3 O for
semantics); **agent-specific identities plus a CLI/MCP shipped in 2026-06**
(G4 O). What it does not cover: storage is Firebase SaaS end to end (G5 X), and
it presumes a GitHub PR (code). Verdict: partial component — borrow the
disposition vocabulary and the resolution formula.

**git-appraise** (Google · 5,304★ · last commit 2023-08 — **dormant 3 years**) —
one-line JSON in git notes (`refs/notes/devtools/*`), synchronized without a
server by `cat_sort_uniq` merging (G5 O — this tool's headline guarantee).
`git appraise show -json` (G4 O).
What it does not cover: **the location is a line range pinned to a commit hash —
no re-anchoring at all** (precisely the diff-snapshot model G1 rules out); no
suggestion diffs; disposition is a resolved bool; maintenance is dead.
Verdict: **partial component (a ledger precedent).** "Put a JSON ledger in a
notes ref and merge it without conflicts" is specround's storage layer itself,
and it works on prose .md unchanged — H3's git notes option is demonstrated.

**Radicle** (heartwood · commit 2026-08-05 · team-maintained) — a Patch is a
list of Revisions (rounds), each Revision with a base plus a delta (G2 O —
exactly our round model), resolve/unresolve operations plus
**`Revision.resolves` (a new revision declares which review comments it closes —
a round × disposition cross-link)** plus an append-only CRDT (G3 O/△), and
`rad cob show` JSON plumbing (G4 O). The official example reviews prose
(MENU.txt).
What it does not cover: CodeLocation is pinned to a commit Oid (G1 X); no
first-class suggestion concept (G8 △/G9 X); COBs live in Radicle's namespace and
do not follow an ordinary `git clone` — adopting the whole P2P node and DID stack
is required (G5 △).
Verdict: partial component — the `Revision.resolves` pattern is the model for how
G2 and G3 interact.

**git-bug** (9,963★ · active) — not a review tool (no anchors, no rounds). But
`doc/spec/dag-entity.md` is a **general-purpose "entity embedded in git" format
with a formal specification**, complete with signatures, Lamport ordering, and
conflict-free merging — define a "review-comment" entity and G3 plus G5 come free
in a proven implementation (with a Go dependency). Verdict: component (H3's third
option).

**prr** (danobi · 409★ · low-intensity activity) — pulls a GitHub PR down as a
local "review file", you mark it up in your editor (inline/span/file/PR-level
comments plus `@prr approve|reject` directives plus a suggestion fence) and
submit. **The official example is a review of the prose of The Art of War** — the
raw-document input surface (half of G6) is designed. The authoritative copy is
GitHub (G5 X). Verdict: component — borrow the raw-surface markup syntax.

**reviewdog** (v0.21.0, 2025-09 · active) — a pipe that turns linter output into
review comments. Not a loop but one-way (no storage, dispositions, or
harvesting). Verdict: unrelated — except that **RDFormat (rdjson): a structured
review-comment exchange format with multi-line ranges, severity, and suggestion
diffs built in** is worth borrowing as G4's output schema.

**CodeRabbit / Greptile** (AI PR reviewers · very active) — participants, not
review infrastructure. No storage or anchor model of their own (inherited from
the host platform). Verdict: unrelated — except that the ecosystem argument
holds: **if the channel is standard, off-the-shelf AI reviewers plug into it. If
specround invents its own channel it is cut off from that ecosystem** → when
designing the G4 channel, borrowing established formats (RDFormat, suggestion
fences) beats a fully proprietary one. CodeRabbit CLI's dual output
(`--prompt-only`/`--plain` — two surfaces for the same result, one for people and
one for agents) is a reference for the G4 design.

### 2.3 Prose annotation and anchoring — standards and algorithms (the component seam)

**CriticMarkup** (spec frozen 2013 · toolkit dormant since 2021 — the ecosystem
is alive: native in MultiMarkdown-6, PyMdown, pancritic, and the Obsidian family
active in 2026) — five inline edit-tracking forms for prose: `{++added++}`
`{--deleted--}` `{~~old~>new~~}` `{>>comment<<}` `{==highlight==}`. The anchor is
a physical position in the text, so it moves with the revision (the re-anchor
problem is structurally eliminated, G1 O); the document is the storage (G5 O);
and `{~~~>~~}` is itself a suggestion diff attached to an anchor (G8 O — a
problem solved 13 years ago).
What it does not cover: the spec has no author, timestamp, thread, or disposition
fields (Obsidian Commentator adds them as its own extension — a precedent); a
disposition is removing the mark, so the history survives only in the git diff
(G3 △); **comments contaminate the document** — during review the document stops
being a clean source (though this does mesh with our G6 decision's flow, "type
inline annotations into the raw text and the harvester absorbs them").
Verdict: **partial component (a strong one).** It is the very syntax SPEC §3
already references as "type inline annotations (the CriticMarkup family) into the
raw text and the harvester absorbs them" — adopt it as the raw surface's input
syntax, but keep the ledger fields separate (not an inline extension), which is
the lesson of Obsidian Commentator's beta warning ("risk of text loss is
non-zero").

**Hypothesis fuzzy anchoring** (actively operated) — **the authority for H4
(anchor survival).** On creating an annotation it captures three selectors at
once (RangeSelector / TextPositionSelector / TextQuoteSelector = exact plus 32
characters of prefix/suffix), and re-anchoring is a four-rung fallback:
① apply the Range directly → **check** the resulting text against the quote's
exact (verifying every strategy's result against the quote is the core design)
② apply TextPosition directly → the same check ③ use the position as a search
hint for two-phase fuzzy matching on prefix/suffix (with an acceptance threshold)
④ a full-text fuzzy search of the whole document.
The engine is a modified diff-match-patch (Bitap for matching, Myers for
comparison). **When all of it fails, it is classified as an orphan and preserved
(not deleted)** — a precedent answer to our H4 question, "what disposition on
failure (an orphaned comment)".
The performance trap is measured: a short common quote plus a long document
blocks for seconds to tens of seconds (client#3919) — the follow-up work
anchor-quote (robertknight) improved 13.3s → 0.94s (a maxErrorRate parameter and
built-in normalization, though it was archived in 2022). An independent module:
dom-anchor-text-quote (dormant since 2023).
The platform itself is G5 X (a server plus Postgres plus ES) and G3/G8/G9 X.
Verdict: **component (the authority for H4).** For raw markdown, drop the DOM
selectors and port only "position hint + quote verification + bitap fuzzy +
orphan preservation".

**W3C Web Annotation Data Model** (REC settled 2017 · stable) — the standard
schema for anchor selectors: TextQuoteSelector (`exact`/`prefix`/`suffix`) ·
TextPositionSelector (`start`/`end`) · `refinedBy` chaining · several selectors
on one target (alternatives of differing precision, and the consumer picks —
Hypothesis capturing three at once is this pattern). There is a motivation
vocabulary (commenting/editing/questioning/replying) but **no disposition state
field for "applied"/"rejected" in the standard** — so where we extend it is
clear. The reference implementation, Apache Annotator, is archived (2024).
Verdict: **component (the anchor part of the ledger schema).** Adopting the
standard makes the Hypothesis and Readium families' code and experience
compatible.

**graft** (tkjaer · 5★ · no activity since 2026-02) — exact → fuzzy
prefix/suffix re-interpretation plus JSON stored on an orphan branch
(`graft-comments`). **The only case found of pairing "G1 automatic re-anchoring"
with "G5 git storage"** — but it is a web app, requires a GitHub login, and has
zero agent integration. Verdict: component (an algorithm reference). That this
pairing exists only as one 5★ experiment is itself proof of the gap.

**Semiont write-time reconcile** (The-AI-Alliance) — when an LLM emits exact plus
prefix/suffix, the position is settled at write time by ① a verbatim search
② deterministic normalization (smart quotes, whitespace) ③ a 5% Levenshtein
tolerance, with the position/quote pair kept mutually consistent (the invariant
`content.substring(start,end)===exact`), and multiple matches are not chosen
quietly but flagged `first-of-many`. **A G4 × G1 precedent for the era where
agents produce the annotations** — fuzzy on the write side only, with the read
side recovering verbatim only. Verdict: component.

### 2.4 Surveyed and judged unrelated

- **spec-kit** (GitHub · 125k★) / **OpenSpec** (64k★): giants of spec generation
  and refinement workflow. There are no anchored comments, dispositions, or
  recorded rounds — review happens in the chat and the only trace is the spec
  diff. That the "review step" is a blank in these giants' workflow is favourable
  data for positioning, and also an absorption risk.
- **difit** (3k★ · active)/diffx/diffity: a local diff view plus line comments →
  copied into an agent prompt. The comments live on the diff snapshot — precisely
  the model we reject (G1 X by definition). If anything it confirms specround's
  G1 differentiation.
- **beads** (Steve Yegge): an issue tracker, but it demonstrates "git = the DB,
  append-only JSONL, merge conflicts are harmless, agents first-class" at scale —
  useful only as an implementation-strategy reference for G3/G5.
- **git-dit** (semi-dormant) · **sit** (dead since 2018) · **picosh/git-pr** (a
  server is presumed) · **git-revue** (design notes only, unimplemented — proof
  in itself that this niche is empty): sweeping the family found no new active
  tool in this line since git-appraise. The one living lineage is Radicle (COB).
- **HumanLayer/gotoHuman** (SaaS approval channels) · **vscode-code-review**
  (CSV, for code) · **MD Review** (line anchors, no AI integration) ·
  **itssan14/md-review** (a minimal clipboard tool): unrelated.
- **Editorially (shut down 2014)/Draft/Penflip**: the generation of markdown
  collaborative-review SaaS — all of them server models, and all of them gone or
  dormant. A counter-example about "the lifespan of a server model" (data
  supporting the G5 direction).

## 3. Proof of absence — the same gap on all four axes

The four survey axes converged independently on the same gap:

1. **The pairing of G1 × automatic prose re-anchoring with G5 git storage** — the
   code review family pins anchors to commits without exception
   (git-appraise · Radicle · GitHub · prr), and the side that has anchor survival
   (Hypothesis · Reviewable) stores on a server without exception. The only
   attempt at pairing them is graft (a 5★ experiment).
2. **G3 + G5: an append-only disposition ledger inside the repo** — Reviewable,
   whose disposition semantics are the finest, is on Firebase, and the side that
   keeps the ledger inside the repo (md-redline · CriticMarkup) erases the marker
   on disposition, so the history disappears. The append-only ledger precedents
   (git-appraise notes · beads JSONL) are not prose review.
3. **G6: two surfaces, render and raw → converging on one ledger** — X for every
   tool. On GitHub, commenting in the rendered view is impossible at all (the
   community is compensating with browser extensions). The only partial answer is
   CriticMarkup inline (the document is the ledger, so the surface is irrelevant),
   but it has no dispositions or threads.
4. **G2: rounds recorded against a base commit** — Gerrit patchsets and Radicle
   Revisions are the precedents on the code side. Among the new markdown review
   tools the best is md-review-tool's file-change snapshot (not against a
   commit).

## 4. Overall verdict

### 4.1 The verdict: (c) partial — the case for building holds, but as an assembly

- **(a) "it already exists" is rejected.** Even the closest single tool,
  md-redline, has no disposition ledger, rounds, or suggestion diffs, and even
  the closest combination (GitHub PR + prr + gh) breaks structurally on G5 (the
  authoritative copy is somebody else's server), G6 (the rendered surface), and
  G1 (the anchor dies on a rewrite). Gerrit has nearly every functional axis, but
  its identity — a server daemon plus a code diff surface — is the opposite of
  our use.
- **(b) The differentiator's exact guarantee combination = G1 (prose re-anchor)
  × G2 (rounds) × G3 (append-only disposition ledger) × G5 (no server) × G6
  (surface neutrality).** Take any one of them away and something already exists:
  without G5, Gerrit/Reviewable; without G1 and G6, GitHub PR; without G2 and G3,
  md-redline. It is new only with all five together. G4·G7·G8·G9 are not the
  differentiator but **the ticket to the market** (the new tools all do them
  already).
- **There is no individual technical hard problem.** H4 (anchor survival) was
  solved 12 years ago with a published algorithm and implementation
  (Hypothesis), and the ledger has three precedent schemas (§4.3). The novelty
  is the combination, and the competitive risk is time (§0, risk).

### 4.2 Answers this gives the open items (H3–H9)

- **H3 (ledger storage: jsonl vs git notes)**: three precedents — git-appraise
  (notes + one-line JSON + cat_sort_uniq), git-bug (dag-entity: signatures,
  Lamport, a formal spec, a Go dependency), beads (flat-file JSONL in the repo).
  Notes have the "the ref does not follow a clone" problem (fetch configuration
  is needed), and flat-file JSONL is readable with no tool at all — by the
  criteria of G4 (an agent just cats it) and G5's contract simplicity, this
  survey strengthens the case that JSONL is the better one (the decision is the
  spec's).
- **H4 (the anchor survival algorithm)**: the assembly prescription got clear —
  the anchor schema = a W3C TextQuoteSelector (exact + 32 characters of
  prefix/suffix) and TextPositionSelector pair, with a mutual-consistency
  invariant at write time (`substring(start,end)===exact`, Semiont).
  Re-anchoring = a four-rung fallback in the order position hint → verbatim →
  normalization → bitap fuzzy (verifying against the quote at every rung)
  (Hypothesis), and on failure **preserve the orphan in the ledger as awaiting
  disposition rather than deleting it**.
  Performance: the fuzzy blocking on a short quote plus a long document is
  measured — see anchor-quote's kind of improvement (an error-rate parameter,
  batching).
- **H8 (stale suggestion diffs)**: Gerrit 3.11+ already does "apply an old
  patchset's suggestion to the latest (patch transformation)" — a precedent for
  solving it with the re-anchor family rather than by locking the round.
- **H9 (absorbing existing diff comment UIs)**: reviewdog's RDFormat is a
  candidate input format for an "outside producer → our ledger" converter.

### 4.3 The parts list to take

| part | source | where it goes |
|---|---|---|
| the anchor selector schema (exact+prefix/suffix+position, refinedBy) | W3C Web Annotation | the ledger's anchor field |
| four-rung re-anchor fallback + quote verification + orphan preservation | Hypothesis fuzzy anchoring | H4 |
| the write-time consistency invariant · `first-of-many` | Semiont | when an agent makes a comment |
| bitap fuzzy matching (the improved version) | diff-match-patch / anchor-quote | the H4 engine |
| a git ledger: notes+JSON+cat_sort_uniq / dag-entity / JSONL | git-appraise / git-bug / beads | H3 |
| the ledger's append chain as the history (a meta ref commit chain) | Gerrit NoteDb | H3 |
| the disposition vocabulary + resolution formula ("0 blockers + at least 1 satisfied") | Reviewable | the G3 state model |
| the cross-link by which a round absorbs a disposition (`Revision.resolves`) | Radicle | G2×G3 |
| the inline suggestion-diff syntax `{~~old~>new~~}` | CriticMarkup | the G8 raw surface |
| suggestions as a structured field on a comment (`fix_suggestions`) + machine apply | Gerrit | G9 |
| ```suggestion fences (a de facto standard people and LLMs have already learned) | GitHub | the G8/G9 wire format |
| co-authoring the suggester on the apply commit (putting the disposition in the git ledger) | GitHub practice | G9 |
| raw review-file markup (quote+interleave+span+directives) | prr | the G6 raw surface |
| a structured review-comment exchange format (rdjson) | reviewdog RDFormat | G4 output · H9 input |
| the MCP tool surface (request_review/ask/wait) | md-redline | the G4 channel |
| the hook intervention point (`ExitPlanMode` and the like) | Plannotator | agent integration |
| the blocking CLI contract (structured stdout + exit code) | md-review-plus | G7×G4 |
| dual output for people and agents (`--plain`/`--prompt-only`) | CodeRabbit CLI | G4 |

## 5. Main sources

Cited inline with each verdict. The core: Gerrit's official docs on NoteDb,
ported comments, and suggest edits / Reviewable docs + CHANGELOG (agent support,
2026-06) / the reviewdog repo / GitHub suggestions and the community discussions
(#23138 · #142466 · #186730) / google/git-appraise (the schema itself) / git-bug's
dag-entity spec / radicle heartwood sources (patch.rs · common.rs) / danobi/prr's
book / Hypothesis's "Fuzzy Anchoring" blog post and client#3919 / W3C
annotation-model / the CriticMarkup toolkit / dejuknow/md-redline /
backnotprop/plannotator / Pimzino/spec-workflow-mcp / konradmichalik/md-annotator
/ jinqishen0725/markdown-review / jaredhughes/commentary /
LetitiaChan/md-review-tool / Seiraiyu/md-review-plus / tkjaer/graft / Semiont
W3C-SELECTORS / Fevol/obsidian-criticmarkup / philphilphil/obsidian-track-changes
/ github/spec-kit / Fission-AI/OpenSpec / steveyegge beads. Activity figures are
as queried from the GitHub API on 2026-08-06.

## 6. Where file-keyed state has lived before (the storage-scope axis)

Added 2026-08-08, from a design conversation about the consumer-mapping adapter
(H17): "who else has had to decide where per-file history lives, and what did
the choice cost them?" This axis is orthogonal to §1's review-loop matrix — it
is about the store, not the loop — and every position we adopted turns out to
have a named predecessor and a named bill.

| question | predecessor | their answer | the bill they paid | where we stand |
|---|---|---|---|---|
| store beside the file or central? | CVS/RCS | `,v` sidecars | directory pollution | rejected the sidecar default for the same reason |
| | SVN ≤1.6 → 1.7 | `.svn/` in *every* directory, then centralized | the industry's clearest sidecar→central migration | central by default, opt-in beside (`.specround.json`) |
| | Vim `undodir` | central dir, absolute path encoded in the filename | moves orphan history; stale files accumulate forever | same key model; H10 (re-bind) and H13 (archive) are the two bills Vim never paid |
| | Lightroom | central catalog + opt-in XMP sidecars, with a sync setting | catalog↔sidecar drift | same default+opt-in split; its "missing photo → relink by content match" flow is `doctor`/H10's ancestor |
| file identity across moves | git | none stored — tree snapshots, renames inferred at diff time | `log --follow` is a heuristic and misses | H10 makes content-hash the oracle and git-rename only a hint — the inverse allocation, bought with the store we keep and git does not |
| | Fossil | repo anywhere, a `_FOSSIL_` pointer file in the checkout | a marker file in the tree | the mirror image of our binding (we key centrally, it points locally) |
| comments across revisions | W3C annotation / Hypothesis | TextQuoteSelector (exact/prefix/suffix) + position, in a fallback chain | "same document, different URL" never solved | our anchor shape and ladder are this lineage; H10 is their URL problem in path form |
| | Gerrit / Critique | a comment belongs to a frozen patchset, ported forward on the next one | porting heuristics | rounds + I7 + round-open carry; the mid-round reanchor bug (fixed 2026-08-08, I12) was rediscovering *why* they scope comments to a frozen revision |
| who defines the directory root | editorconfig | up-walk with an explicit `root = true` stopper | none to speak of | `.specround.json` nearest-wins matches; a root marker is the proven escape if nesting ever confuses |
| file vs workspace as the unit | LSP | the document is the primitive, workspaceFolders aggregate | — | dir-view is navigation-only over per-document stores; the consumer mapping starts per-document for the same reason |
| directory-keyed state (cautionary) | agent-CLI session stores keyed by cwd slug | per-directory memory | sessions in a worktree cannot see the root's memory (measured in this workspace) | the store key is the *document's* absolute path; directories are views, never keys |

The pattern the table keeps repeating: **central-by-default with an opt-in
sidecar is where mature systems converged; path keys are cheap and honest but
their two bills (re-binding after a move, lifetime of the pile) always come
due; and identity-by-heuristic is the one position nobody was happy with.**
H10 and H13 are not our surprises — they are the standing invoices of the
model we chose, with forty years of prior tenants.
