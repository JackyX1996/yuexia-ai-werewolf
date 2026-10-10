"""Read-only, local reconstruction of initial campaign evidence boundaries.

No model calls and no private prompt text in the report. This is not an
archived copy of the original request: rendering uses the current code.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from ai.mind import render_mind
from ai.turn_context import build_speech_turn
from config import settings
from engine.roles import get_role_name
from models.event import Visibility
from models.game import Role


def audit(db_path: str, game_id: int) -> dict:
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        events = [dict(e) for e in conn.execute(
            "SELECT * FROM game_event WHERE game_id=? ORDER BY id", (game_id,))]
        roster = [dict(p) for p in conn.execute(
            "SELECT seat,role,nickname,is_human FROM game_player WHERE game_id=? ORDER BY seat", (game_id,))]
    finally:
        conn.close()
    stop_id = next((e["id"] for e in events if e["event_type"] in
        ("sheriff_withdraw", "sheriff_vote", "sheriff_elected")), float("inf"))
    campaign = [e for e in events if e["event_type"] == "sheriff_campaign"
                and e["visibility"] == "all" and e["id"] < stop_id]
    order = [e["actor_seat"] for e in campaign]
    players = {p["seat"]: p for p in roster}
    checks = []
    for index, speech in enumerate(campaign):
        seat = speech["actor_seat"]
        player = players[seat]
        role = Role(player["role"])
        preceding = [e for e in events if e["id"] < speech["id"]]
        cutoff = speech["id"] - 1
        # Own thought is saved after the response, immediately before public
        # speech. It was not part of that response's original input.
        if preceding and preceding[-1]["event_type"] == "sheriff_campaign" \
                and preceding[-1]["visibility"] == "self_only" \
                and preceding[-1]["actor_seat"] == seat:
            cutoff = preceding[-1]["id"] - 1
        visible = []
        for e in events:
            if e["id"] > cutoff:
                break
            v = e["visibility"]
            if v == "all" or (v == "self_only" and e["actor_seat"] == seat) \
                    or (v == "werewolves" and role == Role.werewolf):
                visible.append(SimpleNamespace(**{**e, "visibility": Visibility[v]}))
        mind = render_mind(seat, get_role_name(role), visible,
            nicknames={p["seat"]: p["nickname"] or "" for p in roster},
            nickname=player["nickname"] or "", alive_seats=players.keys(),
            current_round=1, max_chars=settings.context_memory_chars)
        turn = build_speech_turn(seat, order,
            [{"seat": e["actor_seat"]} for e in campaign[:index]], "sheriff_campaign")
        checks.append({
            "seat": seat, "is_human": bool(player["is_human"]),
            "input_event_cutoff": cutoff, "public_speech_event": speech["id"],
            "spoken_seats": turn["spoken_seats"], "pending_seats": turn["pending_seats"],
            "foreign_personal_thoughts": sum(e.visibility == Visibility.self_only
                                             and e.actor_seat != seat for e in visible),
            "later_public_speech_in_memory": any(e["content"] and e["content"] in mind
                                                 for e in campaign[index + 1:]),
            "reconstructed_memory_sha256": hashlib.sha256(mind.encode()).hexdigest(),
        })
    return {"game_id": game_id, "mode": "read-only local reconstruction; no LLM calls",
            "original_request_archived": False, "campaign_order": order, "checks": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--game-id", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = audit(args.db, args.game_id)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"campaign_order": report["campaign_order"],
                     "checked_turns": len(report["checks"]),
                     "future_speech_leaks": sum(c["later_public_speech_in_memory"] for c in report["checks"]),
                     "foreign_thought_leaks": sum(c["foreign_personal_thoughts"] for c in report["checks"])}, ensure_ascii=False))
