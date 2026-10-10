# AI 狼人杀 — 系统规划文档

> 本文保留后续架构规划；2026-10-04 已完成的规则、上下文与 UI 改造见 [游戏审查报告](gameplay-audit.md)。

## 一、当前架构概览

```
┌─────────────┐     WebSocket      ┌──────────────┐
│   Browser    │ ◄──────────────► │   FastAPI     │
│  (app.js)    │     REST API      │  (uvicorn)    │
└─────────────┘                    └──────┬───────┘
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                   ┌────────────┐  ┌────────────┐  ┌────────────┐
                   │   Engine   │  │   AI Layer  │  │  Database  │
                   │ GameManager│  │  LLMClient  │  │   SQLite   │
                   │ StateMachine│ │  Prompts    │  │ SQLAlchemy │
                   └────────────┘  └────────────┘  └────────────┘
```

- 单进程 asyncio 架构，每局游戏一个 background task
- 每局从百炼 + 火山模型池随机抽 11 个不同模型进场
- 12 人预女猎白：4 狼、4 民 + 预言家 / 女巫 / 猎人 / 白痴

---

## 二、已知风险与待优化项

### 2.1 原始并发风险（部分已修复）

本次已加入 45 秒模型请求超时、最多两次尝试、2400 秒真人超时、重连快照；通知前提交事务，释放 SQLite 写锁。下表保留原问题背景，尚未解决的是服务重启续局和多进程任务协调。

| 风险 | 说明 | 建议 |
|------|------|------|
| LLM 调用无超时 | `openai.chat.completions.create()` 无 timeout 参数，若模型服务卡住，整局游戏挂起 | 给每次 LLM 调用加 `asyncio.wait_for(coro, timeout=60)` |
| WebSocket 断连后 future 悬挂 | `_wait_human_action` 使用 `asyncio.Future`，若客户端断连且无超时，future 永远不会 resolve | 已有超时机制（120s），但需确认断连时 future 被正确 cancel |
| asyncio.gather 无超时 | 多个 AI 并发决策用 `asyncio.gather`，一个模型卡住会阻塞所有 | 改用 `asyncio.wait` + timeout，或给每个 task 单独超时 |
| 数据库连接池耗尽 | 多局并发时，每局持有一个 session 直到结束，pool_size=10 可能不够 | 按需调大 pool_size，或在长等待期间释放 session |

### 2.2 建议短期优化

1. **LLM 调用超时**：`LLMClient.chat()` 增加 `timeout` 参数，默认 60s
2. **AI 决策重试**：单个 AI 调用失败时自动重试 1 次，而非整局崩溃
3. **游戏异常恢复**：game_manager 捕获异常后尝试继续而非直接结束
4. **连接池调优**：pool_size 调到 20，加 pool_recycle=3600

---

## 三、规划：多版型支持

### 3.1 目标

支持多种游戏板子，包括但不限于：
- 12 人预女猎白（当前）
- 9 人标准局
- 狼美人骑士局
- 守卫局
- 白狼王局
- 自定义配置局

### 3.2 核心设计：BoardConfig 配置层

```python
@dataclass
class BoardConfig:
    name: str                           # "9人标准", "12人标准"
    player_count: int                   # 9, 12
    role_composition: dict[str, int]    # {"werewolf": 3, "villager": 3, "seer": 1, ...}
    has_sheriff: bool                   # 是否有警长环节
    night_action_order: list[str]       # ["werewolf", "seer", "witch"] 夜晚行动顺序
    win_conditions: list[WinCondition]  # 胜负判定规则
    special_rules: dict                 # 板子特殊规则（如白狼王自爆等）
```

### 3.3 需要重构的模块

| 模块 | 当前问题 | 重构方向 |
|------|----------|----------|
| `Role` enum | 固定 6 个角色 | 改为字符串 + 角色注册表，新角色通过配置注册 |
| `get_standard_role_list()` | 写死 12 人阵容 | 从 BoardConfig 读取 |
| 状态机 | phase 枚举和转换写死 | 数据驱动，根据 BoardConfig.night_action_order 动态生成 |
| 夜晚行动 | 每个角色一个 handler 方法 | 抽象为 `NightAction` 接口，角色注册自己的 action |
| 胜负判定 | 硬编码屠边规则 | 可配置的 WinCondition 策略 |
| 座位数 | 写死 12 | 从 BoardConfig.player_count 读取 |

### 3.4 角色插件化架构（目标）

```python
class RolePlugin:
    """每个角色实现这个接口"""
    name: str                    # "seer", "wolf_beauty", "knight"
    faction: str                 # "good", "evil", "neutral"
    night_priority: int          # 夜晚行动优先级
    prompt_template: str         # AI prompt 模板文件名

    async def night_action(self, ctx: GameContext) -> ActionResult:
        """夜晚技能"""
        ...

    async def day_action(self, ctx: GameContext) -> ActionResult:
        """白天技能（如骑士翻牌）"""
        ...

    def can_use_skill(self, ctx: GameContext) -> bool:
        """技能发动条件（同一角色不同板子可能不同）"""
        ...

# 注册
ROLE_REGISTRY = {
    "seer": SeerPlugin(),
    "wolf_beauty": WolfBeautyPlugin(),
    "knight": KnightPlugin(),
}
```

### 3.5 实施节奏

1. **Phase 1**：抽出 BoardConfig，当前 12 人预女猎白作为默认配置，不改行为
2. **Phase 2**：角色 handler 抽象为 NightAction 接口，现有角色迁移
3. **Phase 3**：状态机改为数据驱动
4. **Phase 4**：实现第一个新板子（如守卫局）验证架构
5. **Phase 5**：开放自定义板子配置

---

## 四、规划：语音输入 / 输出

### 4.1 目标

- 每个 AI 角色有专属语音包，发言内容通过 TTS 朗读出来
- 玩家可以语音输入，系统通过 ASR 转文字后参与游戏

### 4.2 架构设计

```
┌──────────┐    audio stream     ┌──────────────┐
│  Browser  │ ◄────────────────► │   Backend     │
│  Web Audio│    WebSocket        │               │
│  API      │                    │  ┌──────────┐ │
└──────────┘                    │  │ TTS 引擎  │ │  ← AI 发言 → 语音
                                 │  │ ASR 引擎  │ │  ← 玩家语音 → 文字
                                 │  └──────────┘ │
                                 └──────────────┘
```

### 4.3 TTS（AI 语音输出）

| 项目 | 方案 |
|------|------|
| 引擎选型 | 火山引擎语音合成 / 阿里云语音合成 / CosyVoice |
| 音色分配 | 每个 AI 角色绑定一个固定音色 ID，写入 User 表 |
| 触发时机 | AI 发言事件产生后，异步调用 TTS，生成音频推送给前端 |
| 传输方式 | WebSocket 推送音频 URL 或 base64 片段，前端 Web Audio API 播放 |
| 并发控制 | 发言按座位顺序播放，前一个播完再播下一个 |

### 4.4 ASR（玩家语音输入）

| 项目 | 方案 |
|------|------|
| 引擎选型 | 浏览器原生 Web Speech API / 火山引擎 ASR / Whisper |
| 交互方式 | 按住说话（Push-to-Talk），松开后发送音频 |
| 前端采集 | MediaRecorder API 录音，WebSocket 实时传输或录完上传 |
| 后端处理 | 接收音频 → ASR 转文字 → 作为玩家发言输入 |
| 降级方案 | ASR 失败时回退到文字输入 |

### 4.5 数据库变更

```sql
-- User 表新增字段
ALTER TABLE user ADD COLUMN voice_id VARCHAR(100) NULL;  -- TTS 音色 ID
```

### 4.6 实施节奏

1. **Phase 1**：TTS 集成 — AI 发言生成语音，前端播放（只出不进）
2. **Phase 2**：ASR 集成 — 玩家语音输入转文字
3. **Phase 3**：语音包管理 — 后台配置 AI 角色音色
4. **Phase 4**：实时语音流 — 优化延迟，支持流式 TTS

---

## 五、系统层面的长期规划

### 5.1 性能与可靠性

| 方向 | 说明 |
|------|------|
| LLM 调用超时与重试 | 每次调用 60s 超时，失败自动重试 1 次 |
| 游戏状态持久化 | 当前游戏状态仅在内存中，进程重启丢失。考虑关键节点写入 DB，支持断线重连恢复 |
| 多进程部署 | 当前单进程，WebSocket 状态在内存中。如需水平扩展，需引入 Redis 做 pub/sub |
| 监控告警 | 接入日志系统，LLM 调用耗时 / 失败率监控 |

### 5.2 游戏体验

| 方向 | 说明 |
|------|------|
| AI 个性差异化 | 不同性格的 AI 在策略上有明显区别，不只是语气不同 |
| 跨局记忆进化 | AI 记住历史对局中其他玩家的行为模式，逐局变强 |
| 观战模式 | 支持非参与者观看正在进行的游戏 |
| 对局回放 | 游戏结束后可以回放完整对局过程 |
| 多人对战 | 支持多个真人玩家同时参与一局游戏 |

### 5.3 商业化方向（远期）

| 方向 | 说明 |
|------|------|
| 付费语音包 | 热门角色 / 明星音色 |
| 赛季排行榜 | 玩家和 AI 模型的胜率排行 |
| 自定义板子市场 | 玩家创建并分享自定义板子配置 |
