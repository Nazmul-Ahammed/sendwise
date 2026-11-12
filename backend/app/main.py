import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models
from .db import Base, engine
from .routes import router
from .seed import seed

SEED_ON_STARTUP = os.getenv("SEED_ON_STARTUP", "1") == "1"
# Table creation is non-destructive and must always run so newly introduced
# models are available even when demo-data seeding is disabled in production.
#
# Wrapped because it runs at import on EVERY serverless cold start, and Vercel can
# boot several instances at once: two of them race, and the loser fails on
# UniqueViolation "pg_type_typname_nsp_index" for a composite type whose table the
# winner just created. That is not a real failure - the tables exist - but an
# uncaught error here aborts the import and the whole function returns 500, which
# is what made the live app intermittently show nothing.
try:
    Base.metadata.create_all(bind=engine)
except Exception as _create_exc:  # noqa: BLE001 - existing tables are fine; never block boot
    import logging as _logmod

    _logmod.getLogger("sendwise").warning(
        "create_all skipped (%s): %s", type(_create_exc).__name__, str(_create_exc)[:200])


MIGRATION_FAILURES = []


def _ensure_columns():
    """Add columns that were introduced after a table already existed.

    `create_all` creates missing TABLES but never alters an existing one, so a new
    field on an existing model needs an explicit, idempotent ALTER. Safe to re-run on
    every boot.

    A failure here used to be swallowed completely, which meant the app could boot with a
    column missing and then fail later, mid-request, somewhere unrelated and in a way that
    pointed at the wrong thing. It still does not block boot - a locked table must never
    stop the app - but every failure is now recorded and the columns are verified
    afterwards, so a missing one is reported at startup instead of being discovered by a
    user.
    """
    from sqlalchemy import inspect, text

    # (table, column) that must exist once this has run. Checked afterwards rather than
    # trusted, because "the ALTER raised" and "the column is absent" are not the same
    # thing: an ALTER can fail because the column already exists.
    required = (
        ("contacts", "email_status"),
        ("contacts", "email_reason"),
        ("contacts", "email_checked_at"),
        ("contacts", "deleted_at"),
        ("audiences", "deleted_at"),
        ("campaigns", "deleted_at"),
        ("campaigns", "sequence_slug"),
        ("campaigns", "audience_options"),
        ("campaigns", "send_mode"),
        ("campaigns", "send_test_first"),
        ("campaigns", "provider_message_id"),
        ("campaigns", "delivery_json"),
        ("sequences", "deleted_at"),
        ("audience_members", "tags"),
        ("sequence_enrollments", "pending_ref"),
        ("sequence_enrollments", "pending_at"),
        ("sequence_enrollments", "attempts"),
    )

    statements = (
        "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_status VARCHAR DEFAULT 'unverified'",
        "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_reason VARCHAR DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_checked_at VARCHAR DEFAULT ''",
        # Soft delete - nothing in this app is ever physically removed.
        "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS deleted_at VARCHAR DEFAULT ''",
        "ALTER TABLE audiences ADD COLUMN IF NOT EXISTS deleted_at VARCHAR DEFAULT ''",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS deleted_at VARCHAR DEFAULT ''",
        "ALTER TABLE sequences ADD COLUMN IF NOT EXISTS deleted_at VARCHAR DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_contacts_deleted_at ON contacts (deleted_at)",
        "CREATE INDEX IF NOT EXISTS ix_audiences_deleted_at ON audiences (deleted_at)",
        "CREATE INDEX IF NOT EXISTS ix_campaigns_deleted_at ON campaigns (deleted_at)",
        # Tags became audience-scoped: they live on the membership, not the contact.
        "ALTER TABLE audience_members ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]'::jsonb",
        # A send records which sequence template it came from.
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS sequence_slug VARCHAR DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_campaigns_sequence_slug ON campaigns (sequence_slug)",
        # How a send resolves its recipients and what it is waiting on. These tables were
        # created before the fields existed, so an existing install needs them added.
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS audience_options JSONB DEFAULT '{}'::jsonb",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS send_mode VARCHAR DEFAULT 'direct'",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS send_test_first VARCHAR DEFAULT 'false'",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS provider_message_id VARCHAR DEFAULT ''",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS delivery_json JSONB DEFAULT '{}'::jsonb",
        # Delivery safety on the sequence engine (see models.SequenceEnrollment).
        "ALTER TABLE sequence_enrollments ADD COLUMN IF NOT EXISTS pending_ref VARCHAR DEFAULT ''",
        "ALTER TABLE sequence_enrollments ADD COLUMN IF NOT EXISTS pending_at VARCHAR DEFAULT ''",
        "ALTER TABLE sequence_enrollments ADD COLUMN IF NOT EXISTS attempts INTEGER DEFAULT 0",
        # The on-disk mirror of a snapshot. Existing rows keep '' - their payload is
        # still in the database, they simply predate the file copy.
        "ALTER TABLE backups ADD COLUMN IF NOT EXISTS file_path VARCHAR DEFAULT ''",
    )

    for stmt in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:  # noqa: BLE001 - never block boot on a migration
            MIGRATION_FAILURES.append(f"{type(exc).__name__}: {str(exc)[:160]} :: {stmt[:90]}")

    # Verify rather than assume: this is the difference between "the statement did not
    # raise" and "the schema is actually what the code expects".
    try:
        insp = inspect(engine)
        present = {t: {c["name"] for c in insp.get_columns(t)}
                   for t in {tbl for tbl, _ in required}}
        for table, column in required:
            if column not in present.get(table, set()):
                MIGRATION_FAILURES.append(f"MISSING COLUMN {table}.{column}")
    except Exception as exc:  # noqa: BLE001
        MIGRATION_FAILURES.append(f"schema verification failed: {type(exc).__name__}")

    if MIGRATION_FAILURES:
        _log = __import__("logging").getLogger("sendwise")
        for line in MIGRATION_FAILURES:
            _log.error("migration problem: %s", line)


_ensure_columns()


def _backfill_delivery_ledger():
    """Copy per-recipient rows out of legacy `campaigns.delivery_json` blobs, once.

    Sends made before `send_records` existed kept their recipients inside a JSON column
    on the campaign. Without this, the new ledger would look like nothing had ever been
    sent - and every historical campaign would keep depending on a blob that is not
    queryable and is overwritten by the next send.

    Idempotent and guarded by a settings row, so it is safe to run more than once.

    Opt-in (LEDGER_BACKFILL_ON_START=1) rather than automatic: it is a one-time import,
    and a cold start is the worst moment to wait on a database that may be slow or
    unreachable. Normal operation does not need it - the ledger fills itself at send
    time - and `POST /api/reports/delivery-ledger/backfill` runs it on demand.
    """
    if os.getenv("LEDGER_BACKFILL_ON_START", "0") != "1":
        return
    from . import delivery_ledger
    from .db import SessionLocal

    db = SessionLocal()
    try:
        result = delivery_ledger.backfill(db)
        if result.get("imported"):
            import logging as _miglog
            _miglog.getLogger("sendwise").info(
                "Delivery ledger backfill: %s row(s) imported", result["imported"])
    except Exception as _exc:  # noqa: BLE001 - never block boot
        import logging as _miglog
        _miglog.getLogger("sendwise").warning("Delivery ledger backfill skipped: %s", _exc)
    finally:
        db.close()


_backfill_delivery_ledger()


def _seed_membership_tags():
    """Copy each contact's legacy tags down onto its memberships, once.

    Tags used to live on the contact, which made them global - a tag created in one
    audience appeared in all of them. They are now audience-scoped (stored on the
    membership), so the existing tags are copied into every list the contact is in;
    otherwise every audience would look like it had lost its tags.

    Idempotent: only memberships that are still empty are filled.
    """
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            # `contacts.tags` is json while `audience_members.tags` is jsonb, so the
            # comparison must cast - a bare `<> '[]'::jsonb` raises a type error and
            # the whole migration would silently do nothing.
            result = conn.execute(text(
                "UPDATE audience_members m SET tags = c.tags::jsonb FROM contacts c "
                "WHERE m.contact_id = c.id "
                "AND (m.tags IS NULL OR m.tags = '[]'::jsonb) "
                "AND c.tags IS NOT NULL AND c.tags::jsonb <> '[]'::jsonb"
            ))
            if result.rowcount:
                import logging as _miglog

                _miglog.getLogger("sendwise").info(
                    "Tag migration: copied tags onto %s membership(s)", result.rowcount)
    except Exception as _exc:  # noqa: BLE001 - never block boot, but never hide it either
        import logging as _miglog

        _miglog.getLogger("sendwise").warning("Tag migration skipped: %s", _exc)


_seed_membership_tags()


def _seed_audience_tags():
    """Give every audience an explicit tag list, built from its memberships.

    Idempotent: registers an (audience, tag) pair only once.
    """
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            result = conn.execute(text(
                "INSERT INTO audience_tags (audience_id, name) "
                "SELECT DISTINCT m.audience_id, lower(trim(t.tag)) "
                "FROM audience_members m, jsonb_array_elements_text(m.tags) AS t(tag) "
                "WHERE lower(trim(t.tag)) <> '' "
                "AND NOT EXISTS (SELECT 1 FROM audience_tags at2 "
                "                WHERE at2.audience_id = m.audience_id "
                "                AND lower(at2.name) = lower(trim(t.tag)))"
            ))
            if result.rowcount:
                import logging as _miglog

                _miglog.getLogger("sendwise").info(
                    "Tag registry: registered %s audience tag(s)", result.rowcount)
    except Exception as _exc:  # noqa: BLE001 - never block boot, never hide it
        import logging as _miglog

        _miglog.getLogger("sendwise").warning("Tag registry seed skipped: %s", _exc)


_seed_audience_tags()


def _backfill_campaign_sequence_slug():
    """Link existing sends to the sequence they were built from.

    Sends used to record only the sequence NAME inside `sequence_type`, which makes
    it impossible to ask "which sends use this sequence". The slug is now stored, so
    a sequence can show its own activity. Idempotent: only blanks are filled.
    """
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            linked = conn.execute(text(
                "UPDATE campaigns c SET sequence_slug = s.slug FROM sequences s "
                "WHERE coalesce(c.sequence_slug, '') = '' "
                "AND c.sequence_type IS NOT NULL "
                "AND c.sequence_type LIKE '%(' || s.name || ')%'"
            )).rowcount
            singles = conn.execute(text(
                "UPDATE campaigns SET sequence_slug = 'single' "
                "WHERE coalesce(sequence_slug, '') = '' "
                "AND (sequence_type IS NULL OR sequence_type LIKE 'Single%')"
            )).rowcount
            if linked or singles:
                import logging as _miglog

                _miglog.getLogger("sendwise").info(
                    "Sequence links: %s branched send(s), %s single send(s)", linked, singles)
    except Exception as _exc:  # noqa: BLE001 - never block boot, never hide it
        import logging as _miglog

        _miglog.getLogger("sendwise").warning("Sequence link backfill skipped: %s", _exc)


_backfill_campaign_sequence_slug()


def _soft_delete_columns_ready():
    """Are all four `deleted_at` columns actually present?

    The trashed-row guard rewrites every query for these models, so installing it
    against a table that is missing the column would break the whole API. Verify
    first; if the migration did not land, run without the guard rather than fail.
    """
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            found = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE column_name = 'deleted_at' "
                "AND table_name IN ('contacts','audiences','campaigns','sequences')"
            )).scalar()
        return int(found or 0) == 4
    except Exception:  # noqa: BLE001 - a non-Postgres or locked DB simply skips the guard
        return False


# Nothing in this app is ever physically deleted, so make trashed rows invisible to
# every query by default. Installed once here, at import time.
if _soft_delete_columns_ready():
    from .db import install_soft_delete_guard as _install_soft_delete_guard

    _install_soft_delete_guard()
else:
    import logging as _logging

    _logging.getLogger("sendwise").warning(
        "deleted_at columns missing - soft-delete guard NOT installed; "
        "trashed rows will be visible until the migration succeeds."
    )

if SEED_ON_STARTUP:
    # Local only: seed demo data.
    # Serverless (Vercel): run `python -m app.seed` once against the cloud DB, then set SEED_ON_STARTUP=0.
    seed()

# Sendwise backs itself up. On boot it checks whether a snapshot is due and takes
# one if so, so any downtime is caught up the moment the API comes back. Never
# fatal - a backup problem must not stop the API from starting.
try:
    from .backup import run_due_backup as _run_due_backup
    from .db import SessionLocal as _BackupSession

    _backup_db = _BackupSession()
    _backup_row = _run_due_backup(_backup_db, label="startup")
    _backup_db.close()
    if _backup_row is not None:
        import logging as _bklog

        _bklog.getLogger("sendwise").info("Startup backup #%s taken (%s bytes)",
                                          _backup_row.id, _backup_row.size_bytes)
except Exception:  # noqa: BLE001 - never block startup on a backup
    pass

# Ensure the cron secret is available - env var first, else from DB settings.
if not os.getenv("CRON_SECRET"):
    try:
        from .db import SessionLocal
        from . import models as _m
        _db = SessionLocal()
        for _key, _env in (("cron_secret", "CRON_SECRET"),):
            if os.getenv(_env):
                continue
            row = _db.query(_m.Setting).filter(_m.Setting.key == _key).first()
            if row and row.value:
                os.environ[_env] = row.value
        _db.close()
    except Exception:  # noqa: BLE001
        pass

# Move a legacy plaintext SendClean token into encrypted-at-rest storage and
# clear the plaintext copy. Never fatal: Settings reports the credential as
# unconfigured if SECRET_KEY is missing or the value cannot be encrypted.
try:
    from .db import SessionLocal as _SecretSession
    from . import models as _secret_models
    from .llm_providers import encrypt_secret as _encrypt_secret
    _secret_db = _SecretSession()
    for _legacy_key in ("sendclean_api_token",):
        _encrypted_key = _legacy_key + "_enc"
        _legacy = _secret_db.query(_secret_models.Setting).filter(_secret_models.Setting.key == _legacy_key).first()
        _encrypted = _secret_db.query(_secret_models.Setting).filter(_secret_models.Setting.key == _encrypted_key).first()
        if _legacy and _legacy.value and not (_encrypted and _encrypted.value):
            if _encrypted:
                _encrypted.value = _encrypt_secret(_legacy.value)
            else:
                _secret_db.add(_secret_models.Setting(key=_encrypted_key, value=_encrypt_secret(_legacy.value)))
            _legacy.value = ""
    _secret_db.commit()
    _secret_db.close()
except Exception:  # noqa: BLE001
    # Never prevent startup; Settings will report an unconfigured credential if
    # SECRET_KEY is absent or a legacy value cannot be migrated.
    pass

app = FastAPI(
    title="Sendwise API",
    description="Sendwise - the autonomous email marketing orchestrator (Agent OS)",
    version="1.0.0",
)

# CORS: if CORS_ORIGINS set (comma-separated) use it with credentials; otherwise allow all origins
# (token-based auth - no cookies - so wildcard is safe on the deployed API).
_cors = os.getenv("CORS_ORIGINS", "")
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(router)

# Hosted MCP endpoint - stateless JSON-RPC, token-protected (MCP_TOKEN env).
#
# Mounted twice on purpose. The canonical deployment serves the app on ONE domain
# (https://sendwise.ibos.agency) and its edge only proxies /api/* through to this
# service; every other path is handled by the Next.js frontend. So /mcp reached the
# frontend and returned its 404 page, which made the MCP look dead even though the
# router was mounted. "/api/mcp" is the path that actually arrives here.
#
# /mcp is kept for a direct-to-service deployment; /api/mcp is the canonical one.
from .mcp_min import router as mcp_router
app.include_router(mcp_router)
app.include_router(mcp_router, prefix="/api")

# Email provider surface (SendClean-only status + credential test).
from .provider_routes import router as provider_router
app.include_router(provider_router)

# Saved audiences, contacts and tags.
from .audience_routes import router as audience_router
app.include_router(audience_router)
