from ai.llm_client import get_llm_client


COMPRESS_SYSTEM_PROMPT = """你是一个狼人杀游戏的记忆整理助手。你需要将玩家的游戏事件记录整理成完整、准确的记忆摘要。

核心原则：宁可多记，不可遗漏。所有有价值的信息都必须保留。

必须记录的信息（按优先级）：

1. 技能使用记录（最重要！）
   - 女巫：解药是否已用（何时用的、救了谁）、毒药是否已用（何时用的、毒了谁）
   - 预言家：每一轮查验了谁、结果是好人还是狼人（完整列表）
   - 猎人：是否已开枪、带走了谁

2. 死亡记录
   - 每一轮谁死了、死因是什么（狼刀/投票/女巫毒/猎人枪）
   - 当前还存活的玩家列表

3. 警长信息
   - 谁是警长、怎么当选的
   - 警长是否已死、警徽移交给了谁

4. 竞选信息
   - 谁上警了、谁跳了什么身份（特别是跳预言家的）
   - 谁退水了、竞选演说的关键内容

5. 投票记录
   - 每轮投票的票型（谁投了谁）和结果

6. 发言要点
   - 各玩家的关键发言立场和逻辑
   - 谁指认了谁、谁为谁站台

7. 怀疑判断
   - 基于以上信息，哪些玩家可能是狼人、哪些可能是好人、理由是什么"""

COMPRESS_USER_TEMPLATE = """请整理以下游戏事件记录。我是{seat}号位，身份是{role}。

事件记录：
{events_text}

之前的记忆摘要：
{previous_summary}

请回复JSON格式：
{{
  "summary": "完整的记忆摘要（第一人称，务必包含所有技能使用记录、死亡记录、投票记录、竞选信息等关键信息，不要遗漏）",
  "suspicion_map": {{"座位号": "判断理由（好人/狼人/不确定，附简要理由）"}},
  "key_facts": ["关键事实1（如：第1轮我用解药救了5号）", "关键事实2（如：查验3号结果为狼人）"]
}}"""


async def compress_memory(seat: int, role: str, events_text: str,
                          previous_summary: str = "") -> dict:
    """Compress game events into a narrative memory summary."""
    user_prompt = COMPRESS_USER_TEMPLATE.format(
        seat=seat, role=role,
        events_text=events_text,
        previous_summary=previous_summary or "（无）",
    )
    result = await get_llm_client().chat(
        system_prompt=COMPRESS_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.3,
        json_mode=True,
    )
    return result
