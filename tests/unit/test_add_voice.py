"""Placing the voiceover: each line starts with its scene; lines that do not fit hold the picture."""

from scripts.add_voice import GAP, MAX_TEMPO, plan, shifted


def test_lines_start_with_their_scenes_and_long_lines_hold_the_picture():
    starts = [0.0, 5.0, 10.0]
    cues = [{"from": 0.0, "to": 4.0}, {"from": 4.5, "to": 12.5}, {"from": 13.0, "to": 15.0}]
    placed, holds = plan(cues, starts, 20.0, still={0: 3.0})
    (at, h), = holds
    assert at == 10.0 - 0.6 and h == round(5.0 + 8.0 / MAX_TEMPO + GAP - 10.0, 2)
    assert [p["at"] for p in placed] == [0.0, 5.0, round(10.0 + h, 3)]
    assert [p["tempo"] for p in placed] == [1.0, MAX_TEMPO, 1.0]
    assert shifted(9.0, holds) == 9.0 and shifted(10.0, holds) == round(10.0 + h, 2)
