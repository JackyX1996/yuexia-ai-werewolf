"""Integration test: basic game flow validation."""
import pytest
from engine.state_machine import GamePhase
from engine.rules import check_winner
from engine.roles import get_standard_role_list
from engine.sheriff import elect_sheriff
from unittest.mock import MagicMock
from models.game import Role


def _make_player(seat, role, alive=True):
    p = MagicMock()
    p.seat = seat
    p.role = role
    p.is_alive = alive
    return p


class TestGameFlow:
    def test_role_assignment_is_complete(self):
        """All 12 roles are assigned."""
        roles = get_standard_role_list()
        assert len(roles) == 12
        assert roles.count(Role.idiot) == 1

    def test_sheriff_election_with_clear_winner(self):
        candidates = [1, 3, 5]
        votes = {2: 1, 4: 1, 6: 3, 7: 1, 8: 5, 9: 1}
        winner, tied = elect_sheriff(candidates, votes)
        assert winner == 1
        assert tied == []

    def test_sheriff_election_tie(self):
        candidates = [1, 3]
        votes = {2: 1, 4: 3, 5: 1, 6: 3}
        winner, tied = elect_sheriff(candidates, votes)
        assert winner is None
        assert set(tied) == {1, 3}

    def test_game_ends_when_wolves_eliminated(self):
        """Simulate wolves being eliminated one by one."""
        players = [
            _make_player(1, Role.werewolf, alive=True),
            _make_player(2, Role.werewolf, alive=True),
            _make_player(3, Role.werewolf, alive=True),
            _make_player(4, Role.villager, alive=True),
            _make_player(5, Role.villager, alive=True),
            _make_player(6, Role.villager, alive=True),
            _make_player(7, Role.seer, alive=True),
            _make_player(8, Role.witch, alive=True),
            _make_player(9, Role.hunter, alive=True),
        ]
        # Game should continue
        assert check_winner(players) is None

        # Kill wolf 1
        players[0].is_alive = False
        assert check_winner(players) is None

        # Kill wolf 2
        players[1].is_alive = False
        assert check_winner(players) is None

        # Kill wolf 3 — good wins
        players[2].is_alive = False
        assert check_winner(players) == "good"

    def test_game_ends_on_god_wipe(self):
        """屠神: all gods dead → evil wins."""
        players = [
            _make_player(1, Role.werewolf, alive=True),
            _make_player(2, Role.werewolf, alive=True),
            _make_player(3, Role.werewolf, alive=True),
            _make_player(4, Role.villager, alive=True),
            _make_player(5, Role.villager, alive=True),
            _make_player(6, Role.villager, alive=True),
            _make_player(7, Role.seer, alive=True),
            _make_player(8, Role.witch, alive=True),
            _make_player(9, Role.hunter, alive=True),
        ]
        # Kill seer
        players[6].is_alive = False
        assert check_winner(players) is None

        # Kill witch
        players[7].is_alive = False
        assert check_winner(players) is None

        # Kill hunter — all gods dead
        players[8].is_alive = False
        assert check_winner(players) == "evil"

    def test_phase_sequence_is_valid(self):
        """Verify the expected phase sequence for one full round."""
        from engine.state_machine import next_phase

        phases = [GamePhase.CREATED]
        current = GamePhase.CREATED

        # Night
        current = next_phase(current)
        assert current == GamePhase.NIGHT_START

        current = next_phase(current)
        assert current == GamePhase.WOLF_STRATEGY

        current = next_phase(current)
        assert current == GamePhase.WOLF_KILL

        current = next_phase(current)
        assert current == GamePhase.SEER_CHECK

        current = next_phase(current)
        assert current == GamePhase.WITCH_ACT

        current = next_phase(current, first_round=True)
        assert current == GamePhase.SHERIFF_CAMPAIGN
        current = next_phase(current)
        assert current == GamePhase.SHERIFF_VOTE
        current = next_phase(current)
        assert current == GamePhase.NIGHT_RESOLVE

        current = next_phase(current)
        assert current == GamePhase.WIN_CHECK

        # Game continues → day
        current = next_phase(current, game_over=False)
        assert current == GamePhase.DAY_START

        current = next_phase(current)
        assert current == GamePhase.DAY_SPEECH

        current = next_phase(current)
        assert current == GamePhase.DAY_VOTE

        current = next_phase(current)
        assert current == GamePhase.VOTE_RESOLVE
