from types import SimpleNamespace

from engine.events import extract_key_events


def test_idiot_reveal_is_not_an_exile_even_in_historical_events():
    reveal = SimpleNamespace(event_type="idiot_reveal", round=2, actor_seat=5,
                             target_seat=None, content="白痴翻牌")
    ballot = SimpleNamespace(event_type="vote_result", round=2, actor_seat=None,
                             target_seat=5, content="5号白痴翻牌免死")
    events = extract_key_events([reveal, ballot])
    assert len(events) == 1
    assert events[0]["type"] == "idiot"
    assert "免死" in events[0]["text"]
    assert "出局" not in events[0]["text"]


def test_badge_destruction_is_in_replay():
    event = SimpleNamespace(event_type="sheriff_transfer", round=3, actor_seat=1,
                            target_seat=None, content="1号撕掉了警徽")
    assert "撕掉" in extract_key_events([event])[0]["text"]
