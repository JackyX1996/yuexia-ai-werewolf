"""Seat play versus an observing god game."""


def game_mode(game) -> str:
    mode = ((getattr(game, "config", None) or {}).get("mode") or "seat")
    return mode if mode in ("seat", "god") else "seat"


def is_god_mode(game) -> bool:
    return game_mode(game) == "god"


def observer_user_id(game):
    raw = (getattr(game, "config", None) or {}).get("observer_user_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
