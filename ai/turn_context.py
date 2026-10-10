"""Exact speaking positions, independent of role guesses or seat numbering."""
from typing import Dict, List


def build_speech_turn(seat: int, order: List[int], speeches: List[dict],
                      kind: str) -> Dict:
    """Only a completed prefix of this pass can be evidence for this turn.

    Earlier passes belong in separately labelled history, not this list.
    Fail before calling a model if the engine supplies future/duplicate speech.
    """
    if len(set(order)) != len(order) or seat not in order:
        raise ValueError("invalid speech order")
    position = order.index(seat)
    spoken = [s["seat"] for s in speeches]
    if spoken != order[:position]:
        raise ValueError("speech evidence must be the completed prefix of this pass")
    return {
        "kind": kind,
        "self_seat": seat,
        "order": list(order),
        "spoken_seats": list(spoken),
        "pending_seats": list(order[position + 1:]),
    }
