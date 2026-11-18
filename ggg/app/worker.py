import asyncio
import logging

log = logging.getLogger(__name__)

async def worker_loop():
    # Reserved for future queue-heavy operations.
    # The current direct media path is intentionally async and does not require
    # a second copy of the media on disk.
    log.info("Worker started")
    while True:
        await asyncio.sleep(3600)
