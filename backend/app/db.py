import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


def stamp():
    """The current UTC time as an ISO string, for records that carry a timestamp.

    Job rows used to be written with the literal string "just now" in 42 places, so the
    activity log could never say when anything happened - only that it had. Stored in UTC
    and formatted for display on the way out, never stored pre-formatted.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://sendwise:sendwise@localhost:5432/sendwise",
)

SERVERLESS = os.getenv("SERVERLESS", "0") == "1"

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
elif SERVERLESS:
    # Serverless (Vercel/Lambda): no connection pooling - new conn per invocation.
    # Neon autosuspend can make the first connection slow, so give it a generous
    # connect timeout and a statement timeout so requests fail cleanly instead of hanging.
    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        connect_args={"connect_timeout": 60, "options": "-c statement_timeout=60000"},
    )
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def install_soft_delete_guard():
    """Hide trashed rows from every query, app-wide, by default.

    Soft delete only protects data if nothing forgets to filter. Rather than rely on
    ~30 call sites each remembering `deleted_at == ""`, this installs one ORM-level
    guard so a trashed audience, contact, send or sequence can never leak back into a
    list, a send, a count or a dashboard.

    A query that genuinely needs trashed rows - Restore, the Trash view, or reviving
    an address that is re-added - opts in with:

        db.query(Model).execution_options(include_trashed=True)
    """
    from sqlalchemy import event
    from sqlalchemy.orm import with_loader_criteria

    from . import models

    guarded = (models.Audience, models.Contact, models.Campaign, models.Sequence)

    @event.listens_for(SessionLocal, "do_orm_execute")
    def _hide_trashed(state):  # noqa: ANN001 - SQLAlchemy event signature
        if not state.is_select:
            return
        if state.execution_options.get("include_trashed"):
            return
        for model in guarded:
            state.statement = state.statement.options(
                with_loader_criteria(model, model.deleted_at == "", include_aliases=True)
            )
