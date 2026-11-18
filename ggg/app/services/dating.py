from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.db import SessionLocal
from app.database.models import DatingProfile, DatingRequest


class DatingService:
    def __init__(
        self,
        sessions: async_sessionmaker = SessionLocal,
    ):
        self.sessions = sessions

    async def get_profile(self, telegram_id: int) -> DatingProfile | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(DatingProfile).where(
                    DatingProfile.telegram_id == telegram_id
                )
            )

    async def update_username(self, telegram_id: int, username: str) -> None:
        async with self.sessions.begin() as session:
            profile = await session.scalar(
                select(DatingProfile).where(
                    DatingProfile.telegram_id == telegram_id
                )
            )
            if profile is not None:
                profile.username = username

    async def create_profile(
        self,
        telegram_id: int,
        username: str,
        full_name: str,
        age: int,
        gender: str,
    ) -> DatingProfile:
        profile = DatingProfile(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            age=age,
            gender=gender,
        )
        async with self.sessions.begin() as session:
            session.add(profile)
            await session.flush()
        return profile

    async def list_profiles(
        self,
        telegram_id: int,
        page: int,
        page_size: int,
    ) -> tuple[list[DatingProfile], bool]:
        existing_request = (
            select(DatingRequest.id)
            .where(
                or_(
                    and_(
                        DatingRequest.sender_telegram_id == telegram_id,
                        DatingRequest.recipient_telegram_id
                        == DatingProfile.telegram_id,
                    ),
                    and_(
                        DatingRequest.recipient_telegram_id == telegram_id,
                        DatingRequest.sender_telegram_id
                        == DatingProfile.telegram_id,
                    ),
                )
            )
            .correlate(DatingProfile)
            .exists()
        )
        async with self.sessions() as session:
            result = await session.scalars(
                select(DatingProfile)
                .where(
                    DatingProfile.telegram_id != telegram_id,
                    ~existing_request,
                )
                .order_by(DatingProfile.created_at, DatingProfile.id)
                .offset(page * page_size)
                .limit(page_size + 1)
            )
            profiles = list(result.all())
        return profiles[:page_size], len(profiles) > page_size

    async def create_request(
        self,
        sender_telegram_id: int,
        recipient_telegram_id: int,
    ) -> tuple[DatingRequest | None, str]:
        if sender_telegram_id == recipient_telegram_id:
            return None, "unavailable"

        between_users = or_(
            and_(
                DatingRequest.sender_telegram_id == sender_telegram_id,
                DatingRequest.recipient_telegram_id == recipient_telegram_id,
            ),
            and_(
                DatingRequest.sender_telegram_id == recipient_telegram_id,
                DatingRequest.recipient_telegram_id == sender_telegram_id,
            ),
        )
        async with self.sessions.begin() as session:
            profiles = await session.scalars(
                select(DatingProfile).where(
                    DatingProfile.telegram_id.in_(
                        [sender_telegram_id, recipient_telegram_id]
                    )
                )
            )
            if len(list(profiles.all())) != 2:
                return None, "unavailable"

            existing = await session.scalar(
                select(DatingRequest).where(between_users)
            )
            if existing is not None:
                return existing, "already_contacted"

            request = DatingRequest(
                sender_telegram_id=sender_telegram_id,
                recipient_telegram_id=recipient_telegram_id,
                status="pending",
            )
            session.add(request)
            await session.flush()
            return request, "created"

    async def respond_to_request(
        self,
        request_id: int,
        responder_telegram_id: int,
        accept: bool,
    ) -> tuple[DatingRequest, DatingProfile, DatingProfile] | None:
        async with self.sessions.begin() as session:
            request = await session.scalar(
                select(DatingRequest)
                .where(DatingRequest.id == request_id)
                .with_for_update()
            )
            if (
                request is None
                or request.recipient_telegram_id != responder_telegram_id
                or request.status != "pending"
            ):
                return None

            sender = await session.scalar(
                select(DatingProfile).where(
                    DatingProfile.telegram_id
                    == request.sender_telegram_id
                )
            )
            recipient = await session.scalar(
                select(DatingProfile).where(
                    DatingProfile.telegram_id
                    == request.recipient_telegram_id
                )
            )
            if sender is None or recipient is None:
                return None

            request.status = "accepted" if accept else "declined"
            await session.flush()
            return request, sender, recipient
