"""
The single definition of a conversational turn, shared by the analysis, the R6 check
and the prompt that describes it.

A turn is an uninterrupted stretch of one speaker: consecutive segments with
the same stored speaker are ONE turn, and a new agent turn begins only after
another speaker has spoken. Whisper splits sentences at arbitrary points, so
one agent turn is often several transcript segments.
"""


def conversational_turns(labelled: list) -> list:
    """Groups consecutive segments with the same stored speaker into turns.
    Returns [(speaker, [segment, ...]), ...] in transcript order."""
    turns: list = []
    for seg in sorted(labelled, key=lambda s: s.seq):
        if turns and turns[-1][0] == seg.speaker:
            turns[-1][1].append(seg)
        else:
            turns.append((seg.speaker, [seg]))
    return turns


def first_agent_turns(labelled: list, count: int = 2) -> list:
    """The segments of the agent's first `count` turns: [[segments of turn 1], [turn 2]]."""
    return [segs for speaker, segs in conversational_turns(labelled) if speaker == "agent"][:count]


def first_agent_turn_segments(labelled: list, count: int = 2) -> list:
    """The same segments flattened, in transcript order."""
    return [seg for turn in first_agent_turns(labelled, count) for seg in turn]


def agent_turn_numbers(labelled: list, count: int = 2) -> dict:
    """seq -> 1-based agent turn number, for the segments of the first `count` agent turns."""
    return {seg.seq: n for n, turn in enumerate(first_agent_turns(labelled, count), start=1) for seg in turn}
