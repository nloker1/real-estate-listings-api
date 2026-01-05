import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from .models import Base  # Import Base to create tables

# Load .env from the project root (one level up from app/)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Get the DATABASE_URL from .env
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in .env file. Check the file exists and is loaded correctly.")

# Create the async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Set to True for SQL debug logs
    future=True
)

# Async session factory
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Optional: Function to create tables (call on startup if needed)
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Dependency for endpoints
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session