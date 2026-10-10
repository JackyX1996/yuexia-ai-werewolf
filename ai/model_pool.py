"""Legacy import/preview pool and model-specific request compatibility.

Live games sample saved connections in ai/model_configuration.py.
MATCH_MODELS is only used for one-time legacy imports and simulated previews.

百炼文本模型（2026-10-05 完成生产参数的虚构开局验证）：千问、GLM、
MiniMax-M2.5、DeepSeek V4。火山方舟只保留豆包和 deepseek-v4-1-flash-260910。
kimi-k3、qwen3.7-flash 已永久移出对局池。Kimi 思考关不掉，长上下文会把整段
决策时限耗在推理里。调用适配仍保留，避免已经发到这些模型的旧座位再因参数不兼容失败。
百炼上的 deepseek-v4-* 不能按 deepseek 前缀送去火山。
混元不在百炼文本列表里。MiniMax/MiniMax-M3、stepfun/step-3.7-flash
在平台上可见，但并非所有账号都已开通，不放进默认随机池，避免座位整局回退。
"""
import random
from typing import List, Optional

# Exact ids that must stay on Volcengine Ark. Prefix "deepseek" is not enough:
# deepseek-v4-flash-0731 / deepseek-v4-pro are Bailian-hosted.
# deepseek-v4-flash（无日期后缀）已在百炼下线，不要再写回对局池。
VOLCENGINE_MODELS = frozenset({
    "doubao-seed-2-1-lite-260915",
    "doubao-seed-2-1-pro-260915",
    "doubao-seed-evolving",
    "deepseek-v4-1-flash-260910",
})

# These reject response_format=json_object. The client still asks for JSON in
# the prompt and parses it out of the text.
NO_JSON_MODE_MODELS = frozenset({
    "MiniMax-M2.5",
    "MiniMax-M3",
    "MiniMax/MiniMax-M3",
})

# Thinking cannot be switched off. Do not send enable_thinking=false.
THINKING_ONLY_MODELS = frozenset({
    "MiniMax-M2.5",
    "kimi-k3",
    "kimi/kimi-k3",
})

# https://help.aliyun.com/zh/model-studio/kimi-api
# Kimi K3 rejects the global game's 0.75 temperature, even when explicitly
# overridden by a caller. Use the provider's supported fixed value.
FIXED_TEMPERATURES = {"kimi-k3": 1.0, "kimi/kimi-k3": 1.0}

MATCH_MODELS = [
    # 百炼托管，走 dashscope compatible-mode
    "qwen3.7-plus",
    "qwen3.8-flash",
    "qwen3.8-max",
    "glm-5.3",
    "glm-5.2",
    "MiniMax-M2.5",
    "deepseek-v4-flash-0731",
    "deepseek-v4-pro",
    # 火山方舟
    "doubao-seed-2-1-lite-260915",
    "doubao-seed-2-1-pro-260915",
    "doubao-seed-evolving",
    "deepseek-v4-1-flash-260910",
]


def pick_match_models(count: int, rng: Optional[random.Random] = None) -> List[str]:
    """Sample `count` models for one match. Distinct while the pool is large enough."""
    chooser = rng or random
    pool = list(MATCH_MODELS)
    if count <= 0:
        return []
    if count <= len(pool):
        return chooser.sample(pool, count)
    picked = chooser.sample(pool, len(pool))
    while len(picked) < count:
        picked.append(chooser.choice(pool))
    return picked


def is_volcengine_model(model_name: str) -> bool:
    name = model_name or ""
    if name in VOLCENGINE_MODELS:
        return True
    return name.startswith("doubao")


def supports_json_mode(model_name: str) -> bool:
    return (model_name or "") not in NO_JSON_MODE_MODELS


def streams_reasoning_for(model_name: str) -> bool:
    # K3 cannot disable or cap thinking_budget. Streaming keeps active thinking
    # from hitting the non-streaming 45-second read timeout.
    return (model_name or "") in FIXED_TEMPERATURES


def requires_thinking(model_name: str) -> bool:
    lower = (model_name or "").lower()
    return (lower in {"kimi-k3", "kimi/kimi-k3"}
            or lower.split("/")[-1].startswith("minimax-m2")
            or lower.startswith("glm-5.3") or lower.startswith("zhipu/glm-5.3"))


def extra_body_for(model_name: str, provider: str = None,
                   thinking_enabled: bool = False, thinking_format: str = "auto") -> dict:
    """Every live request uses fixed-off Thinking parameters."""
    name = model_name or ""
    lower = name.lower()
    if thinking_enabled:
        raise ValueError("Thinking 固定关闭，不能开启。")
    if requires_thinking(name):
        raise ValueError("模型「{0}」不支持关闭 Thinking，请更换模型。".format(name))
    if provider == "openai":
        if thinking_format == "auto":
            if lower.startswith(("gpt-3.5", "gpt-4", "chatgpt-4")):
                thinking_format = "none"
            elif lower.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
                thinking_format = "reasoning_effort"
            elif lower.startswith("doubao") or "minimax-m3" in lower:
                thinking_format = "thinking"
            else:
                thinking_format = "enable_thinking"
        if thinking_format == "none":
            if lower.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
                raise ValueError("该模型需要明确关闭 Thinking，请使用自动适配或 reasoning_effort。")
            return {}
        if thinking_format == "thinking":
            return {"thinking": {"type": "disabled"}}
        if thinking_format == "reasoning_effort":
            return {"reasoning_effort": "none"}
        return {"enable_thinking": False}
    if "minimax-m3" in lower:
        return {"thinking": {"type": "disabled"}}
    if provider == "volcengine" or (provider is None and is_volcengine_model(name)):
        return {"thinking": {"type": "disabled"}}
    return {"enable_thinking": False}


def temperature_for(model_name: str, requested: float) -> float:
    return FIXED_TEMPERATURES.get(model_name or "", requested)


def max_tokens_for(model_name: str, default: int) -> int:
    name = model_name or ""
    lower = name.lower()
    if name in FIXED_TEMPERATURES:
        # K3 cannot turn off reasoning. Leave room for reasoning plus final JSON.
        return max(default, 8192)
    if name in THINKING_ONLY_MODELS or lower.startswith("minimax"):
        return max(default, 4096)
    if lower.startswith("glm-5.3") or lower.startswith("zhipu/glm-5.3"):
        return max(default, 2048)
    return default
