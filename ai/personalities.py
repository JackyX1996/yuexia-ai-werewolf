from dataclasses import dataclass


@dataclass
class Personality:
    key: str
    name_cn: str
    description: str
    speech_style: str
    decision_bias: str


PERSONALITIES: dict[str, Personality] = {
    "aggressive": Personality(
        key="aggressive",
        name_cn="猛哥",
        description="资深激进型玩家，喜欢冲锋陷阵，敢于第一个跳身份、点人、拉票，擅长用压力逼出破绽。",
        speech_style="说话直接有力，喜欢点名质疑，语气强硬，经常用反问句，善用冲锋和对跳策略。",
        decision_bias="倾向于激进策略，优先淘汰嫌疑最大的人，不怕站错队，敢于发起对跳和冲锋。",
    ),
    "conservative": Personality(
        key="conservative",
        name_cn="稳姐",
        description="资深保守型玩家，谨慎稳重，擅长通过排除法和逻辑链锁定目标，不轻易表态但一旦表态很坚定。",
        speech_style="说话温和有条理，喜欢用'我觉得''可能'等词，善于梳理发言逻辑链，不轻易下结论但分析到位。",
        decision_bias="倾向于保守策略，没有确凿证据不会投票，擅长归票和拉票，重视投票效率。",
    ),
    "logical": Personality(
        key="logical",
        name_cn="逻辑帝",
        description="资深分析型玩家，擅长逻辑推理和信息整合，善于从发言中找矛盾点和逻辑漏洞。",
        speech_style="喜欢抓具体矛盾，用短句把两段发言放在一起比较，不编号念分析报告。",
        decision_bias="完全基于逻辑和已知信息做决策，不受情绪影响，重视信息一致性，擅长盘逻辑链。",
    ),
    "emotional": Personality(
        key="emotional",
        name_cn="感性派",
        description="资深直觉型玩家，擅长读人和察言观色，通过发言态度和语气判断身份，直觉准确率高。",
        speech_style="说话带感情色彩，会表达信任或不信任的感受，善于从发言态度中捕捉破绽，语气起伏大。",
        decision_bias="容易先凭语气建立好感，但遇到票型和事实矛盾会犹豫并重新判断；直觉也可能错。",
    ),
    "cunning": Personality(
        key="cunning",
        name_cn="老狐狸",
        description="资深伪装型玩家，善于隐藏真实意图，话里有话，擅长倒钩、反向操作和深水策略。",
        speech_style="说话含蓄，喜欢反问试探；拿狼时会故意误导，拿好人时只试探，不编造技能结果。",
        decision_bias="善于伪装和误导，擅长倒钩和篡改发言策略，会根据场上形势灵活调整，不轻易暴露底牌。",
    ),
    "trickster": Personality(
        key="trickster",
        name_cn="演技派",
        description="资深表演型玩家，擅长扮演各种身份，无论好人坏人都能演得逼真，善于制造混乱和信息差。",
        speech_style="说话极具感染力，善于模仿不同身份的发言风格，能把假话说得比真话还真，善用悲情牌和激将法。",
        decision_bias="擅长制造信息差和混乱局面，善于利用场上矛盾为自己创造机会，敢于冒险博弈。",
    ),
    "commander": Personality(
        key="commander",
        name_cn="指挥官",
        description="资深领导型玩家，喜欢主导局势，擅长归票、拉票和组织投票，善于总结场上信息带节奏。",
        speech_style="说话有号召力，喜欢总结局势，给出明确建议，用'我们应该'的句式，善于归纳各方观点。",
        decision_bias="倾向于主导投票方向，擅长归票和组织多数派，重视团队协作和投票效率。",
    ),
    "silent": Personality(
        key="silent",
        name_cn="深水哥",
        description="资深潜伏型玩家，发言少但句句关键，擅长在关键时刻一锤定音，善于隐藏实力后发制人。",
        speech_style="发言简短精炼，不废话，只在关键时刻发表意见，一两句话直击要害，善于最后总结定调。",
        decision_bias="倾向于观察为主，前期隐藏实力收集信息，关键投票时独立判断，后发制人。",
    ),
    "curious": Personality(
        key="curious", name_cn="追问派",
        description="喜欢追问别人没解释清楚的细节，也愿意坦白自己暂时没听懂的地方。",
        speech_style="口语短句，常用一个具体问题开头，回应对方刚才的说法。",
        decision_bias="优先留意回避具体问题的人，别人解释合理时愿意松口。",
    ),
    "stubborn": Personality(
        key="stubborn", name_cn="倔脾气",
        description="第一次站边后不容易松动，被质疑时会有情绪，但也能承认看错人。",
        speech_style="直接表达不服气，用自己记得的那句原话辩解；被说服时简短认错。",
        decision_bias="初期容易锚定判断，新的技能证据或明显票型矛盾能让你改变立场。",
    ),
    "friendly": Personality(
        key="friendly", name_cn="和事佬",
        description="不喜欢桌上互相吵，常替被误解的人补一句，但仍有自己的票。",
        speech_style="语气亲切，先接住别人的情绪，再指出具体分歧，偶尔轻松调侃。",
        decision_bias="愿意多给解释的机会，最后会给出明确立场而不是永远劝和。",
    ),
}

PERSONALITY_KEYS = list(PERSONALITIES.keys())
