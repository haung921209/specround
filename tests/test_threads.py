"""Threads: resolve, reopen, and what the default listing shows (G11).

Most of the records here are written straight onto the ledger rather than
through the store's helpers. That is deliberate: the rules belong to the format,
so they are asked of the format — a hand-written line, or one from a writer in
another language, gets the same answer as the tool's own. The last section then
asks what the store adds on top, which is one thing: it declines to write a line
that would say nothing.
"""

import pytest

from specround.errors import InvariantError
from specround.fold import fold


def resolve(store, target, *, author="alice", actor="human", note=None):
    record = {"type": "thread.resolve", "author": author, "actor": actor, "target": target}
    if note is not None:
        record["note"] = note
    return store.ledger.append(record)["id"]


def reopen(store, target, *, author="alice", actor="human", reason="reopened"):
    return store.ledger.append(
        {
            "type": "thread.reopen",
            "author": author,
            "actor": actor,
            "target": target,
            "reason": reason,
        }
    )["id"]


# -- the round trip ------------------------------------------------------


def test_resolving_a_thread_takes_it_out_of_the_default_listing(store, round_id):
    first = store.add_comment(round_id, author="bob", body="why 30 seconds?")
    second = store.add_comment(round_id, author="carol", body="retries?")
    assert [c.id for c in store.fold().threads()] == [first, second]

    resolve(store, first, note="answered in the reply")
    state = store.fold()
    assert [c.id for c in state.threads()] == [second]
    assert [c.id for c in state.active_threads] == [second]
    assert [c.id for c in state.resolved_threads] == [first]
    # Hidden, not deleted: the comment is still there in full.
    assert set(state.comments) == {first, second}
    assert state.comments[first].body == "why 30 seconds?"


def test_the_toggle_shows_the_resolved_ones_again(store, round_id):
    first = store.add_comment(round_id, author="bob", body="why?")
    second = store.add_comment(round_id, author="carol", body="retries?")
    resolve(store, first)
    state = store.fold()
    assert [c.id for c in state.threads(include_resolved=True)] == [first, second]
    assert [c.id for c in state.comments_in(round_id)] == [first, second]


def test_reopening_puts_a_thread_back_in_the_default_listing(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    resolve(store, cid)
    assert store.fold().threads() == []

    reopen(store, cid, reason="the timeout came back in revision 3")
    state = store.fold()
    assert [c.id for c in state.threads()] == [cid]
    assert state.resolved_threads == []
    assert state.comments[cid].resolved is False


def test_a_thread_can_close_and_open_again_any_number_of_times(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    resolve(store, cid)
    reopen(store, cid, reason="not settled after all")
    resolve(store, cid, note="settled now")
    comment = store.fold().comments[cid]
    assert comment.resolved is True
    assert [r.resolved for r in comment.resolutions] == [True, False, True]
    assert comment.resolution.note == "settled now"


def test_a_suggestion_is_a_thread_too(store, round_id):
    sid = store.add_suggestion(round_id, author="agent:reviewer", patch="-30\n+60\n")
    resolve(store, sid, author="alice")
    assert [c.id for c in store.fold().resolved_threads] == [sid]
    assert store.fold().threads() == []


def test_threads_can_be_listed_per_round(doc, clock):
    from specround.store import ReviewStore

    store = ReviewStore.at(doc.parent, clock=clock)
    other = doc.parent / "other.md"
    other.write_text("second document\n", encoding="utf-8")
    first_round = store.open_round(doc, author="alice")
    second_round = store.open_round(other, author="alice")
    a = store.add_comment(first_round, author="bob", body="on the spec")
    b = store.add_comment(second_round, author="bob", body="on the other")
    resolve(store, a)
    state = store.fold()
    assert state.threads(first_round) == []
    assert [c.id for c in state.threads(first_round, include_resolved=True)] == [a]
    assert [c.id for c in state.threads(second_round)] == [b]


# -- who closed it -------------------------------------------------------


def test_who_closed_the_thread_is_recorded_as_two_facts(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    resolve(store, cid, author="agent:reviewer", actor="agent", note="applied upstream")
    resolution = store.fold().comments[cid].resolution
    assert resolution.actor == "agent"
    assert resolution.author == "agent:reviewer"
    assert resolution.note == "applied upstream"
    assert resolution.ts


def test_an_agent_closing_and_a_person_reopening_both_stay_readable(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    resolve(store, cid, author="agent:reviewer", actor="agent")
    reopen(store, cid, author="bob", actor="human", reason="the agent read it wrong")
    history = store.fold().comments[cid].resolutions
    assert [(r.actor, r.resolved) for r in history] == [("agent", True), ("human", False)]
    # The reopen's reason lands in the same field the resolve's note would.
    assert history[-1].note == "the agent read it wrong"


# -- idempotence (I10) ---------------------------------------------------


def test_resolving_an_already_resolved_thread_changes_nothing(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    resolve(store, cid, note="first")
    before = store.fold()
    resolve(store, cid, author="carol", note="second")
    after = store.fold()
    # Accepted, not refused — saying the same thing twice is agreement, and a
    # caller that closes a closed thread has made a harmless mistake.
    assert after.comments[cid].resolved is True
    assert [c.id for c in after.resolved_threads] == [c.id for c in before.resolved_threads]
    assert after.threads() == before.threads()
    # And the redundant line is still history: who said it is worth keeping.
    assert [r.author for r in after.comments[cid].resolutions] == ["alice", "carol"]


def test_reopening_an_open_thread_changes_nothing(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    reopen(store, cid, reason="never was closed")
    state = store.fold()
    assert state.comments[cid].resolved is False
    assert [c.id for c in state.threads()] == [cid]


def test_the_state_is_the_last_assertion_not_a_count(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    for _ in range(3):
        resolve(store, cid)
    reopen(store, cid, reason="one reopen beats three resolves")
    assert store.fold().comments[cid].resolved is False


# -- refusals ------------------------------------------------------------


def test_resolving_a_thread_that_does_not_exist_is_refused(store, round_id):
    with pytest.raises(InvariantError, match="unknown comment"):
        resolve(store, "c-nonexistent")
    with pytest.raises(InvariantError, match="is a round, not a comment"):
        resolve(store, round_id)


def test_resolving_a_reply_is_refused_because_a_reply_is_not_a_thread(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    reply_id = store.reply(cid, author="alice", body="because of the proxy")
    # Replies are flat: the thread's id is its root comment's, so a reply id
    # names a real event that nothing can hang off.
    with pytest.raises(InvariantError, match="an event but not a comment or suggestion"):
        resolve(store, reply_id)


def test_replying_to_a_resolved_thread_is_refused(store, round_id):
    """I11 — the one thing closing a thread actually gates."""
    cid = store.add_comment(round_id, author="bob", body="why 30 seconds?")
    resolve(store, cid, note="answered in chat")
    with pytest.raises(InvariantError, match="reopen it before replying"):
        store.reply(cid, author="alice", body="because of the proxy")
    assert store.fold().comments[cid].replies == []


def test_reopening_lets_the_conversation_continue(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why 30 seconds?")
    resolve(store, cid)
    reopen(store, cid, reason="bob asked again")
    store.reply(cid, author="alice", body="because of the proxy")
    assert [r.body for r in store.fold().comments[cid].replies] == ["because of the proxy"]


def test_resolving_after_a_reply_keeps_the_reply(store, round_id):
    """Closing is not deleting: the answers stay under the closed thread."""
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.reply(cid, author="alice", body="the proxy caps it")
    resolve(store, cid, note="that answers it")
    comment = store.fold().comments[cid]
    assert comment.resolved is True
    assert [r.body for r in comment.replies] == ["the proxy caps it"]


def test_a_resolved_thread_still_takes_everything_else(store, round_id):
    """Only replies are gated — dispositions and re-resolves still land."""
    cid = store.add_comment(round_id, author="bob", body="why?")
    resolve(store, cid)
    store.dispose(cid, author="alice", verdict="applied", reason="raised to 60")
    resolve(store, cid, author="carol", note="again")
    comment = store.fold().comments[cid]
    assert comment.verdict == "applied"
    assert [r.author for r in comment.resolutions] == ["alice", "carol"]


def test_a_refused_reply_leaves_the_ledger_untouched(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    resolve(store, cid)
    before = store.ledger.read()
    with pytest.raises(InvariantError):
        store.reply(cid, author="alice", body="late answer")
    assert store.ledger.read() == before


def test_a_refused_resolve_leaves_the_ledger_untouched(store, round_id):
    before = store.ledger.read()
    with pytest.raises(InvariantError):
        resolve(store, "c-nonexistent")
    assert store.ledger.read() == before


# -- the axes stay apart -------------------------------------------------


def test_a_thread_can_be_resolved_with_no_disposition(store, round_id):
    """Agreement closes a conversation without deciding the comment (G11)."""
    cid = store.add_comment(round_id, author="bob", body="why 30 seconds?")
    resolve(store, cid, note="bob agreed in chat")
    comment = store.fold().comments[cid]
    assert comment.resolved is True
    assert comment.unresolved is True  # the disposition axis is untouched
    assert comment.verdict is None
    assert [c.id for c in store.fold().unresolved] == [cid]


def test_a_settled_comment_can_keep_its_thread_open(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.dispose(cid, author="alice", verdict="applied", reason="raised to 60")
    comment = store.fold().comments[cid]
    assert comment.settled is True
    assert comment.resolved is False
    assert [c.id for c in store.fold().threads()] == [cid]


def test_resolved_and_unresolved_are_not_complements(store, round_id):
    """The two words are antonyms in English and different axes here."""
    settled_open = store.add_comment(round_id, author="bob", body="settled, still talking")
    store.dispose(settled_open, author="alice", verdict="applied", reason="done")
    resolved_undisposed = store.add_comment(round_id, author="bob", body="agreed, no verdict")
    resolve(store, resolved_undisposed)
    state = store.fold()
    assert [c.id for c in state.unresolved] == [resolved_undisposed]
    assert [c.id for c in state.resolved_threads] == [resolved_undisposed]
    assert [c.id for c in state.active_threads] == [settled_open]


def test_resolving_does_not_change_what_a_round_close_must_declare(store, round_id):
    """Closing a thread is not a way to walk away from an undisposed comment."""
    cid = store.add_comment(round_id, author="bob", body="retries?")
    resolve(store, cid, note="we talked it through")
    assert [c.id for c in store.fold().unresolved_in(round_id)] == [cid]
    with pytest.raises(InvariantError, match="1 unresolved comment"):
        store.close_round(round_id, author="alice")

    close_id = store.close_round(round_id, author="alice", allow_unresolved=True)
    assert store.fold().rounds[round_id].unresolved_at_close == [cid]
    assert close_id


def test_a_thread_outlives_its_round(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="retries?")
    store.dispose(cid, author="alice", verdict="answered", reason="see the reply")
    store.close_round(round_id, author="alice")
    # Conversations usually finish after the round does; no live-round check.
    resolve(store, cid, note="nothing left to say")
    assert store.fold().comments[cid].resolved is True


def test_an_orphan_can_be_resolved(store, doc, round_id):
    anchor = store.anchor_in_round(round_id, "30 seconds")
    cid = store.add_comment(round_id, author="bob", body="too short", anchor=anchor)
    doc.write_text("completely different text\n", encoding="utf-8")
    store.reanchor_document(doc, author="agent:reanchor")
    assert [c.id for c in store.fold().orphans] == [cid]

    resolve(store, cid, note="the sentence is gone, so is the question")
    comment = store.fold().comments[cid]
    assert comment.orphaned is True and comment.resolved is True


# -- determinism ---------------------------------------------------------


def test_fold_stays_deterministic_with_thread_events(store, doc, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    resolve(store, cid)
    reopen(store, cid, reason="again")
    resolve(store, cid)
    records = store.ledger.read()
    assert fold(records) == fold(records)

    from specround.store import ReviewStore

    assert ReviewStore.for_document(doc).fold() == store.fold()


def test_thread_state_does_not_depend_on_timestamps(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    resolve(store, cid)
    reopen(store, cid, reason="again")
    stamps = ["2031-01-01T00:00:00Z", "1999-01-01T00:00:00Z"] * 3
    shuffled = [{**r, "ts": s} for r, s in zip(store.ledger.read(), stamps)]
    # seq is the order; a clock that runs backwards changes nothing.
    assert fold(shuffled).comments[cid].resolved is False


# -- the store's helpers -------------------------------------------------


def test_the_store_closes_and_reopens_a_thread(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    closed = store.resolve(cid, author="alice", actor="human", note="answered above")
    assert closed.startswith("v-")
    assert [c.id for c in store.fold().resolved_threads] == [cid]

    opened = store.reopen(cid, author="bob", actor="human", reason="not settled")
    assert opened.startswith("n-")
    assert [c.id for c in store.fold().threads()] == [cid]


def test_the_store_records_who_closed_it(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.resolve(cid, author="agent:reviewer", actor="agent")
    resolution = store.fold().comments[cid].resolution
    assert (resolution.author, resolution.actor) == ("agent:reviewer", "agent")


def test_the_store_writes_nothing_for_a_redundant_resolve(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.resolve(cid, author="alice", actor="human")
    before = store.ledger.read()

    again = store.resolve(cid, author="carol", actor="human", note="also me")
    # None means the ledger was not touched. The contract accepts a redundant
    # line (I10); the tool just has no reason to produce one, the same way an
    # unchanged anchor records nothing.
    assert again is None
    assert store.ledger.read() == before
    assert store.fold().comments[cid].resolved is True


def test_the_store_writes_nothing_for_a_redundant_reopen(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    before = store.ledger.read()
    assert store.reopen(cid, author="alice", actor="human", reason="was never shut") is None
    assert store.ledger.read() == before


def test_closing_a_closed_thread_is_harmless_not_an_error(store, round_id):
    """The point of idempotence: a misjudgement stays a misjudgement."""
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.resolve(cid, author="alice", actor="human")
    for _ in range(3):
        store.resolve(cid, author="agent:reviewer", actor="agent")
    assert len(store.fold().comments[cid].resolutions) == 1


def test_the_store_refuses_a_target_that_is_not_a_thread(store, round_id):
    with pytest.raises(InvariantError, match="unknown comment"):
        store.resolve("c-nonexistent", author="alice", actor="human")
    with pytest.raises(InvariantError, match="is a round, not a comment"):
        store.reopen(round_id, author="alice", actor="human", reason="wrong id")


def test_the_store_refuses_a_reopen_with_no_reason(store, round_id):
    from specround.errors import SchemaError

    cid = store.add_comment(round_id, author="bob", body="why?")
    store.resolve(cid, author="alice", actor="human")
    with pytest.raises(SchemaError, match="'reason' must not be empty"):
        store.reopen(cid, author="alice", actor="human", reason="")
    assert store.fold().comments[cid].resolved is True


def test_the_store_refuses_an_actor_outside_the_vocabulary(store, round_id):
    from specround.errors import SchemaError

    cid = store.add_comment(round_id, author="bob", body="why?")
    with pytest.raises(SchemaError, match="unknown actor"):
        store.resolve(cid, author="alice", actor="robot")


def test_the_store_keeps_resolving_and_disposing_apart(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.resolve(cid, author="alice", actor="human")
    comment = store.fold().comments[cid]
    assert comment.resolved is True and comment.verdict is None
    # Still disposable afterwards — closing the talk did not decide the comment.
    store.dispose(cid, author="alice", verdict="applied", reason="landed in rev 2")
    assert store.fold().comments[cid].verdict == "applied"
