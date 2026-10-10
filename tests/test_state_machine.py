import pytest
from engine.state_machine import GamePhase, next_phase, can_transition, get_valid_transitions


def test_initial_transition():
    assert next_phase(GamePhase.CREATED) == GamePhase.NIGHT_START


def test_sheriff_flow():
    assert next_phase(GamePhase.SHERIFF_CAMPAIGN) == GamePhase.SHERIFF_VOTE
    assert next_phase(GamePhase.SHERIFF_VOTE) == GamePhase.NIGHT_RESOLVE


def test_night_flow():
    assert next_phase(GamePhase.NIGHT_START) == GamePhase.WOLF_STRATEGY
    assert next_phase(GamePhase.WOLF_STRATEGY) == GamePhase.WOLF_KILL
    assert next_phase(GamePhase.WOLF_KILL) == GamePhase.SEER_CHECK
    assert next_phase(GamePhase.SEER_CHECK) == GamePhase.WITCH_ACT
    assert next_phase(GamePhase.WITCH_ACT) == GamePhase.NIGHT_RESOLVE
    assert next_phase(GamePhase.NIGHT_RESOLVE) == GamePhase.WIN_CHECK


def test_win_check_branch():
    assert next_phase(GamePhase.WIN_CHECK, game_over=False) == GamePhase.DAY_START
    assert next_phase(GamePhase.WIN_CHECK, game_over=True) == GamePhase.GAME_END


def test_day_flow():
    assert next_phase(GamePhase.DAY_START) == GamePhase.DAY_SPEECH
    assert next_phase(GamePhase.DAY_SPEECH) == GamePhase.DAY_VOTE
    assert next_phase(GamePhase.DAY_VOTE) == GamePhase.VOTE_RESOLVE
    assert next_phase(GamePhase.VOTE_RESOLVE) == GamePhase.HUNTER_CHECK
    assert next_phase(GamePhase.HUNTER_CHECK) == GamePhase.LAST_WORDS
    assert next_phase(GamePhase.LAST_WORDS) == GamePhase.WIN_CHECK


def test_can_transition():
    assert can_transition(GamePhase.CREATED, GamePhase.NIGHT_START) is True
    assert can_transition(GamePhase.CREATED, GamePhase.GAME_END) is False
    assert can_transition(GamePhase.WIN_CHECK, GamePhase.DAY_START) is True
    assert can_transition(GamePhase.WIN_CHECK, GamePhase.GAME_END) is True


def test_get_valid_transitions():
    assert GamePhase.DAY_START in get_valid_transitions(GamePhase.WIN_CHECK)
    assert GamePhase.GAME_END in get_valid_transitions(GamePhase.WIN_CHECK)
    assert len(get_valid_transitions(GamePhase.POST_REVIEW)) == 0


def test_first_day_campaign_happens_after_night():
    assert next_phase(GamePhase.WITCH_ACT, first_round=True) == GamePhase.SHERIFF_CAMPAIGN
    assert next_phase(GamePhase.WIN_CHECK, day_complete=True) == GamePhase.NIGHT_START
