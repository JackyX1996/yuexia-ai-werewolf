import pytest
from unittest.mock import MagicMock
from models.game import Role, CauseOfDeath
from engine.rules import (
    check_winner, count_votes, count_weighted_votes, get_wolf_consensus, circular_seat_order,
)
from engine.roles import can_hunter_shoot, get_standard_role_list, get_role_name, idiot_survives_exile


def _make_player(seat, role, alive=True):
    p = MagicMock()
    p.seat = seat
    p.role = role
    p.is_alive = alive
    return p


class TestCheckWinner:
    def test_all_wolves_dead_good_wins(self):
        players = [
            _make_player(1, Role.werewolf, alive=False),
            _make_player(2, Role.werewolf, alive=False),
            _make_player(3, Role.werewolf, alive=False),
            _make_player(4, Role.villager, alive=True),
            _make_player(5, Role.villager, alive=True),
            _make_player(6, Role.villager, alive=True),
            _make_player(7, Role.seer, alive=True),
            _make_player(8, Role.witch, alive=True),
            _make_player(9, Role.hunter, alive=True),
        ]
        assert check_winner(players) == "good"

    def test_all_gods_dead_evil_wins(self):
        players = [
            _make_player(1, Role.werewolf, alive=True),
            _make_player(2, Role.werewolf, alive=True),
            _make_player(3, Role.werewolf, alive=True),
            _make_player(4, Role.villager, alive=True),
            _make_player(5, Role.villager, alive=True),
            _make_player(6, Role.villager, alive=True),
            _make_player(7, Role.seer, alive=False),
            _make_player(8, Role.witch, alive=False),
            _make_player(9, Role.hunter, alive=False),
        ]
        assert check_winner(players) == "evil"

    def test_all_villagers_dead_evil_wins(self):
        players = [
            _make_player(1, Role.werewolf, alive=True),
            _make_player(2, Role.werewolf, alive=True),
            _make_player(3, Role.werewolf, alive=True),
            _make_player(4, Role.villager, alive=False),
            _make_player(5, Role.villager, alive=False),
            _make_player(6, Role.villager, alive=False),
            _make_player(7, Role.seer, alive=True),
            _make_player(8, Role.witch, alive=True),
            _make_player(9, Role.hunter, alive=True),
        ]
        assert check_winner(players) == "evil"

    def test_game_continues(self):
        players = [
            _make_player(1, Role.werewolf, alive=True),
            _make_player(2, Role.werewolf, alive=False),
            _make_player(3, Role.werewolf, alive=True),
            _make_player(4, Role.villager, alive=True),
            _make_player(5, Role.villager, alive=True),
            _make_player(6, Role.villager, alive=False),
            _make_player(7, Role.seer, alive=True),
            _make_player(8, Role.witch, alive=False),
            _make_player(9, Role.hunter, alive=True),
        ]
        assert check_winner(players) is None


class TestCountVotes:
    def test_clear_winner(self):
        votes = {1: 3, 2: 3, 4: 5, 5: 3}
        target, is_tie = count_votes(votes)
        assert target == 3
        assert is_tie is False

    def test_tie(self):
        votes = {1: 3, 2: 5, 4: 3, 5: 1, 6: 5}
        target, is_tie = count_votes(votes)
        assert target is None
        assert is_tie is True

    def test_empty_votes(self):
        target, is_tie = count_votes({})
        assert target is None
        assert is_tie is False


class TestWeightedVotes:
    def test_sheriff_breaks_tie(self):
        # Without sheriff: 3 and 5 each get 2 votes → tie
        # With sheriff at seat 1 voting for 3: 3 gets 2.5, 5 gets 2 → 3 wins
        votes = {1: 3, 2: 3, 4: 5, 5: 5}
        target, is_tie = count_weighted_votes(votes, sheriff_seat=1)
        assert target == 3
        assert is_tie is False

    def test_no_sheriff(self):
        votes = {1: 3, 2: 3, 4: 5}
        target, is_tie = count_weighted_votes(votes, sheriff_seat=None)
        assert target == 3
        assert is_tie is False


class TestIdiot:
    def test_idiot_is_a_god_so_one_left_continues(self):
        players = [
            _make_player(1, Role.werewolf, alive=True),
            _make_player(2, Role.villager, alive=True),
            _make_player(3, Role.seer, alive=False),
            _make_player(4, Role.witch, alive=False),
            _make_player(5, Role.hunter, alive=False),
            _make_player(6, Role.idiot, alive=True),
        ]
        assert check_winner(players) is None

    def test_all_gods_including_idiot_dead_evil_wins(self):
        players = [
            _make_player(1, Role.werewolf, alive=True),
            _make_player(2, Role.villager, alive=True),
            _make_player(3, Role.seer, alive=False),
            _make_player(4, Role.witch, alive=False),
            _make_player(5, Role.hunter, alive=False),
            _make_player(6, Role.idiot, alive=False),
        ]
        assert check_winner(players) == "evil"

    def test_idiot_flip_only_once(self):
        assert idiot_survives_exile(Role.idiot, True) is True
        assert idiot_survives_exile(Role.idiot, False) is False
        assert idiot_survives_exile(Role.hunter, True) is False


class TestCircle:
    def test_seat_twelve_wraps_to_one(self):
        order = circular_seat_order(12, "clockwise", [1, 12], include_start_last=False, total_seats=12)
        assert order == [12, 1]


class TestRoles:
    def test_standard_role_list(self):
        roles = get_standard_role_list()
        assert len(roles) == 12
        assert roles.count(Role.werewolf) == 4
        assert roles.count(Role.villager) == 4
        assert roles.count(Role.seer) == 1
        assert roles.count(Role.witch) == 1
        assert roles.count(Role.hunter) == 1
        assert roles.count(Role.idiot) == 1

    def test_hunter_cannot_shoot_when_poisoned(self):
        assert can_hunter_shoot("witch_poison") is False
        assert can_hunter_shoot("wolf_kill") is True
        assert can_hunter_shoot("vote") is True
        assert can_hunter_shoot(None) is True

    def test_role_names(self):
        assert get_role_name(Role.werewolf) == "狼人"
        assert get_role_name(Role.seer) == "预言家"
