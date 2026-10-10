import pytest
from engine.actions import action_error


def prompt(kind, **kwargs):
    return {"prompt_type": kind, "prompt_id": "current", **kwargs}


def action(kind, **kwargs):
    return {"action_type": kind, "prompt_id": "current", **kwargs}


@pytest.mark.parametrize("target", [True, "2", 99])
def test_invalid_targets_cannot_consume_a_turn(target):
    assert action_error(action("seer_check", target=target), prompt("seer_check", valid_targets=[2, 3]))


def test_stale_and_wrong_phase_responses_are_rejected():
    assert action_error({"action_type": "day_vote", "prompt_id": "old", "target": 2}, prompt("day_vote"))
    assert action_error(action("wolf_kill", target=2), prompt("day_vote"))


def test_wolf_explosion_requires_authorized_speech_turn():
    assert action_error(action("wolf_explode"), prompt("day_vote", is_wolf=True))
    assert action_error(action("wolf_explode"), prompt("day_speech", is_wolf=False))
    assert action_error(action("wolf_explode"), prompt("day_speech", is_wolf=True)) is None


def test_potion_availability_and_one_potion_limit():
    p = prompt("witch_act", can_save=True, has_poison=True, valid_poison_targets=[2])
    assert action_error(action("witch_act", save=True, poison_target=2), p)
    assert action_error(action("witch_act", save=False, poison_target=2), p) is None
    assert action_error(action("witch_act", save=True), {**p, "can_save": False})
    assert action_error(action("witch_act", poison_target=2), {**p, "has_poison": False})


def test_abstention_and_no_remaining_checks_are_valid():
    assert action_error(action("day_vote", target=None), prompt("day_vote", votable_players=[{"seat": 2}])) is None
    assert action_error(action("seer_check", target=None), prompt("seer_check", valid_targets=[])) is None
    assert action_error(action("seer_check", target=None), prompt("seer_check", valid_targets=[2]))


def test_badge_target_must_be_alive():
    p = prompt("sheriff_transfer", alive_players=[{"seat": 3}])
    assert action_error(action("sheriff_transfer", sheriff_transfer=3), p) is None
    assert action_error(action("sheriff_transfer", sheriff_transfer=0), p) is None
    assert action_error(action("sheriff_transfer", sheriff_transfer=5), p)
