"""Automatic backups, taken by Sendwise itself.

No manual download and no external service: Sendwise snapshots its own data on a
schedule, keeps a rolling history, and can restore from any snapshot.

Two independent triggers, so a failure in one never stops the other:

  1. the 15-minute dispatch tick also asks "is a backup due?" - this piggybacks on
     the scheduler that is already running
  2. startup asks the same question, so a restart after downtime catches up

A restore is deliberately ADDITIVE. It re-adds what is missing and un-trashes what
was trashed - it never deletes anything. A backup can therefore only ever move the
data forward, which is the whole point of having one.
"""
from datetime import datetime, timedelta, timezone
import json as _json
import os
import pathlib

from sqlalchemy import func

from . import models

# Rolling history: keep this many automatic snapshots.
KEEP_BACKUPS = 30
# Take a new automatic snapshot at most this often.
BACKUP_INTERVAL_HOURS = 24

# A copy of every snapshot is written here as well as to the database.
#
# The row is the primary store, but a copy that lives inside the database shares the
# database's fate: lose the database and the backups go with it. A file on disk
# survives that, which is the entire point of having a backup.
BACKUP_DIR = os.getenv("BACKUP_DIR", "/workspace/backups")
# Files kept on disk, independent of how many rows rotate out.
KEEP_BACKUP_FILES = int(os.getenv("KEEP_BACKUP_FILES", "30"))


def _safe_name(value):
    """A label goes into a filename, so keep it to characters a shell will not fight."""
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(value or "backup"))[:40]


def write_to_disk(blob, label="auto", checksum=""):
    """Mirror a snapshot onto the server's disk. Returns the path, or "" on failure.

    Written to a temp file and then renamed, so a crash part-way through cannot leave a
    truncated file that looks like a real backup. Never raises: the row is already
    committed by the time this runs, and a full disk must not fail a backup that
    otherwise succeeded.
    """
    try:
        directory = pathlib.Path(BACKUP_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = directory / f"{stamp}-{_safe_name(label)}.json"
        temp = directory / f".{target.name}.part"
        temp.write_bytes(blob.encode("utf-8"))
        os.replace(temp, target)
        _rotate_files(directory)
        return str(target)
    except Exception:  # noqa: BLE001 - a backup must not fail because a disk is full
        return ""


def _rotate_files(directory):
    """Keep the newest KEEP_BACKUP_FILES on disk, dropping the rest."""
    try:
        files = sorted(
            (p for p in directory.iterdir() if p.suffix == ".json" and p.is_file()),
            key=lambda p: p.name,
        )
        for old in files[:-KEEP_BACKUP_FILES] if KEEP_BACKUP_FILES > 0 else []:
            try:
                old.unlink()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def backup_files():
    """The snapshots currently on disk, newest first. For the UI and for operators."""
    try:
        directory = pathlib.Path(BACKUP_DIR)
        if not directory.is_dir():
            return []
        out = []
        for p in sorted(directory.iterdir(), key=lambda p: p.name, reverse=True):
            if p.suffix == ".json" and p.is_file():
                try:
                    size = p.stat().st_size
                except Exception:  # noqa: BLE001
                    size = 0
                out.append({"name": p.name, "size_bytes": size, "path": str(p)})
        return out
    except Exception:  # noqa: BLE001
        return []


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def purge_index(db):
    """Everything the operator has permanently removed, as {kind: {identifier}}."""
    index = {"contact": set(), "audience": set(), "campaign": set(), "sequence": set()}
    for row in db.query(models.Purge).all():
        index.setdefault(row.kind, set()).add(str(row.identifier).strip().lower())
    return index


def is_purged(index, kind, identifier):
    return str(identifier or "").strip().lower() in index.get(kind, set())


def record_purge(db, kind, identifier, reason="permanently removed"):
    """Write a tombstone. Idempotent - the same identifier is never recorded twice."""
    ident = str(identifier or "").strip()
    if not ident:
        return None
    found = (db.query(models.Purge)
             .filter(models.Purge.kind == kind,
                     func.lower(models.Purge.identifier) == ident.lower()).first())
    if found:
        return found
    row = models.Purge(kind=kind, identifier=ident, reason=reason)
    db.add(row)
    db.flush()
    return row


def forget_purge_for(db, kind, identifier):
    """Release a tombstone for one identifier.

    Used when the operator explicitly adds something back by hand: that is a new,
    deliberate decision, so the address becomes an ordinary contact again instead
    of being permanently skipped by every future restore.
    """
    ident = str(identifier or "").strip().lower()
    if not ident:
        return 0
    rows = (db.query(models.Purge)
            .filter(models.Purge.kind == kind,
                    func.lower(models.Purge.identifier) == ident).all())
    for row in rows:
        db.delete(row)
    return len(rows)


def snapshot(db, include_trashed=True):
    """Build the full JSON payload. Mirrors the manual export exactly."""
    def rows(model):
        q = db.query(model)
        if include_trashed and hasattr(model, "deleted_at"):
            q = q.execution_options(include_trashed=True)
        return q.all()

    def iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else (value or "")

    return {
        "taken_at": _stamp(),
        "schema_version": 1,
        "audiences": [
            {"id": a.id, "name": a.name, "description": a.description,
             "deleted_at": a.deleted_at or ""}
            for a in rows(models.Audience)
        ],
        "contacts": [
            {"id": c.id, "email": c.email, "name": c.name, "tags": list(c.tags or []),
             "status": c.status, "source": c.source, "email_status": c.email_status,
             "email_reason": c.email_reason, "deleted_at": c.deleted_at or ""}
            for c in rows(models.Contact)
        ],
        "audience_members": [
            {"audience_id": m.audience_id, "contact_id": m.contact_id,
             "tags": list(m.tags or [])}
            for m in db.query(models.AudienceMember).all()
        ],
        "suppressions": [
            {"email": s.email, "reason": s.reason, "created_at": iso(s.created_at)}
            for s in db.query(models.Suppression).all()
        ],
        "campaigns": [
            {"slug": c.slug, "title": c.title, "status": c.status, "product": c.product,
             "deleted_at": c.deleted_at or ""}
            for c in rows(models.Campaign)
        ],
        "sequences": [
            {"slug": s.slug, "name": s.name, "deleted_at": s.deleted_at or ""}
            for s in rows(models.Sequence)
        ],
        "sequence_enrollments": [
            {"campaign_slug": e.campaign_slug, "email": e.email, "current_ref": e.current_ref,
             "branch": e.branch, "status": e.status, "sends": e.sends}
            for e in db.query(models.SequenceEnrollment).all()
        ],
        # The permanent-removal ledger travels with the snapshot, so a restore knows
        # what must never come back.
        "purges": [
            {"kind": p.kind, "identifier": p.identifier, "reason": p.reason}
            for p in db.query(models.Purge).all()
        ],
    }


def summarise(payload):
    """Small counts stored alongside the snapshot, for the backup list."""
    return {
        "audiences": len(payload.get("audiences") or []),
        "contacts": len(payload.get("contacts") or []),
        "tags": len({t for c in (payload.get("contacts") or []) for t in (c.get("tags") or [])}),
        "members": len(payload.get("audience_members") or []),
        "campaigns": len(payload.get("campaigns") or []),
        "suppressions": len(payload.get("suppressions") or []),
        "enrollments": len(payload.get("sequence_enrollments") or []),
    }


def create_backup(db, label="auto"):
    """Take a snapshot and store it in the database and on disk. Returns the Backup row."""
    import hashlib

    payload = snapshot(db)
    blob = _json.dumps(payload, sort_keys=True, default=str)
    encoded = blob.encode("utf-8")
    checksum = hashlib.sha256(encoded).hexdigest()[:32]

    # Write the file first. If the disk is unavailable the row is still created below, so
    # the backup is never lost to a filesystem problem - the two stores are independent.
    path = write_to_disk(blob, label=label, checksum=checksum)

    row = models.Backup(
        label=label,
        size_bytes=len(encoded),
        checksum=checksum,
        counts_json=summarise(payload),
        payload_json=payload,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if path:
        row.file_path = path
        db.commit()
        db.refresh(row)
    return row


def last_backup(db):
    return db.query(models.Backup).order_by(models.Backup.id.desc()).first()


def backup_due(db, hours=BACKUP_INTERVAL_HOURS):
    """True when there is no snapshot yet, or the newest one is older than `hours`."""
    row = last_backup(db)
    if not row:
        return True
    when = row.created_at
    if when is None:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when >= timedelta(hours=hours)


def rotate(db, keep=KEEP_BACKUPS):
    """Drop the oldest snapshots past the retention window.

    Only backup ROWS rotate - the live data is never touched. The newest snapshot
    is always kept, whatever this does.
    """
    rows = db.query(models.Backup).order_by(models.Backup.id.desc()).all()
    removed = 0
    for row in rows[keep:]:
        db.delete(row)
        removed += 1
    if removed:
        db.commit()
    return removed


def run_due_backup(db, label="auto"):
    """Take a snapshot when one is due. Safe to call from any tick - it no-ops."""
    if not backup_due(db):
        return None
    row = create_backup(db, label=label)
    rotate(db)
    return row


def restore(db, payload):
    """Merge a snapshot back in. Additive only - nothing is ever removed.

    What it does:
      * re-creates audiences that no longer exist, and un-trashes ones that were trashed
      * re-creates contacts that no longer exist, and un-trashes trashed ones
      * merges tags back onto contacts, preserving any added since the snapshot
      * re-links audience membership from the snapshot
      * re-creates suppressions that are missing (a suppression is never removed)

    What it will NOT do:
      * delete a contact, audience, campaign or suppression that exists now but not
        in the snapshot
      * remove a tag that was added after the snapshot
    """
    from .audience_routes import normalise_tags

    stats = {"audiences_added": 0, "audiences_restored": 0,
             "contacts_added": 0, "contacts_restored": 0,
             "tags_merged": 0, "members_relinked": 0, "suppressions_added": 0,
             "skipped_permanently_removed": 0}

    # Fold the snapshot's own ledger in FIRST, so restoring an older snapshot can
    # never forget a permanent removal that happened after it was taken.
    for p in payload.get("purges") or []:
        record_purge(db, p.get("kind") or "", p.get("identifier") or "",
                     p.get("reason") or "from backup")
    db.flush()

    # The list of things that must NOT come back. Checked before everything else.
    purged = purge_index(db)

    # --- audiences -------------------------------------------------------
    by_id = {}
    existing = {a.name: a for a in
                db.query(models.Audience).execution_options(include_trashed=True).all()}
    for a in payload.get("audiences") or []:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        if is_purged(purged, "audience", name):
            stats["skipped_permanently_removed"] += 1
            continue
        was_live = not (a.get("deleted_at") or "")
        row = existing.get(name)
        if row:
            # Only un-trash what the snapshot actually held as live. An item the
            # snapshot itself recorded in Trash stays in Trash - a restore must not
            # resurrect something that was already deleted at snapshot time.
            if row.deleted_at and was_live:
                row.deleted_at = ""
                stats["audiences_restored"] += 1
        else:
            row = models.Audience(name=name, description=a.get("description") or "")
            db.add(row)
            db.flush()
            existing[name] = row
            stats["audiences_added"] += 1
        by_id[a.get("id")] = row

    # --- contacts --------------------------------------------------------
    db.flush()
    c_by_id = {}
    for c in payload.get("contacts") or []:
        email = (c.get("email") or "").strip().lower()
        if not email:
            continue
        # A permanently removed address never returns, whatever the snapshot says.
        if is_purged(purged, "contact", email):
            stats["skipped_permanently_removed"] += 1
            continue
        row = (db.query(models.Contact).filter(models.Contact.email == email)
               .execution_options(include_trashed=True).first())
        was_live = not (c.get("deleted_at") or "")
        if row is None:
            row = models.Contact(email=email, name=c.get("name") or "",
                                 status=c.get("status") or "subscribed",
                                 source=c.get("source") or "backup")
            db.add(row)
            db.flush()
            stats["contacts_added"] += 1
        else:
            # Same rule as audiences: only un-trash what the snapshot held as live.
            if row.deleted_at and was_live:
                row.deleted_at = ""
                stats["contacts_restored"] += 1
        # Tags are NOT restored here - they are audience-scoped and are put back on
        # the membership below, so they land in the right list and nowhere else.
        c_by_id[c.get("id")] = row

    # --- membership (and its audience-scoped tags) -----------------------
    db.flush()
    for m in payload.get("audience_members") or []:
        aud = by_id.get(m.get("audience_id"))
        con = c_by_id.get(m.get("contact_id"))
        if not aud or not con:
            continue
        snap_tags = normalise_tags(m.get("tags") or [])
        link = (db.query(models.AudienceMember)
                .filter(models.AudienceMember.audience_id == aud.id,
                        models.AudienceMember.contact_id == con.id).first())
        if not link:
            db.add(models.AudienceMember(audience_id=aud.id, contact_id=con.id, tags=snap_tags))
            stats["members_relinked"] += 1
        elif snap_tags:
            # Union the snapshot's tags onto the membership - tags added since the
            # snapshot are never dropped.
            merged = normalise_tags(list(link.tags or []) + snap_tags)
            if set(merged) != set(normalise_tags(link.tags)):
                link.tags = merged
                stats["tags_merged"] += 1

    # --- suppressions (added back, never removed) ------------------------
    for s in payload.get("suppressions") or []:
        email = (s.get("email") or "").strip().lower()
        if not email:
            continue
        found = db.query(models.Suppression).filter(models.Suppression.email == email).first()
        if not found:
            db.add(models.Suppression(email=email,
                                      reason=s.get("reason") or "restored from backup",
                                      source="restore"))
            stats["suppressions_added"] += 1

    db.commit()
    return stats
