"""Resume only completed day boundaries; never replay an unfinished skill."""
from typing import List, Optional

from engine.rules import check_winner
from models.game import CauseOfDeath, GameStatus, Role
from models.event import Visibility


DAY_BOUNDARY_PHASES = {"day_vote", "vote_resolve", "last_words", "win_check"}


def completed_day_checkpoint(game, players: List, events: List) -> Optional[dict]:
    if game.status != GameStatus.running or game.current_phase not in DAY_BOUNDARY_PHASES:
        return None
    if check_winner(players) is not None:
        return None
    checkpoint = (game.config or {}).get("resume_checkpoint")
    if checkpoint:
        if (checkpoint.get("kind") == "day_complete"
                and checkpoint.get("round") == game.current_round
                and game.current_phase == "win_check"):
            return dict(checkpoint)
        return None

    # Compatibility with games recorded before durable checkpoints existed.
    # Require an actual voted death followed by public last words. A hunter's
    # unrecorded decision or an unresolved sheriff transfer cannot be inferred.
    current = [e for e in events if e.round == game.current_round]
    votes = [e for e in current if e.event_type == "vote_result" and e.visibility == Visibility.all]
    if not votes:
        return None
    vote = max(votes, key=lambda e: e.id)
    dead = next((p for p in players if p.seat == vote.target_seat), None)
    if (dead is None or dead.is_alive or dead.cause_of_death != CauseOfDeath.vote
            or dead.died_at_round != game.current_round or dead.role == Role.hunter
            or game.sheriff_player_seat == dead.seat):
        return None
    words = [e for e in current if e.event_type == "last_words"
             and e.visibility == Visibility.all and e.actor_seat == dead.seat and e.id > vote.id]
    if not words:
        return None
    knives = [e for e in current if e.event_type == "wolf_kill" and e.target_seat is not None]
    last_target = max(knives, key=lambda e: e.id).target_seat if knives else None
    return {"kind": "day_complete", "round": game.current_round,
            "last_wolf_target": last_target, "source": "completed_exile_events"}
