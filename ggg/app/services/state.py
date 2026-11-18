import time


class InMemoryState:
    def __init__(self):
        self._values: dict[str, tuple[str, float | None]] = {}
        self._operations = 0

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool:
        self._operations += 1
        if self._operations % 256 == 0:
            now = time.monotonic()
            expired_keys = [
                stored_key
                for stored_key, (_, expires_at) in self._values.items()
                if expires_at is not None and expires_at <= now
            ]
            for stored_key in expired_keys:
                del self._values[stored_key]

        current = self._values.get(key)
        if current and current[1] is not None and current[1] <= time.monotonic():
            del self._values[key]
            current = None
        if nx and current is not None:
            return False

        expires_at = time.monotonic() + ex if ex is not None else None
        self._values[key] = (value, expires_at)
        return True

    async def get(self, key: str) -> str | None:
        current = self._values.get(key)
        if current is None:
            return None
        if current[1] is not None and current[1] <= time.monotonic():
            del self._values[key]
            return None
        return current[0]

    async def delete(self, key: str) -> bool:
        return self._values.pop(key, None) is not None


state = InMemoryState()
