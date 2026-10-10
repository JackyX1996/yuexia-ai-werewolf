from typing import Optional, List, Tuple
from models.game import GamePlayer


def elect_sheriff(candidates: List[int], votes: dict) -> Tuple[Optional[int], List[int]]:
    """
    Elect sheriff from candidates based on votes.
    votes: {voter_seat: candidate_seat}
    Returns (winning_seat, tied_seats).
    - If one winner: (seat, [])
    - If tie: (None, [tied_seat1, tied_seat2, ...])
    - If no votes: (None, [])
    """
    if not candidates:
        return None, []

    tally: dict[int, int] = {c: 0 for c in candidates}
    for target in votes.values():
        if target in tally:
            tally[target] += 1

    max_count = max(tally.values())
    if max_count == 0:
        return None, []

    top = [seat for seat, count in tally.items() if count == max_count]
    if len(top) == 1:
        return top[0], []
    return None, top  # Tie


def transfer_sheriff(current_sheriff_seat: int, target_seat: int,
                     alive_players: list) -> Optional[int]:
    """
    Transfer sheriff badge to target. Validates target is alive.
    Returns new sheriff seat or None if invalid.
    """
    alive_seats = {p.seat for p in alive_players}
    if target_seat in alive_seats and target_seat != current_sheriff_seat:
        return target_seat
    return None


def get_sheriff_candidates(alive_players: list) -> List[int]:
    """All alive players can run for sheriff."""
    return [p.seat for p in alive_players]
