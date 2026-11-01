from types import SimpleNamespace

import pytest

from app.services import llm_service, turns, unified_analysis_service as ua


def seg(seq, speaker, text="x"):
    return SimpleNamespace(seq=seq, speaker=speaker, text=text, start=float(seq), end=float(seq) + 1, confidence=0.9)


def labelled(*speakers):
    return [seg(i, s, f"t{i}") for i, s in enumerate(speakers)]


def seqs(turn_list):
    return [[s.seq for s in t] for t in turn_list]


def test_consecutive_segments_by_one_speaker_are_one_turn():
    result = turns.conversational_turns(labelled("agent", "agent", "borrower", "agent"))
    assert [(sp, [s.seq for s in ss]) for sp, ss in result] == [("agent", [0, 1]), ("borrower", [2]), ("agent", [3])]


def test_a_new_agent_turn_starts_only_after_someone_else_spoke():
    assert seqs(turns.first_agent_turns(labelled("agent", "agent", "agent", "borrower", "agent"))) == [[0, 1, 2], [4]]


def test_first_two_agent_turns_only():
    ls = labelled("agent", "borrower", "agent", "borrower", "agent")
    assert seqs(turns.first_agent_turns(ls)) == [[0], [2]]
    assert [s.seq for s in turns.first_agent_turn_segments(ls)] == [0, 2]


def test_the_borrower_speaking_first_does_not_consume_an_agent_turn():
    assert seqs(turns.first_agent_turns(labelled("borrower", "agent", "agent", "borrower", "agent"))) == [[1, 2], [4]]


def test_fewer_than_two_agent_turns_and_no_agent_at_all():
    assert seqs(turns.first_agent_turns(labelled("agent", "agent"))) == [[0, 1]]
    assert turns.first_agent_turns(labelled("borrower", "borrower")) == []
    assert turns.first_agent_turns([]) == []


def test_unordered_input_is_sorted_by_sequence_first():
    ls = [seg(2, "borrower"), seg(0, "agent"), seg(1, "agent")]
    assert seqs(turns.first_agent_turns(ls)) == [[0, 1]]


def test_turn_numbers_label_only_the_first_two_agent_turns():
    ls = labelled("agent", "agent", "borrower", "agent", "borrower", "agent")
    assert turns.agent_turn_numbers(ls) == {0: 1, 1: 1, 3: 2}


# --- old behaviour vs new: the first two agent SEGMENTS were not the first two turns ---


def old_scope(ls):
    return [s for s in ls if s.speaker == "agent"][:2]


def test_the_old_segment_scope_cut_off_the_second_turn_but_the_turn_scope_does_not():
    ls = labelled("agent", "agent", "borrower", "agent")  # turn 1 split in two, purpose in turn 2
    assert [s.seq for s in old_scope(ls)] == [0, 1]  # missed segment 3
    assert [s.seq for s in turns.first_agent_turn_segments(ls)] == [0, 1, 3]


def test_the_analysis_uses_the_shared_turn_definition():
    assert ua.conversational_turns is turns.conversational_turns
    assert ua.first_agent_turns is turns.first_agent_turns


@pytest.mark.parametrize("pattern", [("agent",), ("agent", "borrower", "agent"), ("agent", "agent", "borrower", "agent", "agent")])
def test_scope_never_includes_a_borrower_segment(pattern):
    ls = labelled(*pattern)
    assert all(s.speaker == "agent" for s in turns.first_agent_turn_segments(ls))
