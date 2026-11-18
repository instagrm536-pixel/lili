from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.db import SessionLocal
from app.database.models import QRCollection, QRItem


class QRService:
    def __init__(self, sessions: async_sessionmaker = SessionLocal):
        self.sessions = sessions

    async def create_collection(
        self,
        owner_user_id: int,
        public_token: str,
        items: list[dict],
    ) -> int:
        async with self.sessions.begin() as session:
            collection = QRCollection(
                owner_user_id=owner_user_id,
                public_token=public_token,
            )
            session.add(collection)
            await session.flush()
            session.add_all(
                QRItem(qr_id=collection.id, sort_order=index, **item)
                for index, item in enumerate(items, start=1)
            )
            await session.flush()
            return collection.id

    async def get_public_collection(
        self, public_token: str
    ) -> tuple[QRCollection, list[QRItem]] | None:
        async with self.sessions() as session:
            collection = await session.scalar(
                select(QRCollection).where(
                    QRCollection.public_token == public_token,
                    QRCollection.status == "active",
                )
            )
            if collection is None:
                return None
            result = await session.scalars(
                select(QRItem)
                .where(QRItem.qr_id == collection.id)
                .order_by(QRItem.sort_order)
            )
            return collection, list(result.all())

    async def get_item(self, collection_id: int, item_id: int) -> QRItem | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(QRItem).where(
                    QRItem.qr_id == collection_id,
                    QRItem.id == item_id,
                )
            )

    async def delete_owned_collection(
        self, collection_id: int, owner_user_id: int
    ) -> list[str] | None:
        async with self.sessions.begin() as session:
            collection = await session.scalar(
                select(QRCollection).where(
                    QRCollection.id == collection_id,
                    QRCollection.owner_user_id == owner_user_id,
                    QRCollection.status == "active",
                )
            )
            if collection is None:
                return None

            result = await session.scalars(
                select(QRItem).where(QRItem.qr_id == collection_id)
            )
            items = list(result.all())
            storage_keys = [
                item.storage_key for item in items if item.storage_key
            ]
            for item in items:
                await session.delete(item)
            await session.delete(collection)
            return storage_keys
