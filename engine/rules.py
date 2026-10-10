from typing import Optional, Tuple, List
from models.game import GamePlayer, Role

# Role categories
GOOD_ROLES = {Role.villager, Role.seer, Role.witch, Role.hunter, Role.idiot}
EVIL_ROLES = {Role.werewolf}
GOD_ROLES = {Role.seer, Role.witch, Role.hunter, Role.idiot}  # 神职，白痴算神
CIVILIAN_ROLES = {Role.villager}  # 平民


def check_winner(players: list) -> Optional[str]:
    """
    Check win condition (屠边 rules):
    - All werewolves dead → good wins
    - All gods dead → evil wins (屠神)
    - All villagers dead → evil wins (屠民)
    Returns: 'good', 'evil', or None (game continues)
    """
    alive = [p for p in players if p.is_alive]

    alive_wolves = [p for p in alive if p.role == Role.werewolf]
    alive_gods = [p for p in alive if p.role in GOD_ROLES]
    alive_villagers = [p for p in alive if p.role == Role.villager]

    if not alive_wolves:
        return "good"
    if not alive_gods:
        return "evil"
    if not alive_villagers:
        return "evil"
    return None


def count_votes(votes: dict) -> Tuple[Optional[int], bool]:
    """
    Count votes and determine result.
    votes: {voter_seat: target_seat}
    Returns: (target_seat or None, is_tie)
    """
    if not votes:
        return None, False

    tally: dict[int, int] = {}
    for target in votes.values():
        if target is None:
            continue
        tally[target] = tally.get(target, 0) + 1

    if not tally:
        return None, False

    max_count = max(tally.values())
    top = [seat for seat, count in tally.items() if count == max_count]

    if len(top) == 1:
        return top[0], False
    return None, True  # Tie — no one is voted out


def get_wolf_consensus(votes: dict) -> Optional[int]:
    """Determine wolf kill target from wolf votes. Majority wins, tie = no kill."""
    result, is_tie = count_votes(votes)
    return result


def get_sheriff_weight(seat: int, sheriff_seat: Optional[int]) -> float:
    """Sheriff's vote counts as 1.5 in day vote."""
    return 1.5 if seat == sheriff_seat else 1.0


def count_weighted_votes(votes: dict, sheriff_seat: Optional[int]) -> Tuple[Optional[int], bool]:
    """
    Count votes with sheriff weight (1.5x).
    Returns: (target_seat or None, is_tie)
    """
    if not votes:
        return None, False

    tally: dict[int, float] = {}
    for voter, target in votes.items():
        if target is None:
            continue
        weight = get_sheriff_weight(voter, sheriff_seat)
        tally[target] = tally.get(target, 0) + weight

    if not tally:
        return None, False

    max_count = max(tally.values())
    top = [seat for seat, count in tally.items() if abs(count - max_count) < 0.01]

    if len(top) == 1:
        return top[0], False
    return None, True


def circular_seat_order(start_seat: int, direction: str, alive_seats: List[int],
                        include_start_last: bool = True, total_seats: int = 12) -> List[int]:
    """
    Generate circular speaking order from start_seat.
    direction: "clockwise" (1→2→...→12→1) or "counterclockwise".
    If include_start_last=True: start from next seat after start_seat, append start_seat at end.
    If include_start_last=False: start from start_seat itself, then continue around.
    Only includes seats present in alive_seats.
    """
    if not alive_seats:
        return []

    n = total_seats

    if direction == "counterclockwise":
        ordered = []
        if include_start_last:
            cur = start_seat
            for _ in range(n - 1):
                cur = cur - 1 if cur > 1 else n
                if cur in alive_seats:
                    ordered.append(cur)
            if start_seat in alive_seats:
                ordered.append(start_seat)
        else:
            cur = start_seat
            for _ in range(n):
                if cur in alive_seats:
                    ordered.append(cur)
                cur = cur - 1 if cur > 1 else n
    else:
        # clockwise: start_seat+1, start_seat+2, ...
        ordered = []
        if include_start_last:
            cur = start_seat
            for _ in range(n - 1):
                cur = cur + 1 if cur < n else 1
                if cur in alive_seats:
                    ordered.append(cur)
            if start_seat in alive_seats:
                ordered.append(start_seat)
        else:
            cur = start_seat
            for _ in range(n):
                if cur in alive_seats:
                    ordered.append(cur)
                cur = cur + 1 if cur < n else 1

    return ordered
