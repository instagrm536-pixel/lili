from app.config import settings
from app.services.state import InMemoryState

class RateLimiter:
    def __init__(self, state: InMemoryState):
        self.state = state

    async def allowed(self, user_id: int) -> bool:
        key = f"rate:{user_id}"
        created = await self.state.set(
            key, "1", ex=settings.rate_limit_seconds, nx=True
        )
        return bool(created)
