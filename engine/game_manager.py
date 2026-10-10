"""
Game Manager — orchestrates the full game loop.
Connects engine (state machine, rules) + AI (graph, memory) + DB (repository).
"""
import asyncio
import random
import traceback
import uuid
import time
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.repository import GameRepository
from engine.state_machine import GamePhase, next_phase
from engine.rules import (
    check_winner, count_weighted_votes, get_wolf_consensus, circular_seat_order,
    get_sheriff_weight,
)
from engine.roles import (
    get_standard_role_list, get_role_name, get_role_team, ROLE_CONFIGS,
    can_hunter_shoot, idiot_survives_exile,
)
from engine.sheriff import elect_sheriff, transfer_sheriff
from engine.events import extract_key_events
from engine.nicknames import assign_nicknames
from engine.actions import action_error
from models.game import Game, GamePlayer, GameStatus, Role, CauseOfDeath, Winner
from models.event import Visibility
from models.game_result import GameResult
from ai.graph import ai_decide, wolf_discussion
from ai.mind import render_mind, clip_experience
from ai.turn_context import build_speech_turn
from ai.diagnostics import decision_game_id
from ai.model_configuration import configuration_lock, pick_connections
from ai.personalities import PERSONALITIES, PERSONALITY_KEYS


class GameTimeoutError(Exception):
    """Raised when human player times out — ends the game."""
    pass


class WolfExplodeException(Exception):
    """狼人自爆，中断当前发言流程"""
    def __init__(self, seat: int):
        self.seat = seat


class GameManager:
    def __init__(self, session: AsyncSession, ws_broadcast=None):
        self.repo = GameRepository(session)
        self.session = session
        # ws_broadcast: async callable(game_id, msg_type, data) for pushing to human
        self.ws_broadcast = ws_broadcast
        # Pending human action — set by websocket handler
        self._human_action_future: Optional[asyncio.Future] = None
        # Pending prompt for reconnect resend
        self._pending_prompt: Optional[dict] = None
        # Cache for AI player experiences (user_id → experience text)
        self._experience_cache: dict = {}
        self.public_turn: dict = {}
        self.diagnostic_phase = None
        self.diagnostic_round = None

    # ── Notify helpers ──

    async def _notify(self, game_id: int, msg_type: str, data: dict):
        # Never hold SQLite's writer lock while waiting on a human or model.
        # A reconnect must also see the state that produced this notification.
        await self.session.commit()
        if msg_type == "phase_change":
            self.diagnostic_phase = data.get("phase")
            self.diagnostic_round = data.get("round", self.diagnostic_round)
            self.public_turn = {}
        elif msg_type in ("speech_order", "speaker_change"):
            self.public_turn[msg_type] = data
        if self.ws_broadcast:
            await self.ws_broadcast(game_id, msg_type, data)

    async def _wait_human_action(self, game_id: int, prompt_type: str,
                                  prompt_data: dict, timeout: float = None) -> dict:
        """Send prompt to human player and wait for response with timeout (40min)."""
        timeout = settings.human_action_timeout_seconds if timeout is None else timeout
        prompt_msg = {"prompt_type": prompt_type, **prompt_data,
                      "prompt_id": uuid.uuid4().hex,
                      "expires_at": time.time() + timeout}
        loop = asyncio.get_running_loop()
        # Install the future before notifying; a fast response must not be lost.
        self._human_action_future = loop.create_future()
        self._pending_prompt = prompt_msg
        try:
            await self._notify(game_id, "prompt_action", prompt_msg)
            result = await asyncio.wait_for(self._human_action_future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            await self._notify(game_id, "info", {"message": "（系统：等待超时，本局游戏结束）"})
            raise GameTimeoutError("Human player timed out")
        except asyncio.CancelledError:
            raise
        finally:
            self._pending_prompt = None
            if self._human_action_future and not self._human_action_future.done():
                self._human_action_future.cancel()
            self._human_action_future = None

    def resolve_human_action(self, action: dict):
        """Called by websocket handler when human responds."""
        if not self._pending_prompt or not self._human_action_future or self._human_action_future.done():
            return "当前没有等待中的操作。"
        error = action_error(action, self._pending_prompt)
        if error:
            return error
        self._human_action_future.set_result(action)
        return None

    async def _get_player_model(self, player: GamePlayer) -> Optional[str]:
        """Connection reference for calls; historical display uses the model name."""
        if player.is_human:
            return None
        if getattr(player, "model_config_id", None):
            return player.model_config_id
        return await self._get_player_model_name(player)

    async def _get_player_model_name(self, player: GamePlayer) -> Optional[str]:
        if player.is_human:
            return None
        if player.model_name:
            return player.model_name
        if player.user_id:
            user = await self.repo.get_user_by_id(player.user_id)
            if user and user.model_name:
                return user.model_name
        return None

    async def _get_player_experience(self, player: GamePlayer) -> str:
        """Get experience text for an AI player, with instance-level cache."""
        if player.is_human or not player.user_id:
            return ""
        if player.user_id in self._experience_cache:
            return self._experience_cache[player.user_id]
        user = await self.repo.get_user_by_id(player.user_id)
        exp = clip_experience((user.experience or "") if user else "")
        self._experience_cache[player.user_id] = exp
        return exp

    # ── Game creation ──

    async def create_game(self, human_user_id: int = None) -> Game:
        async with configuration_lock:
            return await self._create_game(human_user_id)

    async def _create_game(self, human_user_id: int = None) -> Game:
        # Validate before creating rows, so an empty pool produces no broken room.
        ai_connections = pick_connections(settings.total_players - 1)
        game = await self.repo.create_game()
        roles = get_standard_role_list()
        random.shuffle(roles)
        personalities = random.sample(PERSONALITY_KEYS, len(PERSONALITY_KEYS))

        human_seat = random.randint(1, settings.total_players)
        ai_pers_idx = 0
        ai_count = settings.total_players - 1

        # Get AI users from DB
        ai_users = await self.repo.get_ai_users(ai_count)

        ai_idx = 0
        for seat_idx in range(settings.total_players):
            seat = seat_idx + 1
            role = roles[seat_idx]
            is_human = (seat == human_seat)
            personality = None
            user_id = None
            nickname = None
            model_name = None
            model_config_id = None

            if is_human:
                user_id = human_user_id
                nickname = None  # Human doesn't need AI nickname
            else:
                personality = personalities[ai_pers_idx % len(personalities)]
                ai_pers_idx += 1
                model_name = ai_connections[ai_idx].model_name
                model_config_id = ai_connections[ai_idx].id
                if ai_idx < len(ai_users):
                    ai_user = ai_users[ai_idx]
                    # A recurring named player keeps the same character across matches.
                    personality = PERSONALITY_KEYS[(ai_user.id - 1) % len(PERSONALITY_KEYS)]
                    user_id = ai_user.id
                    nickname = ai_user.nickname
                else:
                    nicknames = assign_nicknames(1)
                    nickname = nicknames[0]
                ai_idx += 1

            await self.repo.add_player(
                game_id=game.id, seat=seat, role=role,
                is_human=is_human, personality=personality,
                nickname=nickname, user_id=user_id, model_name=model_name, model_config_id=model_config_id,
            )

        # Create witch state
        await self.repo.create_witch_state(game.id)
        await self.session.commit()
        return game

    # ── Memory helpers ──

    async def _get_memory_summary(self, game_id: int, seat: int, role: str = None) -> str:
        """What this player actually carries into the next decision.

        Built from events they are allowed to see, written in the first person:
        private actions, public commitments, grudges, and their own last thoughts.
        """
        player = await self.repo.get_player_by_seat(game_id, seat)
        role_enum = player.role if player else Role.villager
        if role and player is None:
            try:
                role_enum = Role(role)
            except ValueError:
                role_enum = Role.villager

        events = await self.repo.get_visible_events(game_id, seat, role_enum)
        players = await self.repo.get_players(game_id)
        game = await self.repo.get_game(game_id)
        nicknames = {p.seat: p.nickname or "" for p in players}
        return render_mind(
            seat=seat,
            role_name=get_role_name(role_enum),
            nickname=(player.nickname or "") if player else nicknames.get(seat, ""),
            events=events,
            nicknames=nicknames,
            alive_seats=[p.seat for p in players if p.is_alive],
            sheriff_seat=game.sheriff_player_seat if game else None,
            current_round=game.current_round if game else None,
            max_chars=settings.context_memory_chars,
        )

    # ── Phase handlers ──

    async def _pace_delay(self, content: str):
        """Simulate AI thinking/typing delay based on content length (0.5~3s)."""
        length = len(content) if content else 0
        delay = min(0.5 + length * 0.02, 3.0)
        await asyncio.sleep(delay)

    async def _get_alive_info(self, game_id: int):
        players = await self.repo.get_alive_players(game_id)
        return [{"seat": p.seat, "is_alive": True, "personality": p.personality or ""}
                for p in players]

    async def _handle_sheriff_campaign(self, game: Game):
        players = await self.repo.get_alive_players(game.id)

        # ── Step 1: All players decide whether to run ──
        candidate_seats = []

        # Handle human player first (if any)
        human_player = next((p for p in players if p.is_human), None)
        human_run = None
        if human_player:
            action = await self._wait_human_action(game.id, "sheriff_signup", {
                "alive_players": [{"seat": pl.seat} for pl in players],
            })
            human_run = action.get("action_type") == "sheriff_signup" and action.get("run", False)

        # Pre-fetch memory summaries for AI players (DB reads share session)
        ai_players = [p for p in players if not p.is_human]
        ai_memories = {}
        ai_experiences = {}
        for p in ai_players:
            ai_memories[p.seat] = await self._get_memory_summary(game.id, p.seat)
            ai_experiences[p.seat] = await self._get_player_experience(p)

        # Concurrent AI signup decisions
        alive_info = [{"seat": pl.seat} for pl in players]

        async def _ai_signup(p):
            wolf_teammates = []
            if p.role == Role.werewolf:
                wolf_teammates = [{"seat": w.seat} for w in players
                                  if w.role == Role.werewolf and w.seat != p.seat and w.is_alive]
            return await ai_decide(
                seat=p.seat, role=p.role, personality_key=p.personality,
                template_name="sheriff_signup.txt",
                template_vars={
                    "alive_players": alive_info,
                    "seat": p.seat,
                    "role": p.role.value,
                    "wolf_teammates": wolf_teammates,
                    "memory_summary": ai_memories[p.seat],
                },
                action_type="sheriff_signup",
                model_name=await self._get_player_model(p),
                experience=ai_experiences[p.seat],
            )

        ai_results = await asyncio.gather(*[_ai_signup(p) for p in ai_players])
        ai_result_map = {p.seat: r for p, r in zip(ai_players, ai_results)}

        # Process results in seat order (notifications + DB writes)
        for p in players:
            if p.is_human:
                run = human_run
            else:
                result = ai_result_map[p.seat]
                run = result.get("action", {}).get("run", False)
                await self.repo.add_event(
                    game.id, game.current_round, "sheriff_campaign",
                    "sheriff_signup", actor_seat=p.seat,
                    content=result.get("inner_thought", ""), visibility=Visibility.self_only,
                )

            if run:
                candidate_seats.append(p.seat)
            await self.repo.add_event(
                game.id, game.current_round, "sheriff_campaign", "sheriff_signup",
                actor_seat=p.seat, target_seat=p.seat if run else None,
                content=f"{p.seat}号玩家选择{'上警' if run else '不上警'}。",
                visibility=Visibility.all,
            )
            if run:
                await self._notify(game.id, "speech", {
                    "seat": p.seat, "content": f"{p.seat}号玩家选择上警。",
                    "phase": "sheriff_campaign",
                })
            else:
                await self._notify(game.id, "speech", {
                    "seat": p.seat, "content": f"{p.seat}号玩家选择不上警。",
                    "phase": "sheriff_campaign",
                })

        if not candidate_seats:
            return []

        # Announce candidate list
        cand_str = "、".join(f"{s}号" for s in candidate_seats)
        await self._notify(game.id, "info", {
            "message": f"上警的玩家有：{cand_str}。现在由候选人依次发表竞选演说。"
        })

        # ── Step 2: Candidates give speeches in circular order ──
        candidates = []
        candidate_players = [p for p in players if p.seat in candidate_seats]
        # Random start + random direction for campaign speeches
        start_seat = random.choice(candidate_seats)
        direction = random.choice(["clockwise", "counterclockwise"])
        speech_order = circular_seat_order(
            start_seat, direction, candidate_seats, include_start_last=False,
            total_seats=settings.total_players,
        )
        dir_text = "顺时针" if direction == "clockwise" else "逆时针"
        order_str = "→".join(f"{s}号" for s in speech_order)
        await self._notify(game.id, "info", {
            "message": f"竞选发言顺序（从{start_seat}号开始{dir_text}）：{order_str}"
        })
        player_map = {p.seat: p for p in candidate_players}
        await self._notify(game.id, "speech_order", {"seats": speech_order})
        for seat in speech_order:
            p = player_map[seat]
            is_wolf = p.role == Role.werewolf
            speech_turn = build_speech_turn(seat, speech_order, candidates, "sheriff_campaign")
            await self._notify(game.id, "speaker_change", {"seat": p.seat, "is_human": p.is_human})
            if p.is_human:
                prompt_data = {
                    "candidates": [{"seat": s} for s in candidate_seats],
                    "speeches": candidates,
                }
                if is_wolf:
                    prompt_data["is_wolf"] = True
                action = await self._wait_human_action(game.id, "sheriff_speech", prompt_data)
                if is_wolf and action.get("action_type") == "wolf_explode":
                    await self._handle_wolf_explode(game, seat)
                    raise WolfExplodeException(seat)
                speech = action.get("content", "我竞选警长。")
            else:
                wolf_teammates = []
                if p.role == Role.werewolf:
                    wolf_teammates = [{"seat": w.seat} for w in players
                                      if w.role == Role.werewolf and w.seat != p.seat and w.is_alive]
                result = await ai_decide(
                    seat=p.seat, role=p.role, personality_key=p.personality,
                    template_name="sheriff_speech.txt",
                    template_vars={
                        "candidate_seats": candidate_seats,
                        "speeches": candidates,
                        "speech_turn": speech_turn,
                        "seat": p.seat,
                        "role": p.role.value,
                        "wolf_teammates": wolf_teammates,
                        "memory_summary": await self._get_memory_summary(game.id, p.seat),
                    },
                    action_type="sheriff_speech",
                    extra_valid_types=["wolf_explode"] if is_wolf else None,
                    model_name=await self._get_player_model(p),
                    experience=await self._get_player_experience(p),
                )
                if is_wolf and result.get("action", {}).get("type") == "wolf_explode":
                    await self._handle_wolf_explode(game, seat)
                    raise WolfExplodeException(seat)
                speech = result.get("speech", "请大家投我。")
                await self.repo.add_event(
                    game.id, game.current_round, "sheriff_campaign",
                    "sheriff_campaign", actor_seat=p.seat,
                    content=result.get("inner_thought", ""), visibility=Visibility.self_only,
                )

            candidates.append({"seat": p.seat, "speech": speech})
            await self.repo.add_event(
                game.id, game.current_round, "sheriff_campaign",
                "sheriff_campaign", actor_seat=p.seat,
                content=speech, visibility=Visibility.all,
            )
            await self._notify(game.id, "speech", {
                "seat": p.seat, "content": speech,
                "phase": "sheriff_campaign",
            })
            await self.session.commit()
            if not p.is_human:
                await self._pace_delay(speech)

        return candidates

    async def _handle_sheriff_withdraw(self, game: Game, candidates: list[dict]):
        """After campaign speeches, candidates can choose to withdraw (退水)."""
        cand_str = "、".join(f"{c['seat']}号" for c in candidates)
        await self._notify(game.id, "phase_change", {"phase": "sheriff_withdraw"})
        await self._notify(game.id, "info", {
            "message": f"竞选演说结束。当前候选人：{cand_str}。候选人可以选择退水（放弃竞选）。"
        })

        remaining = []
        players = await self.repo.get_alive_players(game.id)
        player_map = {p.seat: p for p in players}

        # Handle human candidate first (if any)
        human_withdraw = None
        for c in candidates:
            p = player_map.get(c["seat"])
            if p and p.is_human:
                action = await self._wait_human_action(game.id, "sheriff_withdraw", {
                    "candidates": [{"seat": cd["seat"], "speech": cd["speech"]} for cd in candidates],
                    "your_speech": c["speech"],
                })
                human_withdraw = action.get("action_type") == "sheriff_withdraw" and action.get("withdraw", False)
                break

        # Pre-fetch memory summaries for AI candidates
        ai_candidates = []
        ai_memories = {}
        ai_experiences = {}
        for c in candidates:
            p = player_map.get(c["seat"])
            if p and not p.is_human:
                ai_candidates.append(c)
                ai_memories[p.seat] = await self._get_memory_summary(game.id, p.seat)
                ai_experiences[p.seat] = await self._get_player_experience(p)

        # Concurrent AI withdraw decisions
        async def _ai_withdraw(c):
            p = player_map[c["seat"]]
            wolf_teammates = []
            if p.role == Role.werewolf:
                wolf_teammates = [{"seat": w.seat} for w in players
                                  if w.role == Role.werewolf and w.seat != p.seat and w.is_alive]
            return await ai_decide(
                seat=p.seat, role=p.role, personality_key=p.personality,
                template_name="sheriff_withdraw.txt",
                template_vars={
                    "candidates": candidates,
                    "seat": p.seat,
                    "role": p.role.value,
                    "your_speech": c["speech"],
                    "wolf_teammates": wolf_teammates,
                    "memory_summary": ai_memories[p.seat],
                },
                action_type="sheriff_withdraw",
                model_name=await self._get_player_model(p),
                experience=ai_experiences[p.seat],
            )

        ai_results = await asyncio.gather(*[_ai_withdraw(c) for c in ai_candidates])
        ai_result_map = {c["seat"]: r for c, r in zip(ai_candidates, ai_results)}

        # Process results in original candidate order
        for c in candidates:
            p = player_map.get(c["seat"])
            if not p:
                remaining.append(c)
                continue

            if p.is_human:
                withdraw = human_withdraw
            else:
                result = ai_result_map[c["seat"]]
                withdraw = result.get("action", {}).get("withdraw", False)
                await self.repo.add_event(
                    game.id, game.current_round, "sheriff_withdraw",
                    "sheriff_withdraw", actor_seat=p.seat,
                    content=result.get("inner_thought", ""), visibility=Visibility.self_only,
                )

            if withdraw:
                await self.repo.add_event(
                    game.id, game.current_round, "sheriff_withdraw",
                    "sheriff_withdraw", actor_seat=p.seat,
                    content=f"{p.seat}号玩家退水", visibility=Visibility.all,
                )
                await self._notify(game.id, "speech", {
                    "seat": p.seat, "content": f"{p.seat}号玩家选择退水，放弃竞选警长。",
                    "phase": "sheriff_withdraw",
                })
            else:
                remaining.append(c)
                await self._notify(game.id, "speech", {
                    "seat": p.seat, "content": f"{p.seat}号玩家坚持竞选。",
                    "phase": "sheriff_withdraw",
                })

        return remaining

    async def _handle_sheriff_vote(self, game: Game, candidates: list[dict], all_candidate_seats: set = None):
        if not candidates:
            return

        if len(candidates) == 1:
            winner, tied_seats = candidates[0]["seat"], []
        else:
            winner, tied_seats = await self._run_sheriff_vote_round(game, candidates, all_candidate_seats)

        if winner:
            game.sheriff_player_seat = winner
            await self.repo.add_event(
                game.id, game.current_round, "sheriff_vote",
                "sheriff_elected", target_seat=winner,
                content=f"{winner}号玩家当选警长", visibility=Visibility.all,
            )
            await self._notify(game.id, "info", {
                "message": f"{winner}号玩家当选警长！警长投票权重为1.5倍。",
            })
            return

        if not tied_seats:
            await self._notify(game.id, "info", {
                "message": "警长投票无有效票，本局无警长。",
            })
            return

        # Tie-break: tied candidates give a second round of speeches, then all alive players vote
        tied_str = "、".join(f"{s}号" for s in tied_seats)
        await self._notify(game.id, "info", {
            "message": f"警长投票平票！{tied_str}得票相同，进入PK环节。",
        })

        # Tied candidates give speeches
        tied_candidates = await self._handle_sheriff_tiebreak_speeches(game, tied_seats, candidates)

        # Second vote: all alive players vote (including former candidates)
        winner2, _ = await self._run_sheriff_vote_round(
            game, tied_candidates, excluded_seats=None, is_tiebreak=True,
        )

        if winner2:
            game.sheriff_player_seat = winner2
            await self.repo.add_event(
                game.id, game.current_round, "sheriff_vote",
                "sheriff_elected", target_seat=winner2,
                content=f"{winner2}号玩家当选警长", visibility=Visibility.all,
            )
            await self._notify(game.id, "info", {
                "message": f"{winner2}号玩家当选警长！警长投票权重为1.5倍。",
            })
        else:
            await self._notify(game.id, "info", {
                "message": "PK轮仍然平票，本局无警长。",
            })

    async def _handle_sheriff_tiebreak_speeches(self, game: Game, tied_seats: list, prev_candidates: list):
        """Tied candidates give a second round of speeches for the PK round."""
        tied_str = "、".join(f"{s}号" for s in tied_seats)
        await self._notify(game.id, "info", {
            "message": f"请{tied_str}依次发表PK演说。"
        })

        players = await self.repo.get_alive_players(game.id)
        player_map = {p.seat: p for p in players}
        start_seat = random.choice(tied_seats)
        direction = random.choice(["clockwise", "counterclockwise"])
        speech_order = circular_seat_order(
            start_seat, direction, tied_seats, include_start_last=False,
            total_seats=settings.total_players,
        )

        tied_candidates = []
        await self._notify(game.id, "speech_order", {"seats": speech_order})
        for seat in speech_order:
            p = player_map.get(seat)
            if not p:
                continue
            is_wolf = p.role == Role.werewolf
            speech_turn = build_speech_turn(seat, speech_order, tied_candidates, "sheriff_pk")
            await self._notify(game.id, "speaker_change", {"seat": p.seat, "is_human": p.is_human})
            if p.is_human:
                action = await self._wait_human_action(game.id, "sheriff_speech", {
                    "candidates": [{"seat": s} for s in tied_seats],
                    "speeches": tied_candidates,
                    "is_tiebreak": True,
                    "is_wolf": is_wolf,
                })
                if is_wolf and action.get("action_type") == "wolf_explode":
                    await self._handle_wolf_explode(game, seat)
                    raise WolfExplodeException(seat)
                speech = action.get("content", "请投我。")
            else:
                wolf_teammates = []
                if p.role == Role.werewolf:
                    wolf_teammates = [{"seat": w.seat} for w in players
                                      if w.role == Role.werewolf and w.seat != p.seat and w.is_alive]
                result = await ai_decide(
                    seat=p.seat, role=p.role, personality_key=p.personality,
                    template_name="sheriff_speech.txt",
                    template_vars={
                        "candidate_seats": tied_seats,
                        "speeches": tied_candidates,
                        "previous_speeches": prev_candidates,
                        "original_candidate_seats": sorted({c["seat"] for c in prev_candidates}),
                        "is_tiebreak": True,
                        "speech_turn": speech_turn,
                        "seat": p.seat,
                        "role": p.role.value,
                        "wolf_teammates": wolf_teammates,
                        "memory_summary": await self._get_memory_summary(game.id, p.seat),
                    },
                    action_type="sheriff_speech",
                    extra_valid_types=["wolf_explode"] if is_wolf else None,
                    model_name=await self._get_player_model(p),
                    experience=await self._get_player_experience(p),
                )
                if is_wolf and result.get("action", {}).get("type") == "wolf_explode":
                    await self._handle_wolf_explode(game, seat)
                    raise WolfExplodeException(seat)
                speech = result.get("speech", "请大家投我。")
                await self.repo.add_event(
                    game.id, game.current_round, "sheriff_campaign",
                    "sheriff_campaign", actor_seat=p.seat,
                    content=result.get("inner_thought", ""), visibility=Visibility.self_only,
                )

            tied_candidates.append({"seat": p.seat, "speech": speech})
            await self.repo.add_event(
                game.id, game.current_round, "sheriff_campaign",
                "sheriff_campaign", actor_seat=p.seat,
                content=speech, visibility=Visibility.all,
            )
            await self._notify(game.id, "speech", {
                "seat": p.seat, "content": speech,
                "phase": "sheriff_campaign",
            })
            await self.session.commit()
            if not p.is_human:
                await self._pace_delay(speech)

        return tied_candidates

    async def _run_sheriff_vote_round(self, game: Game, candidates: list[dict],
                                       excluded_seats: set = None, is_tiebreak: bool = False):
        """Run one round of sheriff voting. Returns (winner, tied_seats)."""
        players = await self.repo.get_alive_players(game.id)
        candidate_seats = [c["seat"] for c in candidates]

        if is_tiebreak:
            # Tiebreak: all alive players can vote (except the candidates themselves)
            voters = [p for p in players if p.seat not in set(candidate_seats)]
        else:
            # First round: exclude all players who ever ran for sheriff
            ex = excluded_seats or set(candidate_seats)
            voters = [p for p in players if p.seat not in ex]
        votes = {}

        # Handle human voter first
        human_voter = next((p for p in voters if p.is_human), None)
        if human_voter:
            action = await self._wait_human_action(game.id, "sheriff_vote", {
                "candidates": candidates,
                "is_tiebreak": is_tiebreak,
            })
            votes[human_voter.seat] = action.get("target")

        # Pre-fetch memory summaries for AI voters
        ai_voters = [p for p in voters if not p.is_human]
        ai_memories = {}
        ai_experiences = {}
        for p in ai_voters:
            ai_memories[p.seat] = await self._get_memory_summary(game.id, p.seat)
            ai_experiences[p.seat] = await self._get_player_experience(p)

        # Concurrent AI vote decisions
        all_game_players = await self.repo.get_players(game.id)
        async def _ai_sheriff_vote(p):
            wolf_teammates = []
            if p.role == Role.werewolf:
                wolf_teammates = [{"seat": w.seat} for w in all_game_players
                                  if w.role == Role.werewolf and w.seat != p.seat and w.is_alive]
            return await ai_decide(
                seat=p.seat, role=p.role, personality_key=p.personality,
                template_name="sheriff_vote.txt",
                template_vars={
                    "candidates": candidates,
                    "is_tiebreak": is_tiebreak,
                    "wolf_teammates": wolf_teammates,
                    "memory_summary": ai_memories[p.seat],
                },
                action_type="sheriff_vote",
                valid_targets=candidate_seats,
                fallback_context={"fallback_target": candidate_seats[0]},
                model_name=await self._get_player_model(p),
                experience=ai_experiences[p.seat],
            )

        ai_results = await asyncio.gather(*[_ai_sheriff_vote(p) for p in ai_voters])

        # Process AI results in seat order
        for p, result in zip(ai_voters, ai_results):
            target = result["action"].get("target")
            votes[p.seat] = target
            await self.repo.add_event(
                game.id, game.current_round, "sheriff_vote",
                "sheriff_vote", actor_seat=p.seat,
                target_seat=result["action"]["target"],
                content=result.get("inner_thought", ""),
                visibility=Visibility.self_only,
            )

        winner, tied_seats = elect_sheriff(candidate_seats, votes)

        # Build vote tally
        tally = {}
        for target in votes.values():
            if target is None:
                continue
            tally[target] = tally.get(target, 0) + 1

        # Show vote details
        vote_detail = "、".join(f"{v}号→{t}号" if t is not None else f"{v}号弃票" for v, t in votes.items())
        round_label = "PK轮" if is_tiebreak else "警长"
        if vote_detail:
            await self.repo.add_event(
                game.id, game.current_round, "sheriff_vote",
                "sheriff_vote_result",
                content=f"{round_label}投票：{vote_detail}",
                visibility=Visibility.all,
            )
        await self._notify(game.id, "sheriff_vote_result", {
            "elected": winner, "votes": votes, "tally": tally,
            "is_tie": winner is None,
            "message": f"{round_label}投票结果：{vote_detail}",
        })

        return winner, tied_seats

    async def _handle_wolf_phase(self, game: Game, last_wolf_target: int = None) -> Optional[int]:
        """Wolf discussion + kill. Returns target seat or None."""
        players = await self.repo.get_players(game.id)
        alive_players = [p for p in players if p.is_alive]
        alive_wolves = [p for p in alive_players if p.role == Role.werewolf]
        valid_targets = [p.seat for p in alive_players]

        if not alive_wolves or not valid_targets:
            return None

        # Check if human is a wolf
        human_wolf = next((w for w in alive_wolves if w.is_human), None)

        if human_wolf:
            # Human wolf participates in discussion
            # First let AI wolves discuss
            ai_wolves = [w for w in alive_wolves if not w.is_human]

            template_vars_base = {
                "wolf_teammates": [{"seat": w.seat, "personality": w.personality or ""}
                                   for w in alive_wolves],
                "alive_players": [{"seat": p.seat, "is_alive": True} for p in alive_players],
                "memory_summary": "",
                "seat": 0,
                "round": game.current_round,
                "last_wolf_target": last_wolf_target,
            }

            # AI wolves suggest first (concurrent)
            ai_wolf_memories = {}
            ai_wolf_experiences = {}
            for w in ai_wolves:
                ai_wolf_memories[w.seat] = await self._get_memory_summary(game.id, w.seat)
                ai_wolf_experiences[w.seat] = await self._get_player_experience(w)

            async def _ai_wolf_suggest(w):
                vars_copy = dict(template_vars_base)
                vars_copy["seat"] = w.seat
                vars_copy["memory_summary"] = ai_wolf_memories[w.seat]
                return await ai_decide(
                    seat=w.seat, role=Role.werewolf, personality_key=w.personality,
                    template_name="werewolf_night.txt",
                    template_vars=vars_copy,
                    action_type="suggest_kill",
                    valid_targets=valid_targets,
                    fallback_context={"fallback_target": valid_targets[0]},
                    model_name=await self._get_player_model(w),
                    experience=ai_wolf_experiences[w.seat],
                )

            wolf_results = await asyncio.gather(*[_ai_wolf_suggest(w) for w in ai_wolves])

            suggestions = []
            for w, result in zip(ai_wolves, wolf_results):
                suggestions.append({
                    "seat": w.seat,
                    "speech": result.get("speech", ""),
                    "target": result["action"].get("target"),
                })
                await self.repo.add_event(
                    game.id, game.current_round, "wolf_strategy",
                    "wolf_discuss", actor_seat=w.seat,
                    content=result.get("speech", ""), visibility=Visibility.werewolves,
                )

            # Step 1: Human wolf discusses with teammates
            discuss_action = await self._wait_human_action(game.id, "wolf_discuss", {
                "wolf_discussion": suggestions,
                "valid_targets": valid_targets,
            })
            human_speech = discuss_action.get("content", "")
            if human_speech:
                suggestions.append({
                    "seat": human_wolf.seat,
                    "speech": human_speech,
                    "target": None,
                })
                await self.repo.add_event(
                    game.id, game.current_round, "wolf_strategy",
                    "wolf_discuss", actor_seat=human_wolf.seat,
                    content=human_speech, visibility=Visibility.werewolves,
                )
                # Echo human wolf's speech in chat log
                await self._notify(game.id, "speech", {
                    "seat": human_wolf.seat, "content": human_speech,
                    "phase": "wolf_discuss",
                })

            # Step 2: Human wolf picks the final target
            action = await self._wait_human_action(game.id, "wolf_kill", {
                "valid_targets": valid_targets,
            })
            target = action.get("target")
            if target not in valid_targets and valid_targets:
                target = valid_targets[0]

            # Notify wolf team of the result
            await self._notify(game.id, "info", {
                "message": f"狼人达成一致，今晚击杀{target}号玩家。",
            })
        else:
            # All AI wolves
            wolf_info = []
            for w in alive_wolves:
                wolf_info.append({
                    "seat": w.seat, "role": Role.werewolf,
                    "personality": w.personality or "logical",
                    "model_name": await self._get_player_model(w),
                })
            # Pre-fetch memory summaries for each wolf
            wolf_memories = {}
            wolf_experiences = {}
            for w in alive_wolves:
                wolf_memories[w.seat] = await self._get_memory_summary(game.id, w.seat)
                wolf_experiences[w.seat] = await self._get_player_experience(w)
            template_vars_base = {
                "wolf_teammates": [{"seat": w.seat, "personality": w.personality or ""}
                                   for w in alive_wolves],
                "alive_players": [{"seat": p.seat, "is_alive": True} for p in alive_players],
                "memory_summary": "",
                "wolf_memories": wolf_memories,
                "seat": 0,
                "round": game.current_round,
                "last_wolf_target": last_wolf_target,
            }

            target, events = await wolf_discussion(
                wolves=wolf_info,
                template_vars_base=template_vars_base,
                valid_targets=valid_targets,
                wolf_experiences=wolf_experiences,
            )

            for e in events:
                await self.repo.add_event(
                    game.id, game.current_round, "wolf_strategy",
                    "wolf_discuss", actor_seat=e["seat"],
                    content=e.get("speech", ""), visibility=Visibility.werewolves,
                )

        if target:
            await self.repo.add_event(
                game.id, game.current_round, "wolf_kill",
                "wolf_kill", target_seat=target,
                content=f"狼人决定击杀{target}号位", visibility=Visibility.werewolves,
            )
        return target

    async def _handle_seer_phase(self, game: Game):
        """Seer checks a player."""
        players = await self.repo.get_alive_players(game.id)
        seer = next((p for p in players if p.role == Role.seer), None)
        if not seer:
            return

        all_players = await self.repo.get_players(game.id)
        valid_targets = [p.seat for p in players if p.seat != seer.seat]

        # Get past checks
        events = await self.repo.get_visible_events(game.id, seer.seat, seer.role)
        past_checks = []
        for e in events:
            if e.event_type == "seer_result":
                past_checks.append({"target": e.target_seat, "result": e.content})

        already_checked_seats = [c["target"] for c in past_checks]
        # Exclude self and already-checked seats from valid targets
        valid_targets = [p.seat for p in players
                         if p.seat != seer.seat and p.seat not in already_checked_seats]

        if seer.is_human:
            action = await self._wait_human_action(game.id, "seer_check", {
                "valid_targets": valid_targets,
                "past_checks": past_checks,
            })
            target = action.get("target")
        else:
            is_sheriff = game.sheriff_player_seat == seer.seat
            result = await ai_decide(
                seat=seer.seat, role=seer.role, personality_key=seer.personality,
                template_name="seer_night.txt",
                template_vars={
                    "alive_players": [{"seat": p.seat} for p in players],
                    "seat": seer.seat,
                    "past_checks": past_checks,
                    "already_checked_seats": already_checked_seats,
                    "is_sheriff": is_sheriff,
                    "sheriff_seat": game.sheriff_player_seat,
                    "memory_summary": await self._get_memory_summary(game.id, seer.seat),
                },
                action_type="seer_check",
                valid_targets=valid_targets,
                fallback_context={"fallback_target": valid_targets[0] if valid_targets else None},
                model_name=await self._get_player_model(seer),
                experience=await self._get_player_experience(seer),
            )
            target = result["action"]["target"]
            await self.repo.add_event(
                game.id, game.current_round, "seer_check",
                "seer_check", actor_seat=seer.seat, target_seat=target,
                content=result.get("inner_thought", ""), visibility=Visibility.self_only,
            )

        if target is not None and target in valid_targets:
            target_player = await self.repo.get_player_by_seat(game.id, target)
            is_wolf = target_player.role == Role.werewolf if target_player else False
            result_text = "狼人" if is_wolf else "好人"
            await self.repo.add_event(
                game.id, game.current_round, "seer_check",
                "seer_result", actor_seat=seer.seat, target_seat=target,
                content=result_text, visibility=Visibility.self_only,
            )
            if seer.is_human:
                await self._notify(game.id, "info", {
                    "message": f"查验结果：{target}号位是{result_text}",
                    "highlight": "seer",
                })

    async def _handle_witch_phase(self, game: Game, wolf_kill_target: Optional[int]):
        """Witch decides to save/poison. Returns (saved: bool, poison_target: int|None)."""
        players = await self.repo.get_alive_players(game.id)
        witch = next((p for p in players if p.role == Role.witch), None)
        if not witch:
            return False, None

        ws = await self.repo.get_witch_state(game.id)
        if not ws:
            return False, None

        # 解药已用，女巫不再知道谁被刀
        if not ws.has_antidote:
            wolf_kill_target = None

        valid_poison_targets = [p.seat for p in players if p.seat != witch.seat]
        can_save = ws.has_antidote and wolf_kill_target is not None
        # Witch can only self-save on the first night
        if can_save and wolf_kill_target == witch.seat and game.current_round > 1:
            can_save = False

        if witch.is_human:
            action = await self._wait_human_action(game.id, "witch_act", {
                "has_antidote": ws.has_antidote,
                "has_poison": ws.has_poison,
                "can_save": can_save,
                "wolf_kill_target": wolf_kill_target,
                "valid_poison_targets": valid_poison_targets,
            })
            save = action.get("save", False) and can_save
            poison_target = action.get("poison_target")
        else:
            result = await ai_decide(
                seat=witch.seat, role=witch.role, personality_key=witch.personality,
                template_name="witch_night.txt",
                template_vars={
                    "round": game.current_round,
                    "has_antidote": ws.has_antidote,
                    "has_poison": ws.has_poison,
                    "can_save": can_save,
                    "valid_poison_targets": valid_poison_targets,
                    "wolf_kill_target": wolf_kill_target,
                    "alive_players": [{"seat": p.seat} for p in players],
                    "seat": witch.seat,
                    "memory_summary": await self._get_memory_summary(game.id, witch.seat),
                },
                action_type="witch_act",
                valid_targets=valid_poison_targets,
                model_name=await self._get_player_model(witch),
                experience=await self._get_player_experience(witch),
            )
            save = result["action"].get("save", False) and can_save
            poison_target = result["action"].get("poison_target")
            await self.repo.add_event(
                game.id, game.current_round, "witch_act",
                "witch_act", actor_seat=witch.seat,
                content=result.get("inner_thought", ""), visibility=Visibility.self_only,
            )

        # Cannot save and poison same night
        if save and poison_target:
            poison_target = None

        if save:
            await self.repo.update_witch_state(game.id, has_antidote=False)
            await self.repo.add_event(
                game.id, game.current_round, "witch_act",
                "witch_save", actor_seat=witch.seat, target_seat=wolf_kill_target,
                visibility=Visibility.self_only,
            )

        if poison_target and ws.has_poison and poison_target in valid_poison_targets:
            await self.repo.update_witch_state(game.id, has_poison=False)
            await self.repo.add_event(
                game.id, game.current_round, "witch_act",
                "witch_poison", actor_seat=witch.seat, target_seat=poison_target,
                content=f"毒了{poison_target}号",
                visibility=Visibility.self_only,
            )
        else:
            poison_target = None

        return save, poison_target

    async def _handle_night_resolve(self, game: Game, wolf_target: Optional[int],
                                     saved: bool, poison_target: Optional[int]):
        """Resolve night deaths. Returns list of dead seats."""
        deaths = []
        # 同夜被刀又被毒，按毒死结算，猎人不能开枪。
        poisoned_knife = bool(
            poison_target and wolf_target and not saved and poison_target == wolf_target
        )

        if wolf_target and not saved and not poisoned_knife:
            player = await self.repo.get_player_by_seat(game.id, wolf_target)
            if player and player.is_alive:
                await self.repo.kill_player(player, CauseOfDeath.wolf_kill, game.current_round)
                deaths.append({"seat": wolf_target, "cause": "wolf_kill"})

        if poison_target:
            player = await self.repo.get_player_by_seat(game.id, poison_target)
            if player and player.is_alive:
                await self.repo.kill_player(player, CauseOfDeath.witch_poison, game.current_round)
                deaths.append({"seat": poison_target, "cause": "witch_poison"})

        # Announce deaths (without revealing cause)
        if deaths:
            dead_seats = sorted(d["seat"] for d in deaths)
            words = "请发表遗言。" if game.current_round == 1 else "没有遗言。"
            if len(dead_seats) == 1:
                msg = f"昨晚，{dead_seats[0]}号玩家倒了。{words}"
            else:
                msg = "昨晚，" + "和".join(f"{s}号" for s in dead_seats) + f"玩家倒了。{words}"
            await self.repo.add_event(
                game.id, game.current_round, "night_resolve",
                "death_announce", content=msg, visibility=Visibility.all,
            )
            # Night causes are hidden, even from the human client.
            public_deaths = [{"seat": d["seat"]} for d in sorted(deaths, key=lambda d: d["seat"])]
            await self._notify(game.id, "death", {"deaths": public_deaths, "message": msg})
        else:
            msg = "昨晚是平安夜，没有玩家倒下。"
            await self.repo.add_event(
                game.id, game.current_round, "night_resolve",
                "death_announce", content=msg, visibility=Visibility.all,
            )
            await self._notify(game.id, "info", {"message": msg})

        return deaths

    async def _handle_wolf_explode(self, game: Game, seat: int):
        """处理狼人自爆：击杀狼人、记录事件、广播消息"""
        player = await self.repo.get_player_by_seat(game.id, seat)
        await self.repo.kill_player(player, CauseOfDeath.wolf_explode, game.current_round)
        await self.repo.add_event(
            game.id, game.current_round, "wolf_explode",
            "wolf_explode", actor_seat=seat,
            content=f"{seat}号玩家（狼人）自爆！", visibility=Visibility.all,
        )
        await self._notify(game.id, "speech", {
            "seat": seat, "content": f"我是狼人，我选择自爆！",
            "phase": "wolf_explode",
        })
        await self._notify(game.id, "death", {
            "deaths": [{"seat": seat, "cause": "wolf_explode"}],
            "message": f"{seat}号玩家自爆，身份是狼人！",
        })
        # 如果自爆者是警长，清除警长（不触发警徽移交）
        if game.sheriff_player_seat == seat:
            game.sheriff_player_seat = None
            await self._notify(game.id, "info", {
                "message": f"警长{seat}号自爆，警徽被销毁，本局不再有警长。"
            })

    async def _handle_day_speech(self, game: Game):
        """All alive players give speeches in circular seat order."""
        players = await self.repo.get_alive_players(game.id)
        alive_seats = [p.seat for p in players]
        player_map = {p.seat: p for p in players}
        speeches = []

        # Determine speech order
        if game.sheriff_player_seat and game.sheriff_player_seat in alive_seats:
            # Sheriff chooses direction
            sheriff_player = player_map[game.sheriff_player_seat]
            if sheriff_player.is_human:
                action = await self._wait_human_action(game.id, "speech_direction", {
                    "message": "你是警长，请选择发言方向。你将最后发言。",
                })
                direction = action.get("direction", "clockwise")
            else:
                direction = random.choice(["clockwise", "counterclockwise"])
            speech_seats = circular_seat_order(
                game.sheriff_player_seat, direction, alive_seats, include_start_last=True,
                total_seats=settings.total_players,
            )
            dir_text = "顺时针" if direction == "clockwise" else "逆时针"
            await self._notify(game.id, "info", {
                "message": f"警长{game.sheriff_player_seat}号选择{dir_text}发言，发言顺序：{'→'.join(f'{s}号' for s in speech_seats)}"
            })
        else:
            # No sheriff: random start + random direction
            start_seat = random.choice(alive_seats)
            direction = random.choice(["clockwise", "counterclockwise"])
            speech_seats = circular_seat_order(
                start_seat, direction, alive_seats, include_start_last=False,
                total_seats=settings.total_players,
            )
            dir_text = "顺时针" if direction == "clockwise" else "逆时针"
            await self._notify(game.id, "info", {
                "message": f"从{start_seat}号开始{dir_text}发言，发言顺序：{'→'.join(f'{s}号' for s in speech_seats)}"
            })

        # Get deaths this round for context
        events = await self.repo.get_all_events(game.id, game.current_round)
        deaths_this_round = [
            {"seat": p.seat} for p in await self.repo.get_players(game.id)
            if not p.is_alive and p.died_at_round == game.current_round
        ]

        # Get sheriff campaign speeches for context
        sheriff_speeches = []
        campaign_events = [
            e for e in events
            if e.event_type == "sheriff_campaign" and e.visibility == Visibility.all
        ]
        for e in campaign_events:
            sheriff_speeches.append({
                "seat": e.actor_seat,
                "speech": e.content or "",
                "withdrew": False,
            })
        # Mark withdrawn candidates
        withdraw_events = [
            e for e in events if e.event_type == "sheriff_withdraw" and e.visibility == Visibility.all
        ]
        withdrawn_seats = {e.actor_seat for e in withdraw_events}
        for s in sheriff_speeches:
            if s["seat"] in withdrawn_seats:
                s["withdrew"] = True

        sheriff_info = f"{game.sheriff_player_seat}号位是警长" if game.sheriff_player_seat else "暂无警长"

        await self._notify(game.id, "speech_order", {"seats": speech_seats})
        for seat in speech_seats:
            p = player_map[seat]
            is_wolf = p.role == Role.werewolf
            speech_turn = build_speech_turn(seat, speech_seats, speeches, "day_speech")
            await self._notify(game.id, "speaker_change", {"seat": p.seat, "is_human": p.is_human})
            if p.is_human:
                prompt_data = {
                    "speeches": speeches,
                    "alive_players": [{"seat": pl.seat} for pl in players],
                    "deaths_this_round": deaths_this_round,
                    "sheriff_info": sheriff_info,
                }
                if is_wolf:
                    prompt_data["is_wolf"] = True
                action = await self._wait_human_action(game.id, "day_speech", prompt_data)
                if is_wolf and action.get("action_type") == "wolf_explode":
                    await self._handle_wolf_explode(game, seat)
                    raise WolfExplodeException(seat)
                content = action.get("content", "我没什么想说的。")
            else:
                # Build wolf teammate info for werewolves
                wolf_teammates = []
                if p.role == Role.werewolf:
                    all_game_players = await self.repo.get_players(game.id)
                    wolf_teammates = [{"seat": w.seat} for w in all_game_players
                                      if w.role == Role.werewolf and w.seat != p.seat and w.is_alive]
                result = await ai_decide(
                    seat=p.seat, role=p.role, personality_key=p.personality,
                    template_name="day_speech.txt",
                    template_vars={
                        "round": game.current_round,
                        "sheriff_info": sheriff_info,
                        "sheriff_seat": game.sheriff_player_seat,
                        "alive_count": len(players),
                        "alive_players": [{"seat": pl.seat} for pl in players],
                        "speeches": speeches,
                        "speech_turn": speech_turn,
                        "deaths_this_round": deaths_this_round,
                        "sheriff_speeches": sheriff_speeches,
                        "seat": p.seat,
                        "role": p.role.value,
                        "wolf_teammates": wolf_teammates,
                        "memory_summary": await self._get_memory_summary(game.id, p.seat),
                    },
                    action_type="speech",
                    extra_valid_types=["wolf_explode"] if is_wolf else None,
                    model_name=await self._get_player_model(p),
                    experience=await self._get_player_experience(p),
                )
                if is_wolf and result.get("action", {}).get("type") == "wolf_explode":
                    await self._handle_wolf_explode(game, seat)
                    raise WolfExplodeException(seat)
                content = result.get("speech", "我过。")
                await self.repo.add_event(
                    game.id, game.current_round, "day_speech",
                    "day_speech", actor_seat=p.seat,
                    content=result.get("inner_thought", ""), visibility=Visibility.self_only,
                )

            speeches.append({"seat": p.seat, "content": content})
            await self.repo.add_event(
                game.id, game.current_round, "day_speech",
                "day_speech", actor_seat=p.seat,
                content=content, visibility=Visibility.all,
            )
            await self._notify(game.id, "speech", {
                "seat": p.seat, "content": content, "phase": "day_speech",
            })
            await self.session.commit()
            if not p.is_human:
                await self._pace_delay(content)

        return speeches

    def _vote_tally(self, votes: dict, sheriff_seat: Optional[int]) -> dict:
        tally = {}
        for voter, target in votes.items():
            if target is None:
                continue
            weight = get_sheriff_weight(voter, sheriff_seat)
            tally[target] = round(tally.get(target, 0) + weight, 2)
        return tally

    def _tied_seats(self, tally: dict) -> list:
        if not tally:
            return []
        top = max(tally.values())
        return [seat for seat, count in tally.items() if abs(count - top) < 0.01]

    async def _collect_votes(self, game: Game, voters: list, votable: list,
                             speeches: list, sheriff_info: str,
                             pk_targets: str = "") -> dict:
        """One round of votes. Voters who lost the idiot's ballot are already excluded."""
        votes = {}
        human_player = next((p for p in voters if p.is_human), None)
        if human_player:
            choices = [s for s in votable if s != human_player.seat]
            if choices:
                action = await self._wait_human_action(game.id, "day_vote", {
                    "speeches": speeches,
                    "votable_players": [{"seat": s} for s in choices],
                    "sheriff_info": sheriff_info,
                })
                target = action.get("target")
                votes[human_player.seat] = target

        ai_players = [p for p in voters if not p.is_human]
        ai_memories = {}
        ai_experiences = {}
        for p in ai_players:
            ai_memories[p.seat] = await self._get_memory_summary(game.id, p.seat)
            ai_experiences[p.seat] = await self._get_player_experience(p)
        all_game_players = await self.repo.get_players(game.id)

        async def _ai_day_vote(p):
            targets_excl_self = [s for s in votable if s != p.seat]
            wolf_teammates = []
            if p.role == Role.werewolf:
                wolf_teammates = [{"seat": w.seat} for w in all_game_players
                                  if w.role == Role.werewolf and w.seat != p.seat and w.is_alive]
            return await ai_decide(
                seat=p.seat, role=p.role, personality_key=p.personality,
                template_name="day_vote.txt",
                template_vars={
                    "round": game.current_round,
                    "seat": p.seat,
                    "sheriff_info": sheriff_info,
                    "sheriff_seat": game.sheriff_player_seat,
                    "speeches": speeches,
                    "votable_players": [{"seat": s} for s in targets_excl_self],
                    "wolf_teammates": wolf_teammates,
                    "memory_summary": ai_memories[p.seat],
                    "pk_targets": pk_targets,
                },
                action_type="vote",
                valid_targets=targets_excl_self,
                fallback_context={"fallback_target": targets_excl_self[0] if targets_excl_self else None},
                model_name=await self._get_player_model(p),
                experience=ai_experiences[p.seat],
            )

        ai_results = await asyncio.gather(*[_ai_day_vote(p) for p in ai_players])
        for p, result in zip(ai_players, ai_results):
            target = result["action"].get("target")
            votes[p.seat] = target
            await self.repo.add_event(
                game.id, game.current_round, "day_vote",
                "day_vote", actor_seat=p.seat, target_seat=result["action"]["target"],
                content=result.get("inner_thought", ""), visibility=Visibility.self_only,
            )
        return votes

    async def _publish_vote(self, game: Game, votes: dict, voted_out: Optional[int],
                            is_tie: bool, message: str, idiot_seat: Optional[int] = None):
        await self.repo.update_game(game, current_phase=GamePhase.VOTE_RESOLVE.value)
        await self._notify(game.id, "phase_change", {"phase": "vote_resolve", "round": game.current_round})
        tally = self._vote_tally(votes, game.sheriff_player_seat)
        vote_detail = "、".join(f"{v}号→{t}号" if t is not None else f"{v}号弃票" for v, t in votes.items())
        await self.repo.add_event(
            game.id, game.current_round, "vote_resolve",
            "vote_result", target_seat=None if idiot_seat else voted_out,
            content=f"{message}（投票详情：{vote_detail}）", visibility=Visibility.all,
        )
        await self._notify(game.id, "vote_result", {
            "voted_out": None if idiot_seat else voted_out,
            "votes": votes,
            "tally": tally,
            "is_tie": is_tie,
            "message": message,
            "idiot_revealed": idiot_seat,
        })

    async def _handle_pk_speech(self, game: Game, tied: list) -> list:
        """Tied players speak once. A wolf may still explode and skip the revote."""
        alive = await self.repo.get_alive_players(game.id)
        player_map = {p.seat: p for p in alive}
        speakers = [player_map[s] for s in tied if s in player_map]
        pk_targets = "、".join(f"{s}号" for s in tied)
        await self._notify(game.id, "info", {
            "message": f"平票，{pk_targets}进入PK发言，然后再投一次。"
        })
        speeches = []
        sheriff_info = f"{game.sheriff_player_seat}号位是警长" if game.sheriff_player_seat else "暂无警长"
        all_players = await self.repo.get_players(game.id)
        await self._notify(game.id, "speech_order", {"seats": [p.seat for p in speakers], "is_pk": True})
        for p in speakers:
            is_wolf = p.role == Role.werewolf
            speech_turn = build_speech_turn(p.seat, [s.seat for s in speakers], speeches, "day_pk")
            await self._notify(game.id, "speaker_change", {"seat": p.seat, "is_human": p.is_human})
            if p.is_human:
                action = await self._wait_human_action(game.id, "day_speech", {
                    "speeches": speeches,
                    "alive_players": [{"seat": pl.seat} for pl in alive],
                    "sheriff_info": sheriff_info,
                    "is_wolf": is_wolf,
                })
                if is_wolf and action.get("action_type") == "wolf_explode":
                    await self._handle_wolf_explode(game, p.seat)
                    raise WolfExplodeException(p.seat)
                content = action.get("content", "我没什么想说的。")
            else:
                result = await ai_decide(
                    seat=p.seat, role=p.role, personality_key=p.personality,
                    template_name="day_speech.txt",
                    template_vars={
                        "round": game.current_round,
                        "sheriff_info": sheriff_info,
                        "sheriff_seat": game.sheriff_player_seat,
                        "alive_count": len(alive),
                        "alive_players": [{"seat": pl.seat} for pl in alive],
                        "speeches": speeches,
                        "speech_turn": speech_turn,
                        "deaths_this_round": [],
                        "sheriff_speeches": [],
                        "seat": p.seat,
                        "role": p.role.value,
                        "wolf_teammates": [
                            {"seat": w.seat} for w in all_players
                            if w.role == Role.werewolf and w.seat != p.seat and w.is_alive
                        ] if is_wolf else [],
                        "pk_targets": pk_targets,
                        "memory_summary": await self._get_memory_summary(game.id, p.seat),
                    },
                    action_type="speech",
                    extra_valid_types=["wolf_explode"] if is_wolf else None,
                    model_name=await self._get_player_model(p),
                    experience=await self._get_player_experience(p),
                )
                if is_wolf and result.get("action", {}).get("type") == "wolf_explode":
                    await self._handle_wolf_explode(game, p.seat)
                    raise WolfExplodeException(p.seat)
                content = result.get("speech", "我过。")
            speeches.append({"seat": p.seat, "content": content})
            await self.repo.add_event(
                game.id, game.current_round, "day_speech",
                "day_speech", actor_seat=p.seat,
                content=content, visibility=Visibility.all,
            )
            await self._notify(game.id, "speech", {
                "seat": p.seat, "content": content, "phase": "day_speech",
            })
            await self.session.commit()
            if not p.is_human:
                await self._pace_delay(content)
        return speeches

    async def _apply_exile(self, game: Game, seat: int):
        """Kill the exile, or let an unused idiot flip and stay.

        Returns (dead_seat or None, idiot_flipped).
        """
        player = await self.repo.get_player_by_seat(game.id, seat)
        if not player:
            return None, False
        if idiot_survives_exile(player.role, player.can_vote):
            player.can_vote = False
            message = f"{seat}号翻出白痴，免于放逐，但从此失去投票权。"
            await self.repo.add_event(
                game.id, game.current_round, "vote_resolve",
                "idiot_reveal", actor_seat=seat,
                content=message, visibility=Visibility.all,
            )
            await self._notify(game.id, "idiot_reveal", {"seat": seat, "message": message})
            return None, True
        await self.repo.kill_player(player, CauseOfDeath.vote, game.current_round)
        return seat, False

    async def _handle_day_vote(self, game: Game, speeches: list) -> Optional[int]:
        """Day vote, with one PK round on a tie. Returns the seat that actually died."""
        alive = await self.repo.get_alive_players(game.id)
        voters = [p for p in alive if p.can_vote]
        votable = [p.seat for p in alive]
        sheriff_info = f"{game.sheriff_player_seat}号位是警长" if game.sheriff_player_seat else "暂无警长"
        if game.sheriff_player_seat:
            sheriff = next((p for p in alive if p.seat == game.sheriff_player_seat), None)
            if sheriff and not sheriff.can_vote:
                sheriff_info += "（白痴已翻牌，不能投票，警徽票权作废）"

        votes = await self._collect_votes(game, voters, votable, speeches, sheriff_info)
        voted_out, is_tie = count_weighted_votes(votes, game.sheriff_player_seat)
        if is_tie:
            tied = self._tied_seats(self._vote_tally(votes, game.sheriff_player_seat))
            names = "、".join(f"{s}号" for s in tied)
            await self._publish_vote(
                game, votes, None, True,
                f"投票结束，{names}平票。",
            )
            pk_speeches = await self._handle_pk_speech(game, tied)
            pk_label = "、".join(f"{s}号" for s in tied)
            votes = await self._collect_votes(
                game, [p for p in voters if p.seat not in tied], tied,
                speeches + pk_speeches, sheriff_info, pk_targets=pk_label,
            )
            voted_out, is_tie = count_weighted_votes(votes, game.sheriff_player_seat)
            if is_tie or not voted_out:
                await self._publish_vote(game, votes, None, True, "PK后再次平票，平安日，无人出局。")
                return None

        if not voted_out:
            await self._publish_vote(game, votes, None, False, "投票结束，本轮无人出局。")
            return None

        dead_seat, idiot_flipped = await self._apply_exile(game, voted_out)
        if idiot_flipped:
            await self._publish_vote(
                game, votes, voted_out, False,
                f"投票结束，{voted_out}号被放逐，但翻出白痴，免死并失去投票权。",
                idiot_seat=voted_out,
            )
            return None

        await self._publish_vote(
            game, votes, dead_seat, False,
            f"投票结束，{dead_seat}号玩家被票出局。",
        )
        return dead_seat

    async def _resolve_hunter_chain(self, game: Game, first_seat: int):
        """Hunter shoots, then a shot hunter may shoot. Shot players get no last words."""
        pending = [first_seat]
        seen = set()
        while pending:
            seat = pending.pop(0)
            if seat in seen:
                continue
            seen.add(seat)
            shot = await self._handle_hunter_check(game, seat)
            if not shot:
                continue
            await self._handle_sheriff_transfer(game, shot)
            pending.append(shot)

    async def _handle_hunter_check(self, game: Game, dead_seat: Optional[int]) -> Optional[int]:
        """Check if dead player is hunter and handle shooting."""
        if not dead_seat:
            return None

        player = await self.repo.get_player_by_seat(game.id, dead_seat)
        if not player or player.role != Role.hunter:
            return None

        if not can_hunter_shoot(player.cause_of_death.value if player.cause_of_death else None):
            return None

        alive_players = await self.repo.get_alive_players(game.id)
        valid_targets = [p.seat for p in alive_players]

        cause_desc = {
            "wolf_kill": "被狼人击杀",
            "vote": "被投票出局",
        }.get(player.cause_of_death.value if player.cause_of_death else "", "死亡")

        if player.is_human:
            action = await self._wait_human_action(game.id, "hunter_shot", {
                "valid_targets": valid_targets,
                "cause_of_death_desc": cause_desc,
            })
            target = action.get("target")
        else:
            result = await ai_decide(
                seat=player.seat, role=player.role, personality_key=player.personality,
                template_name="hunter_skill.txt",
                template_vars={
                    "cause_of_death_desc": cause_desc,
                    "can_shoot": True,
                    "alive_players": [{"seat": p.seat} for p in alive_players],
                    "memory_summary": await self._get_memory_summary(game.id, player.seat),
                },
                action_type="hunter_shot",
                valid_targets=valid_targets,
                fallback_context={"fallback_target": valid_targets[0] if valid_targets else None},
                model_name=await self._get_player_model(player),
                experience=await self._get_player_experience(player),
            )
            target = result["action"].get("target")

        if target and target in valid_targets:
            shot_player = await self.repo.get_player_by_seat(game.id, target)
            if shot_player:
                await self.repo.kill_player(shot_player, CauseOfDeath.hunter_shot, game.current_round)
                await self.repo.add_event(
                    game.id, game.current_round, "hunter_check",
                    "hunter_shot", actor_seat=dead_seat, target_seat=target,
                    content=f"猎人{dead_seat}号发动技能，带走了{target}号玩家！",
                    visibility=Visibility.all,
                )
                await self._notify(game.id, "death", {
                    "deaths": [{"seat": target, "cause": "hunter_shot"}],
                    "message": f"猎人{dead_seat}号发动技能，带走了{target}号玩家！",
                })
                return target
        return None

    async def _handle_last_words(self, game: Game, dead_seat: int):
        """Dead player gives last words. Handle sheriff transfer if applicable."""
        player = await self.repo.get_player_by_seat(game.id, dead_seat)
        if not player:
            return

        await self.repo.update_game(game, current_phase=GamePhase.LAST_WORDS.value)
        await self._notify(game.id, "phase_change", {"phase": "last_words", "round": game.current_round})

        is_sheriff = game.sheriff_player_seat == dead_seat
        alive_players = await self.repo.get_alive_players(game.id)

        role_name = get_role_name(player.role)
        cause_desc = {
            "wolf_kill": "夜间死亡（死因未公开）",
            "vote": "被投票出局",
            "witch_poison": "夜间死亡（死因未公开）",
            "hunter_shot": "被猎人射杀",
            "wolf_explode": "狼人自爆",
        }.get(player.cause_of_death.value if player.cause_of_death else "", "死亡")

        if player.is_human:
            action = await self._wait_human_action(game.id, "last_words", {
                "is_sheriff": is_sheriff,
                "alive_players": [{"seat": p.seat} for p in alive_players],
            })
            content = action.get("content", "")
            sheriff_transfer_target = action.get("sheriff_transfer") if is_sheriff else None
        else:
            result = await ai_decide(
                seat=player.seat, role=player.role, personality_key=player.personality,
                template_name="last_words.txt",
                template_vars={
                    "role_name": role_name,
                    "role": player.role,
                    "seat": player.seat,
                    "cause_of_death_desc": cause_desc,
                    "is_sheriff": is_sheriff,
                    "alive_players": [{"seat": p.seat} for p in alive_players],
                    "memory_summary": await self._get_memory_summary(game.id, player.seat),
                },
                action_type="last_words",
                model_name=await self._get_player_model(player),
                experience=await self._get_player_experience(player),
            )
            content = result.get("speech", "")
            sheriff_transfer_target = result["action"].get("sheriff_transfer") if is_sheriff else None

        await self.repo.add_event(
            game.id, game.current_round, "last_words",
            "last_words", actor_seat=dead_seat, content=content, visibility=Visibility.all,
        )
        await self._notify(game.id, "speech", {
            "seat": dead_seat, "content": content, "phase": "last_words",
        })
        if not player.is_human:
            await self._pace_delay(content)

        # Sheriff transfer
        if is_sheriff:
            if sheriff_transfer_target is None:
                sheriff_transfer_target = 0
            if sheriff_transfer_target == 0:
                # Tear the badge - no transfer
                game.sheriff_player_seat = None
                await self.repo.add_event(
                    game.id, game.current_round, "last_words",
                    "sheriff_transfer", actor_seat=dead_seat,
                    content=f"{dead_seat}号撕掉了警徽，警长之位空缺。", visibility=Visibility.all,
                )
                await self._notify(game.id, "info", {
                    "message": f"{dead_seat}号撕掉了警徽，警长之位空缺。",
                })
            else:
                new_sheriff = transfer_sheriff(dead_seat, sheriff_transfer_target, alive_players)
                if new_sheriff:
                    game.sheriff_player_seat = new_sheriff
                    await self.repo.add_event(
                        game.id, game.current_round, "last_words",
                        "sheriff_transfer", actor_seat=dead_seat, target_seat=new_sheriff,
                        content=f"{dead_seat}号将警徽移交给{new_sheriff}号玩家。", visibility=Visibility.all,
                    )
                    await self._notify(game.id, "info", {
                        "message": f"{dead_seat}号将警徽移交给{new_sheriff}号玩家。",
                    })
                else:
                    game.sheriff_player_seat = None

    async def _handle_sheriff_transfer(self, game: Game, dead_seat: int):
        """Handle sheriff badge transfer when sheriff dies without last words (night death round 2+)."""
        if game.sheriff_player_seat != dead_seat:
            return
        player = await self.repo.get_player_by_seat(game.id, dead_seat)
        if not player:
            return

        alive_players = await self.repo.get_alive_players(game.id)
        role_name = get_role_name(player.role)

        if player.is_human:
            action = await self._wait_human_action(game.id, "sheriff_transfer", {
                "alive_players": [{"seat": p.seat} for p in alive_players],
            })
            sheriff_transfer_target = action.get("sheriff_transfer")
        else:
            result = await ai_decide(
                seat=player.seat, role=player.role, personality_key=player.personality,
                template_name="last_words.txt",
                template_vars={
                    "role_name": role_name,
                    "role": player.role,
                    "seat": player.seat,
                    "cause_of_death_desc": "夜间死亡（死因未公开）",
                    "is_sheriff": True,
                    "alive_players": [{"seat": p.seat} for p in alive_players],
                    "memory_summary": await self._get_memory_summary(game.id, player.seat),
                },
                action_type="last_words",
                model_name=await self._get_player_model(player),
                experience=await self._get_player_experience(player),
            )
            sheriff_transfer_target = result["action"].get("sheriff_transfer")

        if sheriff_transfer_target is not None and sheriff_transfer_target != 0:
            new_sheriff = transfer_sheriff(dead_seat, sheriff_transfer_target, alive_players)
            if new_sheriff:
                game.sheriff_player_seat = new_sheriff
                await self.repo.add_event(
                    game.id, game.current_round, "night_resolve",
                    "sheriff_transfer", actor_seat=dead_seat, target_seat=new_sheriff,
                    content=f"{dead_seat}号将警徽移交给{new_sheriff}号玩家。", visibility=Visibility.all,
                )
                await self._notify(game.id, "info", {
                    "message": f"{dead_seat}号将警徽移交给{new_sheriff}号玩家。",
                })
            else:
                game.sheriff_player_seat = None
        else:
            # Tear badge or no transfer specified
            game.sheriff_player_seat = None
            if sheriff_transfer_target == 0:
                await self.repo.add_event(
                    game.id, game.current_round, "night_resolve",
                    "sheriff_transfer", actor_seat=dead_seat,
                    content=f"{dead_seat}号撕掉了警徽，警长之位空缺。", visibility=Visibility.all,
                )
                await self._notify(game.id, "info", {
                    "message": f"{dead_seat}号撕掉了警徽，警长之位空缺。",
                })

    # ── Main game loop ──

    async def run_game(self, game_id: int, resume_after_day: bool = False):
        """Main game loop — runs the full game from start to finish."""
        game = await self.repo.get_game(game_id)
        if not game:
            return

        if not resume_after_day:
            await self.repo.update_game(game, status=GameStatus.running, started_at=datetime.utcnow())

        scope_token = decision_game_id.set(game_id)
        try:
            if resume_after_day:
                await self._run_game_loop(game, resume_after_day=True)
            else:
                await self._run_game_loop(game)
        except GameTimeoutError:
            print(f"[Game {game.id}] Human player timed out, ending game.")
            await self._end_game_timeout(game)
        finally:
            decision_game_id.reset(scope_token)

    async def _run_game_loop(self, game: Game, resume_after_day: bool = False):
        """Inner game loop — separated so GameTimeoutError bubbles up cleanly."""
        if resume_after_day:
            await self._notify(game.id, "info", {"message": "对局已恢复，投票与遗言已保留，从下一个夜晚继续。"})
        else:
            # 上帝开场白只在新局发送；恢复不能重发报名、技能或阵容事件。
            players = await self.repo.get_alive_players(game.id)
            seat_list = "、".join(f"{p.seat}号" for p in players)
            await self._notify(game.id, "info", {
                "message": f"本局共12名玩家（{seat_list}），身份已分配完毕。板子是预女猎白，有警徽。"
            })

        # Main loop: night → day cycles
        checkpoint = (getattr(game, "config", None) or {}).get("resume_checkpoint") or {}
        last_wolf_target = checkpoint.get("last_wolf_target") if resume_after_day else None
        sheriff_exploded = False
        while True:
            game.current_round += 1
            config = dict(getattr(game, "config", None) or {})
            config.pop("resume_checkpoint", None)
            await self.repo.update_game(
                game, current_round=game.current_round,
                current_phase=GamePhase.NIGHT_START.value, config=config,
            )
            await self._notify(game.id, "phase_change", {
                "phase": "night_start", "round": game.current_round,
            })
            await self._notify(game.id, "info", {
                "message": f"第{game.current_round}个夜晚降临，天黑请闭眼。"
            })

            # ── Night ──
            wolf_target = None
            saved = False
            poison_target = None

            # Wolf phase
            await self.repo.update_game(game, current_phase=GamePhase.WOLF_KILL.value)
            await self._notify(game.id, "phase_change", {"phase": "wolf_kill", "round": game.current_round})
            await self._notify(game.id, "info", {"message": "狼人请睁眼，请选择今晚要击杀的目标。"})
            try:
                wolf_target = await self._handle_wolf_phase(game, last_wolf_target=last_wolf_target)
            except GameTimeoutError:
                raise
            except Exception as e:
                print(f"[Game {game.id}] Wolf phase error: {e}")
                traceback.print_exc()
                await self._notify(game.id, "info", {"message": "（系统：狼人阶段出现异常，跳过本轮击杀）"})
            await self.session.commit()
            last_wolf_target = wolf_target

            # Seer phase
            await self.repo.update_game(game, current_phase=GamePhase.SEER_CHECK.value)
            await self._notify(game.id, "phase_change", {"phase": "seer_check", "round": game.current_round})
            await self._notify(game.id, "info", {"message": "狼人请闭眼。预言家请睁眼，请选择要查验的玩家。"})
            try:
                await self._handle_seer_phase(game)
            except GameTimeoutError:
                raise
            except Exception as e:
                print(f"[Game {game.id}] Seer phase error: {e}")
                traceback.print_exc()
            await self.session.commit()

            # Witch phase
            await self.repo.update_game(game, current_phase=GamePhase.WITCH_ACT.value)
            await self._notify(game.id, "phase_change", {"phase": "witch_act", "round": game.current_round})
            await self._notify(game.id, "info", {"message": "预言家请闭眼。女巫请睁眼。"})
            try:
                saved, poison_target = await self._handle_witch_phase(game, wolf_target)
            except GameTimeoutError:
                raise
            except Exception as e:
                print(f"[Game {game.id}] Witch phase error: {e}")
                traceback.print_exc()
            await self.session.commit()

            # ── 天亮了（先不公布死亡）──
            await self._notify(game.id, "info", {"message": "女巫请闭眼。天亮了，所有人请睁眼。"})

            # Sheriff campaign (first day only, before death announcement)
            if game.current_round == 1:
                await self.repo.update_game(game, current_phase=GamePhase.SHERIFF_CAMPAIGN.value)
                alive_now = await self.repo.get_alive_players(game.id)
                first_seat = alive_now[0].seat if alive_now else 1
                await self._notify(game.id, "phase_change", {"phase": "sheriff_campaign"})
                await self._notify(game.id, "info", {
                    "message": f"在公布昨晚情况之前，先进行警长竞选。从{first_seat}号开始，依次发表竞选演说或选择弃权。"
                })
                sheriff_exploded = False
                try:
                    candidates = await self._handle_sheriff_campaign(game)
                except WolfExplodeException:
                    candidates = []
                    sheriff_exploded = True
                    await self._notify(game.id, "info", {"message": "狼人自爆，警徽被吞，本局无警长。"})
                    await self.session.commit()
                except GameTimeoutError:
                    raise
                except Exception as e:
                    print(f"[Game {game.id}] Sheriff campaign error: {e}")
                    traceback.print_exc()
                    candidates = []

                # Withdraw phase (退水)
                all_candidate_seats = {c["seat"] for c in candidates}
                if candidates and not sheriff_exploded:
                    try:
                        candidates = await self._handle_sheriff_withdraw(game, candidates)
                    except GameTimeoutError:
                        raise
                    except Exception as e:
                        print(f"[Game {game.id}] Sheriff withdraw error: {e}")
                        traceback.print_exc()

                await self.repo.update_game(game, current_phase=GamePhase.SHERIFF_VOTE.value)
                await self._notify(game.id, "phase_change", {"phase": "sheriff_vote"})
                if candidates and not sheriff_exploded:
                    cand_str = "、".join(f"{c['seat']}号" for c in candidates)
                    await self._notify(game.id, "info", {
                        "message": f"竞选演说结束。候选人：{cand_str}。请所有非候选玩家投票选出警长。"
                    })
                else:
                    await self._notify(game.id, "info", {
                        "message": "没有人参与竞选，本局无警长。"
                    })
                try:
                    await self._handle_sheriff_vote(game, candidates, all_candidate_seats)
                except WolfExplodeException:
                    sheriff_exploded = True
                    game.sheriff_player_seat = None
                    await self._notify(game.id, "info", {"message": "警上自爆，警徽被吞，本局无警长。"})
                except GameTimeoutError:
                    raise
                except Exception as e:
                    print(f"[Game {game.id}] Sheriff vote error: {e}")
                    traceback.print_exc()
                await self.session.commit()

            # Night resolve (announce deaths)
            await self.repo.update_game(game, current_phase=GamePhase.NIGHT_RESOLVE.value)
            await self._notify(game.id, "phase_change", {"phase": "night_resolve", "round": game.current_round})
            await self._notify(game.id, "info", {"message": "现在公布昨晚的情况。"})
            try:
                night_deaths = await self._handle_night_resolve(game, wolf_target, saved, poison_target)
            except GameTimeoutError:
                raise
            except Exception as e:
                print(f"[Game {game.id}] Night resolve error: {e}")
                traceback.print_exc()
                night_deaths = []

            # 先遗言、移交警徽，再让猎人开枪。被开枪的人没有遗言。
            try:
                if game.current_round == 1:
                    for death in night_deaths:
                        await self._handle_last_words(game, death["seat"])
                else:
                    for death in night_deaths:
                        await self._handle_sheriff_transfer(game, death["seat"])
                for death in night_deaths:
                    await self._resolve_hunter_chain(game, death["seat"])
            except GameTimeoutError:
                raise
            except Exception as e:
                print(f"[Game {game.id}] Night aftermath error: {e}")
                traceback.print_exc()
            await self.session.commit()

            # Win check
            await self.repo.update_game(game, current_phase=GamePhase.WIN_CHECK.value)
            await self._notify(game.id, "phase_change", {"phase": "win_check", "round": game.current_round})
            players = await self.repo.get_players(game.id)
            winner = check_winner(players)
            if winner:
                await self._end_game(game, winner)
                break

            if sheriff_exploded:
                sheriff_exploded = False
                continue  # First-day explosion also skips discussion and exile.

            # ── Day ──
            await self.repo.update_game(game, current_phase=GamePhase.DAY_START.value)
            await self._notify(game.id, "phase_change", {
                "phase": "day_start", "round": game.current_round,
            })

            # Day speech
            await self.repo.update_game(game, current_phase=GamePhase.DAY_SPEECH.value)
            await self._notify(game.id, "phase_change", {"phase": "day_speech"})
            await self._notify(game.id, "info", {
                "message": "现在按顺序发言，请留意前置位的观点。"
            })
            speeches = []
            wolf_exploded = False
            try:
                speeches = await self._handle_day_speech(game)
            except WolfExplodeException:
                wolf_exploded = True
            except GameTimeoutError:
                raise
            except Exception as e:
                print(f"[Game {game.id}] Day speech error: {e}")
                traceback.print_exc()
            await self.session.commit()

            if wolf_exploded:
                players = await self.repo.get_players(game.id)
                winner = check_winner(players)
                if winner:
                    await self._end_game(game, winner)
                    break
                continue  # 跳过投票，直接进入下一轮夜晚

            # Day vote
            await self.repo.update_game(game, current_phase=GamePhase.DAY_VOTE.value)
            await self._notify(game.id, "phase_change", {"phase": "day_vote"})
            await self._notify(game.id, "info", {
                "message": "讨论结束，请所有存活玩家投票，选出你认为最可疑的玩家。"
            })
            voted_out = None
            try:
                voted_out = await self._handle_day_vote(game, speeches)
            except WolfExplodeException:
                wolf_exploded = True
            except GameTimeoutError:
                raise
            except Exception as e:
                print(f"[Game {game.id}] Day vote error: {e}")
                traceback.print_exc()
                raise
            await self.session.commit()

            if wolf_exploded:
                players = await self.repo.get_players(game.id)
                winner = check_winner(players)
                if winner:
                    await self._end_game(game, winner)
                    break
                continue

            # 放逐者有遗言。猎人开枪带走的人没有遗言，只移交警徽并允许连环开枪。
            if voted_out:
                try:
                    await self._handle_last_words(game, voted_out)
                    await self._resolve_hunter_chain(game, voted_out)
                except GameTimeoutError:
                    raise
                except Exception as e:
                    print(f"[Game {game.id}] Vote aftermath error: {e}")
                    traceback.print_exc()
                    raise

            # Win check
            await self.repo.update_game(game, current_phase=GamePhase.WIN_CHECK.value)
            await self._notify(game.id, "phase_change", {"phase": "win_check", "round": game.current_round})
            players = await self.repo.get_players(game.id)
            winner = check_winner(players)
            if winner:
                await self._end_game(game, winner)
                break

            # Durable boundary: all exile words, badge transfers and hunter
            # chains have completed, and the match can enter the next night.
            config = dict(getattr(game, "config", None) or {})
            config["resume_checkpoint"] = {"kind": "day_complete", "round": game.current_round,
                                            "last_wolf_target": last_wolf_target}
            await self.repo.update_game(game, config=config)
            await self.session.commit()

    async def _end_game(self, game: Game, winner: str):
        w = Winner.good if winner == "good" else Winner.evil
        await self.repo.update_game(
            game, status=GameStatus.finished, winner=w,
            current_phase=GamePhase.GAME_END.value,
        )
        await self.repo.add_event(
            game.id, game.current_round, "game_end",
            "game_end", content=f"游戏结束！{'好人阵营' if winner == 'good' else '狼人阵营'}取得胜利！",
            visibility=Visibility.all,
        )

        # Save per-player game results
        players = await self.repo.get_players(game.id)
        results = []
        for p in players:
            is_good = get_role_team(p.role) == "good"
            is_winner = (winner == "good" and is_good) or (winner == "evil" and not is_good)
            model_name = await self._get_player_model_name(p)
            results.append(GameResult(
                game_id=game.id,
                user_id=p.user_id,
                seat=p.seat,
                role=p.role.value,
                model_name=model_name,
                is_human=p.is_human,
                is_winner=is_winner,
                is_alive=p.is_alive,
            ))
        await self.repo.save_game_results(results)
        await self.session.commit()

        # Reveal all roles (include model_name from results we just built)
        result_map = {r.seat: r.model_name for r in results}
        reveal = [{"seat": p.seat, "role": p.role.value, "role_name": get_role_name(p.role),
                    "is_human": p.is_human, "is_alive": p.is_alive,
                    "nickname": p.nickname,
                    "model_name": result_map.get(p.seat)}
                   for p in players]
        all_events = await self.repo.get_all_events(game.id)
        key_events = extract_key_events(all_events)
        await self._notify(game.id, "game_end", {
            "winner": winner,
            "winner_text": "好人阵营获胜" if winner == "good" else "狼人阵营获胜",
            "players": reveal,
            "key_events": key_events,
        })

        # Update AI experiences after game ends
        try:
            await self._update_ai_experiences(game, players, winner)
        except Exception as e:
            print(f"[Game {game.id}] Experience update error: {e}")

    async def _update_ai_experiences(self, game: Game, players: list, winner: str):
        """Update each AI player's habits from what they themselves lived through."""
        for p in players:
            if p.is_human or not p.user_id:
                continue
            is_good = get_role_team(p.role) == "good"
            is_winner = (winner == "good" and is_good) or (winner == "evil" and not is_good)
            role_cfg = ROLE_CONFIGS[p.role]

            # Load current experience, and only what this player actually lived through
            user = await self.repo.get_user_by_id(p.user_id)
            current_experience = clip_experience((user.experience or "") if user else "")
            personal = await self._get_memory_summary(game.id, p.seat)
            pers = PERSONALITIES.get(p.personality)

            try:
                result = await ai_decide(
                    seat=p.seat,
                    role=p.role,
                    personality_key=p.personality,
                    template_name="update_experience.txt",
                    template_vars={
                        "role_name": role_cfg.name_cn,
                        "team": role_cfg.team,
                        "is_winner": is_winner,
                        "events_summary": personal,
                        "current_experience": current_experience,
                        "personality_name": pers.name_cn if pers else "",
                        "speech_style": pers.speech_style if pers else "",
                    },
                    action_type="any",
                    model_name=await self._get_player_model(p),
                )
                new_experience = clip_experience(result.get("experience", ""))
                if new_experience and new_experience != "（系统回退决策）":
                    await self.repo.update_user_experience(p.user_id, new_experience)
                    # Release the writer lock before the next player's model call.
                    await self.session.commit()
                    self._experience_cache[p.user_id] = new_experience
            except GameTimeoutError:
                raise
            except Exception as e:
                print(f"[Game {game.id}] Experience update for seat {p.seat} failed: {e}")
                continue

        await self.session.commit()

    async def _end_game_timeout(self, game: Game):
        """End game due to human player timeout."""
        await self.repo.update_game(
            game, status=GameStatus.finished,
            current_phase=GamePhase.GAME_END.value,
        )
        await self.repo.add_event(
            game.id, game.current_round, "game_end",
            "game_end", content="游戏结束！玩家长时间未操作，本局自动结束。",
            visibility=Visibility.all,
        )
        await self.session.commit()

        players = await self.repo.get_players(game.id)
        reveal = [{"seat": p.seat, "role": p.role.value, "role_name": get_role_name(p.role),
                    "is_human": p.is_human, "is_alive": p.is_alive,
                    "nickname": p.nickname, "model_name": p.model_name}
                   for p in players]
        await self._notify(game.id, "game_end", {
            "winner": "timeout",
            "winner_text": "玩家超时，本局游戏结束",
            "players": reveal,
        })
