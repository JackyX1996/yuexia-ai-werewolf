"""Opt-in real model checks with a wholly fictional board, never a live DB."""
import argparse
import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace as Event

from ai.graph import ai_decide
from ai.mind import render_mind
from ai.turn_context import build_speech_turn
from models.event import Visibility
from models.game import Role
from engine.roles import get_role_name


ORDER = [2, 9, 5, 11, 4]
PUBLIC = [
    {"seat": 2, "speech": "我先上来听信息，后面9、5、11、4还没说，想跳预言家的报一下查验。"},
    {"seat": 9, "speech": "我是预言家，昨晚验6号是好人。2号你想听查验，我报了。后面愿意说的先讲讲立场。"},
    {"seat": 5, "speech": "目前9号报了预言家，我先听11号和4号。上警不代表我跳神；如果有人对跳，再对比查验。"},
    {"seat": 11, "speech": "我才是预言家，昨晚验1号是好人。9号是跟我对跳的，警徽流留3号、8号。"},
    {"seat": 4, "speech": "现在9、11两个人跳了预言家，等PK补充依据再决定。"},
]


def memory(seat, role, heard):
    events = [Event(round=1, event_type="sheriff_campaign", actor_seat=s["seat"],
        target_seat=None, content=s["speech"], visibility=Visibility.all) for s in heard]
    if role == Role.seer:
        events.insert(0, Event(round=1, event_type="seer_result", actor_seat=seat,
            target_seat=6, content="好人", visibility=Visibility.self_only))
    return render_mind(seat, get_role_name(role), events, alive_seats=range(1, 13), current_round=1)


async def sample(name, seat, role, model, heard, previous=None):
    order = [9, 11] if previous else ORDER
    context = {
        "seat": seat, "role": role.value, "candidate_seats": sorted(order),
        "speeches": heard, "previous_speeches": previous or [],
        "is_tiebreak": bool(previous),
        "original_candidate_seats": sorted(ORDER),
        "speech_turn": build_speech_turn(seat, order, heard, "sheriff_pk" if previous else "sheriff_campaign"),
        "memory_summary": memory(seat, role, (previous or []) + heard),
        "wolf_teammates": [{"seat": s} for s in [3, 8, 10]] if role == Role.werewolf else [],
    }
    started = time.monotonic()
    result = await ai_decide(seat, role, "logical", "sheriff_speech.txt", context,
                             "sheriff_speech", model_name=model,
                             extra_valid_types=["wolf_explode"] if role == Role.werewolf else None)
    return {"scenario": name, "fictional": True, "seat": seat, "model": model,
            "spoken_seats": context["speech_turn"]["spoken_seats"],
            "pending_seats": context["speech_turn"]["pending_seats"],
            "has_previous_pass": bool(previous), "speech": result.get("speech"),
            "action": result.get("action"),
            "fallback": result.get("inner_thought") == "（系统回退决策）",
            "elapsed_seconds": round(time.monotonic() - started, 2)}


async def main(output_path):
    # Two independent requests at a time; no real game records are loaded.
    results = []
    for group in [
        [sample("seer_after_first_speaker", 9, Role.seer, "MiniMax-M2.5", PUBLIC[:1]),
         sample("villager_before_counterclaim", 5, Role.villager, "deepseek-v4-pro", PUBLIC[:2])],
        [sample("wolf_counterclaim_after_public_seer", 11, Role.werewolf,
                "doubao-seed-2-1-pro-260915", PUBLIC[:3]),
         sample("seer_pk_distinguishes_previous_claims", 9, Role.seer,
                "MiniMax-M2.5", [], PUBLIC)],
    ]:
        results.extend(await asyncio.gather(*group))
    report = {"mode": "real configured models, entirely fictional evidence; no live DB",
              "semantic_review": "pending manual inspection; JSON validity alone is insufficient",
              "results": results}
    Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/speech-context-eval-20261005.json")
    args = parser.parse_args()
    asyncio.run(main(args.output))
