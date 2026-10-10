"""Opt-in real Kimi latency check; all evidence is invented, no database access."""
import argparse
import asyncio
import json
import time
from pathlib import Path

from ai.graph import ai_decide
from ai.turn_context import build_speech_turn
from models.game import Role


def fictional_context():
    campaign = [
        {"seat": 9, "speech": "我不是预言家，上警是想听大家的判断，暂时不站边。"},
        {"seat": 5, "speech": "我是预言家，昨晚验6号是好人，警徽流7号、2号。"},
        {"seat": 11, "speech": "我才是预言家，昨晚验3号是好人，警徽流4号、8号。"},
    ]
    texts = [
        "现在5、11对跳，查验都报了好人。我先看两个人回应质疑的方式，不能因为谁说得长就相信谁。9号退水不等于他是好人，我也想听他最后投谁。",
        "11号给我金水，我知道自己是好人，但狼人也能给好人发金水。我暂时偏11，不是认死。他今天如果回避自己的查验理由，我也会重新考虑。",
        "我在11号警徽流里，我接受被验。5号点了7号和2号，这两条警徽流都合理，不能只凭警徽流区分真假。两位预言家都应该解释为什么拿警徽。",
        "我是5号，我坚持昨晚6号是好人。11号后来对跳并发了3号金水，我认为他是狼人。3号愿意重新考虑是好事，但我想知道你具体更相信他的哪句话。",
        "5号说验我是好人，这个结果符合我自己的身份。可我不能只靠自己接金水来站边。我想听11号正面回应5号，尤其是查验对象选择和今天的投票目标。",
        "我没上警，是想先听完两个人的完整发言。现在5号咬11号，11号还没在白天回应，我先不把这个单方面判断当结论。9号的投票理由也得说清。",
        "我是9号，警上说的就是先听信息，所以听完两人后退水。我的警长票投11号，理由是他正面报了查验，我暂时觉得他的态度更可信。我不会把这当成实锤。",
    ]
    # Seats 8, 9, 10 must retain their own statements; no shifted narrator.
    texts.insert(6, "我在11号警徽流里，没有意见。现在3号偏11，6号没有认死5号，两边金水都还算谨慎。谁能把查验、站边和今天要出的目标讲连贯，我就更信谁。")
    texts.append("我是10号。9号退水并投11号，我想听11号接下来的回应。4号接受查验，这不自动证明4号是好人；今天要出的目标还得有理由，不能只凭谁给谁发金水。")
    order = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1]
    heard = [{"seat": seat, "content": text} for seat, text in zip(order[:9], texts)]
    return {
        "round": 1, "sheriff_info": "11号", "sheriff_seat": 11,
        "alive_count": 12, "alive_players": [{"seat": n} for n in range(1, 13)],
        "deaths_this_round": [], "sheriff_speeches": campaign,
        "speeches": heard, "speech_turn": build_speech_turn(11, order, heard, "day_speech"),
        "memory_summary": "【自己掌握的事实】首夜查验3号，结果好人。查验动机：开局随机选座位，没有白天发言依据。\n"
            + "【公开竞选原话】\n" + "\n".join("{seat}号：{speech}".format(**s) for s in campaign)
            + "\n【已听白天发言】\n" + "\n".join("{seat}号：{content}".format(**s) for s in heard),
    }


async def main(output, label, count):
    results = []
    for index in range(count):
        start = time.monotonic()
        result = await ai_decide(11, Role.seer, "logical", "day_speech.txt", fictional_context(),
                                 "speech", model_name="kimi-k3", max_retries=1)
        item = {"sample": index + 1, "fictional": True, "model": "kimi-k3",
                "elapsed_seconds": round(time.monotonic() - start, 2),
                "fallback": result.get("inner_thought") == "（系统回退决策）",
                "speech": result.get("speech"), "action": result.get("action")}
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        Path(output).write_text(json.dumps({"label": label,
            "evidence": "wholly fictional, no live DB or private match replay",
            "fixture_version": 2, "validation_scope": "transport, JSON and action; not semantic correctness",
            "results": results},
            ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--label", default="streaming")
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.output, args.label, args.count))
