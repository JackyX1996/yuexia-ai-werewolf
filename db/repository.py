from typing import Optional, List
from sqlalchemy import Integer, cast, func, or_, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.game import Game, GamePlayer, GameStatus, Role, WitchState, CauseOfDeath
from models.event import GameEvent, Visibility
from models.memory import PlayerMemory
from models.user import User, UserSession
from models.game_result import GameResult


class GameRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.event_hook = None

    # ── Game CRUD ──

    async def create_game(self, config: dict = None) -> Game:
        game = Game(status=GameStatus.waiting, current_phase="created", current_round=0, config=config)
        self.session.add(game)
        await self.session.flush()
        return game

    async def get_game(self, game_id: int) -> Optional[Game]:
        return await self.session.get(Game, game_id)

    async def update_game(self, game: Game, **kwargs) -> Game:
        for k, v in kwargs.items():
            setattr(game, k, v)
        await self.session.flush()
        return game

    # ── Players ──

    async def add_player(self, game_id: int, seat: int, role: Role,
                         is_human: bool = False, personality: str = None,
                         nickname: str = None, user_id: int = None,
                         model_name: str = None, model_config_id: str = None) -> GamePlayer:
        player = GamePlayer(
            game_id=game_id, seat=seat, role=role,
            is_human=is_human, personality=personality, nickname=nickname,
            user_id=user_id, model_name=model_name, model_config_id=model_config_id,
        )
        self.session.add(player)
        await self.session.flush()
        return player

    async def get_players(self, game_id: int) -> list[GamePlayer]:
        result = await self.session.execute(
            select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.seat)
        )
        return list(result.scalars().all())

    async def get_player_by_seat(self, game_id: int, seat: int) -> Optional[GamePlayer]:
        result = await self.session.execute(
            select(GamePlayer).where(
                and_(GamePlayer.game_id == game_id, GamePlayer.seat == seat)
            )
        )
        return result.scalar_one_or_none()

    async def get_alive_players(self, game_id: int) -> list[GamePlayer]:
        result = await self.session.execute(
            select(GamePlayer).where(
                and_(GamePlayer.game_id == game_id, GamePlayer.is_alive == True)
            ).order_by(GamePlayer.seat)
        )
        return list(result.scalars().all())

    async def kill_player(self, player: GamePlayer, cause: CauseOfDeath, round_num: int):
        player.is_alive = False
        player.cause_of_death = cause
        player.died_at_round = round_num
        await self.session.flush()

    # ── Witch State ──

    async def get_witch_state(self, game_id: int) -> Optional[WitchState]:
        return await self.session.get(WitchState, game_id)

    async def create_witch_state(self, game_id: int) -> WitchState:
        ws = WitchState(game_id=game_id, has_antidote=True, has_poison=True)
        self.session.add(ws)
        await self.session.flush()
        return ws

    async def update_witch_state(self, game_id: int, **kwargs):
        ws = await self.get_witch_state(game_id)
        if ws:
            for k, v in kwargs.items():
                setattr(ws, k, v)
            await self.session.flush()

    # ── Events ──

    async def add_event(self, game_id: int, round_num: int, phase: str,
                        event_type: str, actor_seat: int = None,
                        target_seat: int = None, content: str = None,
                        visibility: Visibility = Visibility.all) -> GameEvent:
        event = GameEvent(
            game_id=game_id, round=round_num, phase=phase,
            event_type=event_type, actor_seat=actor_seat,
            target_seat=target_seat, content=content, visibility=visibility,
        )
        self.session.add(event)
        await self.session.flush()
        if self.event_hook is not None:
            await self.event_hook(event)
        return event

    async def get_visible_events(self, game_id: int, seat: int, role: Role,
                                 up_to_round: int = None) -> list[GameEvent]:
        """Core visibility query: returns events a player is allowed to see."""
        conditions = [GameEvent.game_id == game_id]
        if up_to_round is not None:
            conditions.append(GameEvent.round <= up_to_round)

        result = await self.session.execute(
            select(GameEvent).where(and_(*conditions)).order_by(GameEvent.id)
        )
        all_events = list(result.scalars().all())

        visible = []
        for e in all_events:
            if e.visibility == Visibility.all:
                visible.append(e)
            elif e.visibility == Visibility.werewolves and role == Role.werewolf:
                visible.append(e)
            elif e.visibility == Visibility.self_only and e.actor_seat == seat:
                visible.append(e)
            # system events are never shown to players
        return visible

    async def get_all_events(self, game_id: int, round_num: int = None) -> list[GameEvent]:
        """Get all events (for review/admin)."""
        conditions = [GameEvent.game_id == game_id]
        if round_num is not None:
            conditions.append(GameEvent.round == round_num)
        result = await self.session.execute(
            select(GameEvent).where(and_(*conditions)).order_by(GameEvent.id)
        )
        return list(result.scalars().all())

    # ── Memory ──

    async def get_latest_memory(self, game_id: int, seat: int) -> Optional[PlayerMemory]:
        result = await self.session.execute(
            select(PlayerMemory).where(
                and_(PlayerMemory.game_id == game_id, PlayerMemory.seat == seat)
            ).order_by(PlayerMemory.as_of_round.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def save_memory(self, game_id: int, seat: int, as_of_round: int,
                          summary: str, suspicion_map: dict = None,
                          key_facts: dict = None) -> PlayerMemory:
        mem = PlayerMemory(
            game_id=game_id, seat=seat, as_of_round=as_of_round,
            summary=summary, suspicion_map=suspicion_map, key_facts=key_facts,
        )
        self.session.add(mem)
        await self.session.flush()
        return mem

    # ── User / Auth ──

    async def get_user_by_username(self, username: str) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        return await self.session.get(User, user_id)

    async def get_ai_users(self, count: int = 8) -> List[User]:
        result = await self.session.execute(
            select(User).where(User.is_ai == True)
        )
        all_ai = list(result.scalars().all())
        import random
        random.shuffle(all_ai)
        return all_ai[:count]

    async def create_session(self, user_id: int, token: str) -> UserSession:
        sess = UserSession(user_id=user_id, token=token, is_active=True)
        self.session.add(sess)
        await self.session.flush()
        return sess

    async def get_session_by_token(self, token: str) -> Optional[UserSession]:
        result = await self.session.execute(
            select(UserSession).where(
                and_(UserSession.token == token, UserSession.is_active == True)
            )
        )
        return result.scalar_one_or_none()

    async def deactivate_session(self, token: str):
        sess = await self.get_session_by_token(token)
        if sess:
            sess.is_active = False
            await self.session.flush()

    # ── Game list ──

    async def get_games_by_user(self, user_id: int, page: int = 1, page_size: int = 10):
        """Returns (games, total_count) with pagination.

        A person appears here as a seated player, or as the observer of a god game.
        """
        seated = select(GamePlayer.game_id).where(GamePlayer.user_id == user_id)
        observer = cast(func.json_extract(Game.config, "$.observer_user_id"), Integer)
        member = or_(Game.id.in_(seated), observer == user_id)
        count_result = await self.session.execute(
            select(func.count(Game.id)).where(member)
        )
        total = count_result.scalar() or 0

        offset = (page - 1) * page_size
        result = await self.session.execute(
            select(Game).where(member).order_by(Game.id.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def save_game_results(self, results: List[GameResult]):
        for r in results:
            self.session.add(r)
        await self.session.flush()

    async def create_user(self, username: str, password_hash: str, nickname: str, is_ai: bool = False) -> User:
        user = User(
            username=username,
            password_hash=password_hash,
            nickname=nickname,
            is_ai=is_ai,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_stats_by_user(self, limit: int = 10) -> list:
        """Get win rate stats grouped by AI user, top N by total games."""
        from sqlalchemy import func, case, cast, Float
        result = await self.session.execute(
            select(
                GameResult.user_id,
                User.nickname,
                func.count().label("total"),
                func.sum(case((GameResult.is_winner == True, 1), else_=0)).label("wins"),
                func.sum(case((GameResult.role == "werewolf", 1), else_=0)).label("wolf_total"),
                func.sum(case((and_(GameResult.role == "werewolf", GameResult.is_winner == True), 1), else_=0)).label("wolf_wins"),
                func.sum(case((GameResult.role != "werewolf", 1), else_=0)).label("good_total"),
                func.sum(case((and_(GameResult.role != "werewolf", GameResult.is_winner == True), 1), else_=0)).label("good_wins"),
            )
            .join(User, GameResult.user_id == User.id)
            .where(User.is_ai == True)
            .group_by(GameResult.user_id, User.nickname)
            .order_by(
                (func.sum(case((GameResult.is_winner == True, 1), else_=0)) * 1.0 / func.count()).desc()
            )
            .limit(limit)
        )
        return [dict(r._mapping) for r in result.all()]

    async def get_stats_by_model(self) -> list:
        """Get win rate stats grouped by model_name."""
        from sqlalchemy import func, case
        result = await self.session.execute(
            select(
                GameResult.model_name,
                func.count().label("total"),
                func.sum(case((GameResult.is_winner == True, 1), else_=0)).label("wins"),
                func.sum(case((GameResult.role == "werewolf", 1), else_=0)).label("wolf_total"),
                func.sum(case((and_(GameResult.role == "werewolf", GameResult.is_winner == True), 1), else_=0)).label("wolf_wins"),
                func.sum(case((GameResult.role != "werewolf", 1), else_=0)).label("good_total"),
                func.sum(case((and_(GameResult.role != "werewolf", GameResult.is_winner == True), 1), else_=0)).label("good_wins"),
            )
            .where(GameResult.is_human == False)
            .where(GameResult.model_name.isnot(None))
            .group_by(GameResult.model_name)
            .order_by(
                (func.sum(case((GameResult.is_winner == True, 1), else_=0)) * 1.0 / func.count()).desc()
            )
        )
        return [dict(r._mapping) for r in result.all()]

    async def update_user_experience(self, user_id: int, experience_text: str):
        """Update the experience field for an AI user."""
        user = await self.session.get(User, user_id)
        if user:
            user.experience = experience_text
            await self.session.flush()

    async def get_stats_by_human(self) -> list:
        from sqlalchemy import func, case
        result = await self.session.execute(
            select(
                GameResult.user_id,
                User.nickname,
                func.count().label("total"),
                func.sum(case((GameResult.is_winner == True, 1), else_=0)).label("wins"),
                func.sum(case((GameResult.role == "werewolf", 1), else_=0)).label("wolf_total"),
                func.sum(case((and_(GameResult.role == "werewolf", GameResult.is_winner == True), 1), else_=0)).label("wolf_wins"),
                func.sum(case((GameResult.role != "werewolf", 1), else_=0)).label("good_total"),
                func.sum(case((and_(GameResult.role != "werewolf", GameResult.is_winner == True), 1), else_=0)).label("good_wins"),
            )
            .join(User, GameResult.user_id == User.id)
            .where(User.is_ai == False)
            .group_by(GameResult.user_id, User.nickname)
            .order_by(
                (func.sum(case((GameResult.is_winner == True, 1), else_=0)) * 1.0 / func.count()).desc()
            )
        )
        return [dict(r._mapping) for r in result.all()]
