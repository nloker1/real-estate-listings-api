from app.preview import require_writable_mode
import asyncio
from app.database import engine
from app.models import Base

async def init():
    require_writable_mode()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tables created!")

if __name__ == "__main__":
    asyncio.run(init())
