"""The mind brief should read like a person, not an event log."""
from types import SimpleNamespace

import pytest

from ai.mind import clip_experience, render_mind
from models.event import Visibility


def _e(**kwargs):
    defaults = {
        "round": 1,
        "phase": "day",
        "event_type": "day_speech",
        "actor_seat": None,
        "target_seat": None,
        "content": "",
        "visibility": Visibility.all,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestRenderMind:
    def test_fresh_game_is_a_person_not_an_empty_log(self):
        text = render_mind(seat=7, role_name="预言家", events=[], nickname="逻辑帝")
        assert "事件" not in text
        assert "逻辑帝" in text
        assert "预言家" in text
        assert "刚开" in text

    def test_seer_remembers_as_himself(self):
        events = [
            _e(event_type="seer_result", actor_seat=7, target_seat=5,
               content="狼人", visibility=Visibility.self_only, phase="seer_check"),
            _e(event_type="sheriff_campaign", actor_seat=7,
               content="我是预言家，昨晚验了5号，查杀。", visibility=Visibility.all),
            _e(event_type="sheriff_campaign", actor_seat=3,
               content="我才是预言家，7号悍跳，1号金水。", visibility=Visibility.all),
            _e(event_type="sheriff_campaign", actor_seat=7,
               content="3号对跳了，我必须把查杀说死。", visibility=Visibility.self_only),
            _e(event_type="day_speech", actor_seat=5,
               content="我是好人，7号在装预言家。", visibility=Visibility.all),
            _e(event_type="day_speech", actor_seat=7,
               content="5号查杀，今天归票5号。", visibility=Visibility.all),
            _e(event_type="day_speech", actor_seat=7,
               content="5号在洗，今天必须归票。", visibility=Visibility.self_only),
            _e(event_type="vote_result", target_seat=5,
               content="投票结束，5号玩家被票出局。（投票详情：7号→5号、1号→5号、3号→7号）",
               visibility=Visibility.all),
        ]
        text = render_mind(
            seat=7, role_name="预言家", nickname="逻辑帝", events=events,
            nicknames={3: "阿强", 5: "小美", 7: "逻辑帝"},
            alive_seats={1, 2, 3, 4, 6, 7, 8, 9},
        )
        assert "第1夜我查了5号，是狼人" in text
        assert "只有我知道" in text
        assert "不能打脸" in text
        assert "我是预言家" in text
        assert "阿强" in text
        assert "投过我" in text
        assert "小美" in text
        assert "被票出局" in text
        assert "接着往下想" in text
        assert "[day]" not in text
        assert "event_type" not in text

    def test_wolf_knife_stays_private_and_is_warned(self):
        events = [
            _e(event_type="wolf_discuss", actor_seat=2, content="刀7号预言家。",
               visibility=Visibility.werewolves),
            _e(event_type="wolf_kill", target_seat=7, content="狼人决定击杀7号位",
               visibility=Visibility.werewolves),
        ]
        wolf = render_mind(seat=4, role_name="狼人", events=events, alive_seats={1, 2, 3, 4, 5, 6, 7, 8, 9})
        assert "刀了7号" in wolf
        assert "不能提刀口" in wolf
        assert "狼队友" in wolf
        assert "狼队最近商量的计划" in wolf
        assert "刀7号预言家" in wolf

        villager = render_mind(
            seat=1, role_name="平民",
            events=[_e(event_type="death_announce", content="昨晚，7号玩家倒了。")],
            alive_seats={1, 2, 3, 4, 5, 6, 8, 9},
        )
        assert "刀了" not in villager
        assert "7号" in villager

    def test_witch_remembers_her_own_potions(self):
        text = render_mind(
            seat=8, role_name="女巫",
            events=[
                _e(event_type="witch_save", actor_seat=8, target_seat=3,
                   visibility=Visibility.self_only, phase="witch_act"),
            ],
            alive_seats={1, 2, 3, 4, 5, 6, 7, 8, 9},
        )
        assert "解药救了3号" in text
        assert "暴露女巫" in text
        assert "毒药还在" in text
        assert "解药还在" not in text

    def test_other_peoples_thoughts_are_not_adopted(self):
        text = render_mind(
            seat=1, role_name="平民",
            events=[
                _e(event_type="day_speech", actor_seat=3,
                   content="我其实是狼，别投我。", visibility=Visibility.self_only),
                _e(event_type="day_speech", actor_seat=3,
                   content="我是平民，过。", visibility=Visibility.all),
            ],
        )
        assert "我其实是狼" not in text
        assert "原话：「我是平民，过。」" in text

    def test_clip_experience_keeps_a_habit_not_an_essay(self):
        long = "我容易被悍跳带跑。" + "这是一段不该带进下一局的攻略。" * 20
        clipped = clip_experience(long, limit=40)
        assert len(clipped) <= 40
        assert clipped.startswith("我容易被悍跳带跑。")

    def test_short_cross_game_quotes_are_removed_before_the_length_limit(self):
        text = "7号刚才说不想带节奏，后面却归票。我容易被强势发言带跑。"
        assert clip_experience(text) == "我容易被强势发言带跑。"

    def test_cross_game_seats_and_round_results_are_not_habits(self):
        text = "第三夜验到了狼。三号是预言家。上局我支持了他。我习惯先核对依据。"
        assert clip_experience(text) == "我习惯先核对依据。"


def test_personality_reaches_the_system_prompt():
    from ai.graph import _build_system_prompt
    from models.game import Role

    prompt = _build_system_prompt(3, Role.villager, personality_key="aggressive", experience="我被点名就急。")
    assert "猛哥" in prompt
    assert "说话直接有力" in prompt
    assert "你打牌的习惯" in prompt
    assert "我被点名就急。" in prompt
    assert "对战经验" not in prompt


def test_wrong_role_cannot_receive_wolf_memory_even_if_caller_is_wrong():
    text = render_mind(1, "平民", [
        _e(event_type="wolf_discuss", actor_seat=2, content="秘密计划", visibility=Visibility.werewolves),
        _e(event_type="wolf_kill", target_seat=7, visibility=Visibility.werewolves),
        _e(content="system secret", visibility=Visibility.system),
    ])
    assert "秘密计划" not in text
    assert "刀了7号" not in text
    assert "system secret" not in text


def test_recent_evidence_preserves_claims_and_targets_beyond_first_sentence():
    content = "先接一下前置位，我没有跟风。" + "我们需要听完每个人的理由。" * 5 + "我是预言家，11号查杀，警徽流给2号。"
    text = render_mind(5, "平民", [_e(actor_seat=3, content=content)], current_round=1)
    assert "11号查杀，警徽流给2号" in text
    assert "身份宣称尚未证实" in text


def test_idiot_vote_does_not_turn_survivor_into_a_dead_player():
    text = render_mind(2, "平民", [_e(event_type="vote_result", target_seat=3,
        content="3号被放逐，但翻出白痴，免死并失去投票权。（2号→3号）")], alive_seats=[2, 3])
    assert "3号被票出局" not in text
    assert "免死" in text


def test_old_private_skills_survive_long_game_and_context_remains_bounded():
    events = [_e(event_type="seer_result", actor_seat=1, target_seat=12,
                 content="狼人", visibility=Visibility.self_only)]
    for rnd in range(1, 41):
        for seat in range(1, 13):
            events.append(_e(round=rnd, actor_seat=seat, content="发言" * 200))
        events.append(_e(round=rnd, event_type="day_vote", actor_seat=1, target_seat=2,
                         visibility=Visibility.self_only))
    text = render_mind(1, "预言家", events, current_round=40, max_chars=6500)
    assert "第1夜我查了12号，是狼人" in text
    assert len(text) <= 6500


@pytest.mark.parametrize("speech", [
    "12号跳预言家，发3号金水，我听到了。我不是来跳神的。",
    "12号说‘我是预言家’，但我还没有站边。",
    "我不是预言家，也没发过金水，别把12号的查验算到我头上。",
    "如果8号等下说我是预言家，我们再对比查验。",
])
def test_mentions_quotes_negations_and_predictions_are_not_rewritten_as_claims(speech):
    text = render_mind(7, "猎人", [_e(actor_seat=10, content=speech)])
    impression = text.split("【我记得他们的表现，身份宣称尚未证实】")[1].split("【")[0]
    assert "10号（还在）：原话：「" + speech + "」" in impression
    assert "跳了预言家" not in impression
    assert "发了金水" not in impression


def test_ownerless_private_event_is_not_public_memory():
    text = render_mind(7, "猎人", [_e(event_type="death_announce", actor_seat=None,
        content="不属于任何玩家的私密信息", visibility=Visibility.self_only)])
    assert "不属于任何玩家的私密信息" not in text


def test_original_check_motive_survives_later_subjective_thoughts():
    events = [
        _e(event_type="seer_check", actor_seat=9, target_seat=6,
           content="首夜还没有公开发言，先按位置查一下6号。", visibility=Visibility.self_only),
        _e(event_type="seer_result", actor_seat=9, target_seat=6,
           content="好人", visibility=Visibility.self_only),
    ] + [_e(event_type="sheriff_campaign", actor_seat=9,
             content="后来在警上考虑站边。", visibility=Visibility.self_only) for _ in range(4)]
    text = render_mind(9, "预言家", events)
    assert "当时的主观理由是：「首夜还没有公开发言，先按位置查一下6号。」" in text
    assert "不能用后来警上谁没说话" in text


def test_other_players_check_reason_is_never_adopted():
    text = render_mind(7, "猎人", [_e(event_type="seer_check", actor_seat=9,
        target_seat=6, content="不属于我的验人理由", visibility=Visibility.self_only)])
    assert "不属于我的验人理由" not in text


def test_public_signup_is_a_fact_even_before_any_speech_or_private_thought():
    text = render_mind(9, "预言家", [
        _e(event_type="sheriff_signup", actor_seat=2, target_seat=2),
        _e(event_type="sheriff_signup", actor_seat=9, target_seat=9),
        _e(event_type="sheriff_signup", actor_seat=7),
    ])
    assert "上警：2号、9号。" in text
    assert "查验对象、金水和警徽流对象不是候选名单" in text


def test_private_intent_to_signup_is_not_a_published_candidate_list():
    text = render_mind(9, "预言家", [_e(event_type="sheriff_signup", actor_seat=9,
        target_seat=9, content="我准备上警", visibility=Visibility.self_only)])
    assert "主持人公布的首日报名名单" not in text
