import asyncio
import sys
import os

# Ensure the app directory is in the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine, Base
from app.models import Listing

async def reset_database():
    async with engine.begin() as conn:
        print("Dropping all tables...")
        # run_sync is required to bridge async engine to sync metadata methods
        await conn.run_sync(Base.metadata.drop_all)
        
        print("Creating all tables from current models...")
        await conn.run_sync(Base.metadata.create_all)
    
    print("Database is now synced with your models.")
    # Always dispose of the engine in a standalone script
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(reset_database())