import enum
from typing import Optional


class GamePhase(str, enum.Enum):
    CREATED = "created"
    SHERIFF_CAMPAIGN = "sheriff_campaign"
    SHERIFF_VOTE = "sheriff_vote"
    NIGHT_START = "night_start"
    WOLF_STRATEGY = "wolf_strategy"
    WOLF_KILL = "wolf_kill"
    SEER_CHECK = "seer_check"
    WITCH_ACT = "witch_act"
    NIGHT_RESOLVE = "night_resolve"
    WIN_CHECK = "win_check"
    DAY_START = "day_start"
    DAY_SPEECH = "day_speech"
    DAY_VOTE = "day_vote"
    VOTE_RESOLVE = "vote_resolve"
    HUNTER_CHECK = "hunter_check"
    LAST_WORDS = "last_words"
    GAME_END = "game_end"
    POST_REVIEW = "post_review"


# Transition table: current_phase → next_phase
# Some transitions depend on game state (handled by transition function)
_TRANSITIONS: dict[GamePhase, list[GamePhase]] = {
    GamePhase.CREATED: [GamePhase.NIGHT_START],
    GamePhase.SHERIFF_CAMPAIGN: [GamePhase.SHERIFF_VOTE],
    GamePhase.SHERIFF_VOTE: [GamePhase.NIGHT_RESOLVE],
    GamePhase.NIGHT_START: [GamePhase.WOLF_STRATEGY],
    GamePhase.WOLF_STRATEGY: [GamePhase.WOLF_KILL],
    GamePhase.WOLF_KILL: [GamePhase.SEER_CHECK],
    GamePhase.SEER_CHECK: [GamePhase.WITCH_ACT],
    GamePhase.WITCH_ACT: [GamePhase.NIGHT_RESOLVE, GamePhase.SHERIFF_CAMPAIGN],
    GamePhase.NIGHT_RESOLVE: [GamePhase.WIN_CHECK],
    GamePhase.WIN_CHECK: [GamePhase.DAY_START, GamePhase.GAME_END, GamePhase.NIGHT_START],
    GamePhase.DAY_START: [GamePhase.DAY_SPEECH],
    GamePhase.DAY_SPEECH: [GamePhase.DAY_VOTE],
    GamePhase.DAY_VOTE: [GamePhase.VOTE_RESOLVE],
    GamePhase.VOTE_RESOLVE: [GamePhase.HUNTER_CHECK],
    GamePhase.HUNTER_CHECK: [GamePhase.LAST_WORDS],
    GamePhase.LAST_WORDS: [GamePhase.WIN_CHECK],
    GamePhase.GAME_END: [GamePhase.POST_REVIEW],
}


def get_valid_transitions(phase: GamePhase) -> list[GamePhase]:
    return _TRANSITIONS.get(phase, [])


def can_transition(current: GamePhase, target: GamePhase) -> bool:
    return target in _TRANSITIONS.get(current, [])


def next_phase(current: GamePhase, game_over: bool = False,
               first_round: bool = False, day_complete: bool = False) -> GamePhase:
    """Determine the next phase given current phase and game state."""
    if current == GamePhase.WIN_CHECK:
        if game_over:
            return GamePhase.GAME_END
        return GamePhase.NIGHT_START if day_complete else GamePhase.DAY_START
    if current == GamePhase.WITCH_ACT:
        return GamePhase.SHERIFF_CAMPAIGN if first_round else GamePhase.NIGHT_RESOLVE
    if current == GamePhase.LAST_WORDS:
        return GamePhase.WIN_CHECK

    valid = _TRANSITIONS.get(current, [])
    if len(valid) == 1:
        return valid[0]
    raise ValueError(f"Ambiguous transition from {current}, use specific logic")


def next_night_phase(current: GamePhase) -> GamePhase:
    """After WIN_CHECK passes (no winner yet) and day ends, go to next night."""
    return GamePhase.NIGHT_START
