# Worker entry point reserved for scalable background processing.
# The current implementation keeps the critical media path inside the async bot
# so direct Telegram URL sends can avoid unnecessary disk I/O.
import asyncio
from app.worker import worker_loop

if __name__ == "__main__":
    asyncio.run(worker_loop())
