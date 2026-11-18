from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings



def _make_engine(url: str, echo: bool = settings.DEBUG, **pool_kwargs):
    # hide_parameters keeps bound values (e.g. invitation token hashes) out of
    # SQLAlchemy's statement logs and error messages, even with echo on.
    return create_async_engine(url, echo=echo, hide_parameters=True, **pool_kwargs)


engine = _make_engine(settings.DATABASE_URL, pool_size=5, max_overflow=10)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides an async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
