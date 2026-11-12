"""Saved audiences, contacts and tags.

SendClean has no audience, contact or tag API, so Sendwise owns this data itself.
An audience is a named, reusable list of contacts. Contacts carry free-form tags,
and a tag acts as a filter so one audience can serve several segments.

Suppression always wins: an address on the global suppression list (unsubscribe,
hard bounce, complaint) is never returned as a recipient, whatever its tags say.
"""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models
from . import sequence_engine as seq
from .auth import require_user
from .db import get_db, stamp

router = APIRouter(prefix="/api")

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _clean(value):
    return str(value or "").strip().lower()


def normalise_tags(tags):
    out = []
    for t in (tags or []):
        t = str(t or "").strip().lower()
        if t and t not in out:
            out.append(t)
    return out


def parse_tag_param(tags):
    """Accept ['a','b'] or 'a,b' or 'a, b' and return a normalised list.

    Splitting is comma-only. A tag name may legitimately contain spaces
    ("real audiences") and the UI joins its array with "," - splitting on
    whitespace would shatter such a tag into fragments that match nobody.
    """
    if tags is None:
        return []
    if isinstance(tags, (list, tuple, set)):
        return normalise_tags(tags)
    return normalise_tags([t for t in re.split(r"\s*,\s*", str(tags)) if t])


def _tag_matches(have, want, mode):
    """`want` is the selected-tag list; `have` is the contact's own tags."""
    if not want:
        return True
    if str(mode or "any").lower() == "all":
        return all(t in have for t in want)
    return any(t in have for t in want)


def upsert_contact(db, email, name="", tags=None, source="manual"):
    """Create or update one contact. Returns the Contact row."""
    e = _clean(email)
    if not EMAIL_RE.match(e):
        raise ValueError(f"invalid email: {email or '(blank)'}")
    row = (db.query(models.Contact)
           .filter(models.Contact.email == e)
           .execution_options(include_trashed=True).first())
    if row is None:
        row = models.Contact(email=e, name=(name or "").strip(), tags=normalise_tags(tags), source=source)
        db.add(row)
        db.flush()
    else:
        # Re-adding a trashed address revives the original row rather than creating
        # a duplicate - its tags, lists and validation history all come back.
        if row.deleted_at:
            row.deleted_at = ""
        if name:
            row.name = name.strip()
        if tags:
            merged = normalise_tags(list(row.tags or []) + list(tags))
            row.tags = merged
        if source and not row.source:
            row.source = source

    # Adding an address by hand is a new, deliberate decision - release any tombstone
    # so it behaves like a normal contact again instead of being skipped by restores.
    try:
        from . import backup as _bk
        _bk.forget_purge_for(db, "contact", e)
    except Exception:  # noqa: BLE001 - never block an add on ledger housekeeping
        pass
    return row


def _suppressed_email_set(db):
    """Load the small global suppression ledger once for bulk audience checks.

    Calling ``seq.is_suppressed`` inside a member loop turns a 9,000-contact
    audience into 9,000 extra SQL round trips.  Audience summaries and previews
    only need membership in the ledger, so one compact query is equivalent and
    dramatically cheaper.
    """
    return {
        (email or "").strip().lower()
        for (email,) in db.query(models.Suppression.email).all()
        if (email or "").strip()
    }


def member_emails(db, audience_id, tags="", tag_match="any", include_unsubscribed=False,
                  suppressed=None):
    """Recipient emails for an audience, optionally filtered by its own tags.

    Filtering uses the MEMBERSHIP tags, not the contact's, so a tag that belongs to
    another audience can never affect this one.
    """
    q = (db.query(models.Contact, models.AudienceMember)
         .join(models.AudienceMember, models.AudienceMember.contact_id == models.Contact.id)
         .filter(models.AudienceMember.audience_id == int(audience_id))
         .filter(models.Contact.deleted_at == ""))
    rows = q.all()
    want = parse_tag_param(tags)
    out = []
    if not include_unsubscribed and suppressed is None:
        suppressed = _suppressed_email_set(db)
    suppressed = suppressed or set()
    for c, m in rows:
        if not _tag_matches(normalise_tags(m.tags), want, tag_match):
            continue
        if not include_unsubscribed:
            if c.status != "subscribed":
                continue
            if (c.email or "").strip().lower() in suppressed:
                continue
            # A syntax-invalid or MX-less address can only ever bounce, so it never
            # reaches a send. `unknown` is NOT dropped - a DNS blip must not lose it.
            if (c.email_status or "") in ("invalid", "no_mx"):
                continue
        out.append(c.email)
    return sorted(set(out))


def register_tag_names(db, audience_id):
    """Every tag DEFINED for this audience (the registry names)."""
    rows = (db.query(models.AudienceTag)
            .filter(models.AudienceTag.audience_id == int(audience_id)).all())
    return normalise_tags([r.name for r in rows])


def register_tag(db, audience_id, name):
    """Add a tag to this audience's registry. Idempotent."""
    n = _clean(name)
    if not n:
        return None
    found = (db.query(models.AudienceTag)
             .filter(models.AudienceTag.audience_id == int(audience_id),
                     func.lower(models.AudienceTag.name) == n).first())
    if found:
        return found
    row = models.AudienceTag(audience_id=int(audience_id), name=n)
    db.add(row)
    db.flush()
    return row


def audience_tag_inventory(db, audience_id):
    """Every tag DEFINED for this audience, with how many of its contacts use each.

    Built from the registry, so a tag with 0 contacts still appears - that is exactly
    what makes "create a tag now, delete it later" possible.
    """
    aid = int(audience_id)
    counts = {t: 0 for t in register_tag_names(db, aid)}
    for m in db.query(models.AudienceMember).filter(models.AudienceMember.audience_id == aid).all():
        for t in normalise_tags(m.tags):
            counts[t] = counts.get(t, 0) + 1
    return counts


def _audience_or_404(db, audience_id, include_trashed=False):
    q = db.query(models.Audience).filter(models.Audience.id == int(audience_id))
    if include_trashed:
        q = q.execution_options(include_trashed=True)
    row = q.first()
    if not row:
        raise HTTPException(status_code=404, detail="Audience not found")
    return row


def _live_contacts(db, ids=None):
    """Contacts that are not in Trash. Trashed rows never appear in a list or a send."""
    q = db.query(models.Contact).filter(models.Contact.deleted_at == "")
    if ids is not None:
        if not ids:
            return []
        q = q.filter(models.Contact.id.in_(ids))
    return q.all()


def _contact_dict(c):
    return {"id": c.id, "email": c.email, "name": c.name, "tags": normalise_tags(c.tags),
            "status": c.status, "source": c.source,
            "email_status": c.email_status or "unverified",
            "email_reason": c.email_reason or "",
            "email_checked_at": c.email_checked_at or "",
            "created_at": c.created_at.isoformat() if c.created_at else ""}


def _member_dict(contact, membership):
    """A contact as it appears inside an audience - with THAT audience's tags."""
    d = _contact_dict(contact)
    d["tags"] = normalise_tags(membership.tags if membership else [])
    return d


# ------------------------------------------------------------------ audiences
class AudienceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""


class AudiencePatch(BaseModel):
    name: str | None = None
    description: str | None = None


class ContactsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emails: list = []
    text: str = ""            # free-form paste: one email per line, "Name <email>" allowed
    tags: list = []
    source: str = "manual"


class ContactIn(BaseModel):
    email: str
    name: str = ""
    tags: list = []


class ContactPatch(BaseModel):
    name: str | None = None
    tags: list | None = None
    status: str | None = None


def _parse_pasted(text, emails):
    """Accept a list, a textarea, or both. Supports 'Name <email>'."""
    out = []
    for raw in (emails or []):
        e = _clean(raw)
        if e:
            out.append((e, ""))
    for line in str(text or "").replace(",", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(.*)<([^<>]+)>\s*$", line)
        if m:
            out.append((_clean(m.group(2)), m.group(1).strip().strip('"').strip("'")))
        else:
            out.append((_clean(line), ""))
    return out


@router.get("/audiences", dependencies=[Depends(require_user)])
def list_audiences(db: Session = Depends(get_db)):
    out = []
    suppressed = _suppressed_email_set(db)
    for a in db.query(models.Audience).filter(models.Audience.deleted_at == "").order_by(models.Audience.name).all():
        members = db.query(models.AudienceMember).filter(models.AudienceMember.audience_id == a.id).all()
        ids = [m.contact_id for m in members]
        contacts = _live_contacts(db, ids)
        # Tags come from this audience's own memberships - never from another list.
        tags = audience_tag_inventory(db, a.id)
        usable = len(member_emails(db, a.id, suppressed=suppressed))
        bad = sum(1 for c in contacts if (c.email_status or "") in ("invalid", "no_mx"))
        risky = sum(1 for c in contacts if (c.email_status or "") in ("disposable", "role"))
        unchecked = sum(1 for c in contacts if (c.email_status or "unverified") in ("unverified", "unknown"))
        out.append({
            "id": a.id, "name": a.name, "description": a.description,
            "members": len(contacts), "usable": usable, "tags": tags,
            "blocked": bad, "risky": risky, "unchecked": unchecked,
            "created_at": a.created_at.isoformat() if a.created_at else "",
        })
    return out


@router.post("/audiences", dependencies=[Depends(require_user)])
def create_audience(body: AudienceIn, db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Audience name is required")
    live = db.query(models.Audience).filter(models.Audience.name == name,
                                            models.Audience.deleted_at == "").first()
    if live:
        raise HTTPException(status_code=400, detail="An audience with that name already exists")
    # A trashed list with this name is revived rather than duplicated.
    trashed = (db.query(models.Audience)
               .filter(models.Audience.name == name)
               .execution_options(include_trashed=True).first())
    if trashed:
        trashed.deleted_at = ""
        trashed.description = (body.description or "").strip() or trashed.description
        db.commit()
        db.refresh(trashed)
        members = db.query(models.AudienceMember).filter(
            models.AudienceMember.audience_id == trashed.id).count()
        return {"id": trashed.id, "name": trashed.name, "description": trashed.description,
                "members": members, "usable": len(member_emails(db, trashed.id)),
                "tags": {}, "restored_from_trash": True}

    a = models.Audience(name=name, description=(body.description or "").strip())
    db.add(a)
    db.commit()
    db.refresh(a)
    return {"id": a.id, "name": a.name, "description": a.description, "members": 0, "usable": 0, "tags": {}}


@router.patch("/audiences/{audience_id}", dependencies=[Depends(require_user)])
def update_audience(audience_id: int, body: AudiencePatch, db: Session = Depends(get_db)):
    """Rename an audience, or change its description.

    The audience keeps its id and its memberships, so renaming never disturbs the
    contacts in it, its tags, or any send that used it.
    """
    a = _audience_or_404(db, audience_id)
    old_name = a.name

    if body.name is not None:
        new_name = (body.name or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Audience name cannot be empty")
        if len(new_name) > 120:
            raise HTTPException(status_code=400, detail="Audience name is too long (120 characters max)")
        clash = (db.query(models.Audience)
                 .filter(models.Audience.deleted_at == "",
                         func.lower(models.Audience.name) == new_name.lower(),
                         models.Audience.id != a.id).first())
        if clash:
            raise HTTPException(status_code=400,
                                detail=f"Another audience is already called “{new_name}”. Pick a different name.")
        a.name = new_name

    if body.description is not None:
        a.description = (body.description or "").strip()

    if a.name != old_name:
        db.add(models.Job(job_type="sync", title=f"Audience renamed · {old_name} → {a.name}",
                          detail="members, tags and sends are unaffected",
                          status="done", created_at=stamp()))
    db.commit()
    db.refresh(a)
    return {"ok": True, "id": a.id, "name": a.name, "description": a.description,
            "renamed_from": old_name if a.name != old_name else ""}


@router.delete("/audiences/{audience_id}", dependencies=[Depends(require_user)])
def delete_audience(audience_id: int, db: Session = Depends(get_db)):
    """Move an audience to Trash. Nothing is erased - it can be restored exactly.

    Membership rows are deliberately kept, so restoring the list brings back the
    same contacts it had, with no re-import needed.
    """
    a = _audience_or_404(db, audience_id)
    a.deleted_at = _stamp()
    members = db.query(models.AudienceMember).filter(models.AudienceMember.audience_id == a.id).count()
    db.add(models.Job(job_type="warning", title=f"Audience moved to Trash · {a.name}",
                      detail=f"{members} membership(s) kept · restorable from Trash",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "trashed": a.name, "restorable": True, "members_kept": members}


@router.post("/audiences/{audience_id}/restore", dependencies=[Depends(require_user)])
def restore_audience(audience_id: int, db: Session = Depends(get_db)):
    """Bring an audience back out of Trash, with its membership intact."""
    a = _audience_or_404(db, audience_id, include_trashed=True)
    a.deleted_at = ""
    members = db.query(models.AudienceMember).filter(models.AudienceMember.audience_id == a.id).count()
    db.commit()
    return {"ok": True, "restored": a.name, "members": members}


@router.get("/audiences/{audience_id}/tags", dependencies=[Depends(require_user)])
def audience_tags(audience_id: int, db: Session = Depends(get_db)):
    """The tags that exist in THIS audience. Nothing from any other list appears."""
    a = _audience_or_404(db, audience_id)
    counts = audience_tag_inventory(db, a.id)
    return sorted([{"tag": k, "contacts": v} for k, v in counts.items()],
                  key=lambda x: (-x["contacts"], x["tag"]))


class TagIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str


@router.post("/audiences/{audience_id}/tags/create", dependencies=[Depends(require_user)])
def create_audience_tag(audience_id: int, body: TagIn, db: Session = Depends(get_db)):
    """Define a tag for this audience, even before anything uses it."""
    a = _audience_or_404(db, audience_id)
    name = _clean(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="Tag name is required")
    existing = (db.query(models.AudienceTag)
                .filter(models.AudienceTag.audience_id == a.id,
                        func.lower(models.AudienceTag.name) == name).first())
    if existing:
        raise HTTPException(status_code=400,
                            detail=f"“{name}” already exists in {a.name}")
    db.add(models.AudienceTag(audience_id=a.id, name=name))
    db.add(models.Job(job_type="sync", title=f"Tag created · {name}",
                      detail=f"in {a.name}", status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "audience": a.name, "tag": name, "tags": audience_tag_inventory(db, a.id)}


@router.delete("/audiences/{audience_id}/tags/{tag}", dependencies=[Depends(require_user)])
def delete_audience_tag(audience_id: int, tag: str, db: Session = Depends(get_db)):
    """Delete a tag from this audience.

    REFUSED while any contact in the audience still carries it. A tag that is in use
    is removed from the contacts first; only then can the tag itself be deleted, so a
    live tag can never disappear out from under the list.
    """
    a = _audience_or_404(db, audience_id)
    name = _clean(tag)

    holders = []
    for m in db.query(models.AudienceMember).filter(models.AudienceMember.audience_id == a.id).all():
        if name in normalise_tags(m.tags):
            holders.append(m.contact_id)

    if holders:
        contacts = db.query(models.Contact).filter(models.Contact.id.in_(holders)).all()
        sample = ", ".join(c.email for c in contacts[:3])
        if len(contacts) > 3:
            sample += f" and {len(contacts) - 3} more"
        raise HTTPException(
            status_code=409,
            detail=(f"“{name}” is still on {len(contacts)} contact(s) in {a.name} "
                    f"({sample}). Remove the tag from those contacts first - then you can "
                    f"remove the tag itself."),
        )

    row = (db.query(models.AudienceTag)
           .filter(models.AudienceTag.audience_id == a.id,
                   func.lower(models.AudienceTag.name) == name).first())
    if row:
        db.delete(row)
    db.add(models.Job(job_type="sync", title=f"Tag removed · {name}",
                      detail=f"from {a.name} (was unused)", status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "removed": name, "tags": audience_tag_inventory(db, a.id)}


@router.get("/audiences/{audience_id}/contacts", dependencies=[Depends(require_user)])
def audience_contacts(audience_id: int, tags: str = "", tag_match: str = "any", q: str = "",
                      db: Session = Depends(get_db)):
    a = _audience_or_404(db, audience_id)
    pairs = (db.query(models.Contact, models.AudienceMember)
             .join(models.AudienceMember, models.AudienceMember.contact_id == models.Contact.id)
             .filter(models.AudienceMember.audience_id == a.id)
             .filter(models.Contact.deleted_at == "")
             .all())
    pairs = sorted(pairs, key=lambda p: p[0].email)

    want = parse_tag_param(tags)
    if want:
        pairs = [(c, m) for c, m in pairs if _tag_matches(normalise_tags(m.tags), want, tag_match)]

    # Search inside the list. An audience can hold thousands of addresses, so looking one
    # person up by email should not mean scrolling or exporting the list. Matches the
    # address or the stored name, case-insensitively, and runs before the delivery
    # checks below so the counts still describe exactly the rows returned.
    needle = (q or "").strip().lower()
    if needle:
        pairs = [(c, m) for c, m in pairs
                 if needle in (c.email or "").lower() or needle in (c.name or "").lower()]

    # Mark who will actually receive a send. The campaign resolves recipients
    # through `member_emails`, which drops unsubscribed, suppressed and
    # un-deliverable addresses - so the preview must reflect exactly that, or it
    # would promise delivery to someone the send will skip.
    out_rows = []
    receiving = 0
    suppressed = _suppressed_email_set(db)
    for c, m in pairs:
        d = _member_dict(c, m)
        why = ""
        if c.status != "subscribed":
            why = f"contact status is '{c.status}'"
        elif (c.email or "").strip().lower() in suppressed:
            why = "on the suppression list"
        elif (c.email_status or "") in ("invalid", "no_mx"):
            why = c.email_reason or "address cannot receive mail"
        d["will_receive"] = not why
        d["excluded_reason"] = why
        if not why:
            receiving += 1
        out_rows.append(d)

    return {"audience": {"id": a.id, "name": a.name}, "count": len(out_rows),
            "will_receive": receiving, "excluded": len(out_rows) - receiving,
            "tags": audience_tag_inventory(db, a.id),
            "tags_applied": want, "tag_match": str(tag_match or "any").lower(),
            "query": needle,
            "contacts": out_rows}


@router.post("/audiences/{audience_id}/contacts", dependencies=[Depends(require_user)])
def add_audience_contacts(audience_id: int, body: ContactsIn, db: Session = Depends(get_db)):
    a = _audience_or_404(db, audience_id)
    pairs = _parse_pasted(body.text, body.emails)
    if not pairs:
        raise HTTPException(status_code=400, detail="No contacts supplied")

    existing_ids = {m.contact_id for m in
                    db.query(models.AudienceMember).filter(models.AudienceMember.audience_id == a.id).all()}
    added, updated, skipped, bad = 0, 0, 0, []
    for email, name in pairs:
        if not EMAIL_RE.match(email):
            bad.append(email or "(blank)")
            continue
        before = db.query(models.Contact).filter(models.Contact.email == email).first()
        # Tags are NOT written on the contact - they belong to this audience only.
        contact = upsert_contact(db, email, name=name, tags=[], source=body.source or "manual")
        if before is None:
            added += 1
        else:
            updated += 1
        if contact.id not in existing_ids:
            db.add(models.AudienceMember(audience_id=a.id, contact_id=contact.id,
                                         tags=normalise_tags(body.tags)))
            existing_ids.add(contact.id)
        else:
            # Already in this list - merge the tags onto its membership instead.
            mem = (db.query(models.AudienceMember)
                   .filter(models.AudienceMember.audience_id == a.id,
                           models.AudienceMember.contact_id == contact.id).first())
            if mem and body.tags:
                mem.tags = normalise_tags(list(mem.tags or []) + list(body.tags))
            skipped += 1

    # Verify what we just accepted, so the list shows a real deliverability state
    # instead of a hopeful one. DNS lookups are cached per domain.
    from . import email_validator as ev
    good = [e for e, _ in pairs if EMAIL_RE.match(e)]
    fresh = (db.query(models.Contact).filter(models.Contact.email.in_(good)).all() if good else [])
    verification = ev.summarise([])
    if fresh:
        stamp = _stamp()
        results = ev.validate_many([c.email for c in fresh])
        by_email = {r["email"]: r for r in results}
        for c in fresh:
            if c.email in by_email:
                _apply_verdict(c, by_email[c.email], stamp)
        verification = ev.summarise(results)

    db.commit()
    return {"ok": True, "audience": a.name, "added": added, "updated": updated,
            "already_members": skipped, "invalid": bad, "verification": verification,
            "usable": len(member_emails(db, a.id))}


@router.post("/audiences/{audience_id}/contacts/remove", dependencies=[Depends(require_user)])
def remove_audience_contacts(audience_id: int, body: ContactsIn, db: Session = Depends(get_db)):
    a = _audience_or_404(db, audience_id)
    wanted = {e for e, _ in _parse_pasted(body.text, body.emails)}
    if not wanted:
        raise HTTPException(status_code=400, detail="No contacts supplied")
    ids = [c.id for c in db.query(models.Contact).filter(models.Contact.email.in_(list(wanted))).all()]
    removed = 0
    if ids:
        removed = (db.query(models.AudienceMember)
                   .filter(models.AudienceMember.audience_id == a.id,
                           models.AudienceMember.contact_id.in_(ids))
                   .delete(synchronize_session=False))
    db.commit()
    return {"ok": True, "removed": removed, "usable": len(member_emails(db, a.id))}


# ------------------------------------------------------------------- contacts
@router.get("/contacts", dependencies=[Depends(require_user)])
def list_contacts(tags: str = "", tag_match: str = "any", q: str = "", limit: int = 2000, db: Session = Depends(get_db)):
    rows = (db.query(models.Contact).filter(models.Contact.deleted_at == "")
            .order_by(models.Contact.email).limit(max(1, min(limit, 10000))).all())
    want = parse_tag_param(tags)
    needle = _clean(q)
    if want:
        rows = [c for c in rows if _tag_matches(normalise_tags(c.tags), want, tag_match)]
    if needle:
        rows = [c for c in rows if needle in c.email or needle in (c.name or "").lower()]
    return {"count": len(rows), "tags_applied": want, "tag_match": str(tag_match or "any").lower(),
            "contacts": [_contact_dict(c) for c in rows]}


@router.get("/tags", dependencies=[Depends(require_user)])
def list_tags(db: Session = Depends(get_db)):
    counts = {}
    for c in db.query(models.Contact).filter(models.Contact.deleted_at == "").all():
        for t in normalise_tags(c.tags):
            counts[t] = counts.get(t, 0) + 1
    return sorted([{"tag": k, "contacts": v} for k, v in counts.items()],
                  key=lambda x: -x["contacts"])


@router.post("/contacts", dependencies=[Depends(require_user)])
def create_contact(body: ContactIn, db: Session = Depends(get_db)):
    try:
        c = upsert_contact(db, body.email, name=body.name, tags=body.tags, source="manual")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(c)
    return _contact_dict(c)


@router.patch("/contacts/{email}", dependencies=[Depends(require_user)])
def update_contact(email: str, body: ContactPatch, db: Session = Depends(get_db)):
    c = db.query(models.Contact).filter(models.Contact.email == _clean(email)).first()
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found")
    if body.name is not None:
        c.name = body.name.strip()
    if body.tags is not None:
        c.tags = normalise_tags(body.tags)
    if body.status in ("subscribed", "unsubscribed", "suppressed"):
        c.status = body.status
    db.commit()
    db.refresh(c)
    return _contact_dict(c)


@router.delete("/contacts/{email}", dependencies=[Depends(require_user)])
def delete_contact(email: str, db: Session = Depends(get_db)):
    """Move a contact to Trash. Its tags and every list membership are kept."""
    c = db.query(models.Contact).filter(models.Contact.email == _clean(email)).first()
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found")
    c.deleted_at = _stamp()
    db.add(models.Job(job_type="warning", title=f"Contact moved to Trash · {c.email}",
                      detail="tags and list memberships kept · restorable any time",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "trashed": c.email, "restorable": True}


@router.post("/contacts/{email}/restore", dependencies=[Depends(require_user)])
def restore_contact(email: str, db: Session = Depends(get_db)):
    """Bring a contact back out of Trash, with its tags and lists intact."""
    c = (db.query(models.Contact).filter(models.Contact.email == _clean(email))
         .execution_options(include_trashed=True).first())
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found")
    c.deleted_at = ""
    db.commit()
    return _contact_dict(c)


@router.get("/trash", dependencies=[Depends(require_user)])
def list_trash(db: Session = Depends(get_db)):
    """Everything currently in Trash. Nothing here is lost - all of it restores."""
    def trashed(model):
        return (db.query(model).filter(model.deleted_at != "")
                .execution_options(include_trashed=True).all())

    aud = trashed(models.Audience)
    con = trashed(models.Contact)
    cam = trashed(models.Campaign)
    seq = trashed(models.Sequence)

    def counter(audience_id):
        return db.query(models.AudienceMember).filter(
            models.AudienceMember.audience_id == audience_id).count()

    return {
        "audiences": [{"id": a.id, "name": a.name, "deleted_at": a.deleted_at,
                       "members": counter(a.id)} for a in aud],
        "contacts": [{"email": c.email, "name": c.name, "tags": normalise_tags(c.tags),
                      "deleted_at": c.deleted_at} for c in con],
        "campaigns": [{"slug": c.slug, "title": c.title, "deleted_at": c.deleted_at} for c in cam],
        "sequences": [{"slug": s.slug, "name": s.name, "deleted_at": s.deleted_at} for s in seq],
        "total": len(aud) + len(con) + len(cam) + len(seq),
    }


@router.get("/backup/counts", dependencies=[Depends(require_user)])
def backup_counts(db: Session = Depends(get_db)):
    """Totals for the backup panel - counts INCLUDE trashed rows, so the numbers
    add up to everything the database is holding on to."""
    def all_rows(model):
        return db.query(model).execution_options(include_trashed=True).all()

    contacts = all_rows(models.Contact)
    tags = {}
    for c in contacts:
        for t in normalise_tags(c.tags):
            tags[t] = tags.get(t, 0) + 1
    return {
        "audiences": len(all_rows(models.Audience)),
        "audiences_trashed": sum(1 for a in all_rows(models.Audience) if a.deleted_at),
        "contacts": len(contacts),
        "contacts_trashed": sum(1 for c in contacts if c.deleted_at),
        "tags": len(tags),
        "campaigns": len(all_rows(models.Campaign)),
        "sequences": len(all_rows(models.Sequence)),
        "suppressions": len(db.query(models.Suppression).all()),
        "enrollments": len(db.query(models.SequenceEnrollment).all()),
    }


@router.get("/backup/export", dependencies=[Depends(require_user)])
def backup_export(db: Session = Depends(get_db)):
    """A complete JSON snapshot of everything Sendwise owns.

    Includes trashed rows so the export is a true picture of the database, not just
    the live view. Keep a copy of this offline: it is the last line of defence.
    """
    def all_rows(model):
        return db.query(model).execution_options(include_trashed=True).all()

    def iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else (value or "")

    return {
        "exported_at": _stamp(),
        "schema_version": 1,
        "note": "Complete Sendwise data export - audiences, contacts, tags, suppressions, "
                "sequence enrollments and sends. Trashed rows are included.",
        "audiences": [
            {"id": a.id, "name": a.name, "description": a.description,
             "deleted_at": a.deleted_at or "", "created_at": iso(a.created_at)}
            for a in all_rows(models.Audience)
        ],
        "contacts": [
            {"id": c.id, "email": c.email, "name": c.name, "tags": normalise_tags(c.tags),
             "status": c.status, "source": c.source, "email_status": c.email_status,
             "email_reason": c.email_reason, "email_checked_at": c.email_checked_at or "",
             "deleted_at": c.deleted_at or "", "created_at": iso(c.created_at)}
            for c in all_rows(models.Contact)
        ],
        "audience_members": [
            {"audience_id": m.audience_id, "contact_id": m.contact_id}
            for m in db.query(models.AudienceMember).all()
        ],
        "suppressions": [
            {"email": s.email, "reason": s.reason, "created_at": iso(s.created_at)}
            for s in db.query(models.Suppression).all()
        ],
        "sequence_enrollments": [
            {"campaign_slug": e.campaign_slug, "sequence_slug": e.sequence_slug,
             "email": e.email, "current_ref": e.current_ref, "branch": e.branch,
             "status": e.status, "sends": e.sends, "next_due_at": e.next_due_at,
             "history": e.history_json or []}
            for e in db.query(models.SequenceEnrollment).all()
        ],
        "campaigns": [
            {"slug": c.slug, "title": c.title, "status": c.status, "product": c.product,
             "deleted_at": c.deleted_at or "", "emails": len(c.emails_json or [])}
            for c in all_rows(models.Campaign)
        ],
    }


# ------------------------------------------------- permanent removal
class PurgeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = "permanently removed"


@router.get("/purges", dependencies=[Depends(require_user)])
def list_purges(db: Session = Depends(get_db)):
    """The permanent-removal ledger. Nothing listed here can ever come back."""
    rows = db.query(models.Purge).order_by(models.Purge.id.desc()).limit(500).all()
    return {
        "count": len(rows),
        "purges": [
            {"id": r.id, "kind": r.kind, "identifier": r.identifier, "reason": r.reason,
             "created_at": r.created_at.isoformat() if r.created_at else ""}
            for r in rows
        ],
    }


@router.delete("/contacts/{email}/purge", dependencies=[Depends(require_user)])
def purge_contact(email: str, body: PurgeIn | None = None, db: Session = Depends(get_db)):
    """Remove a contact FOR GOOD.

    Unlike Trash, this is final: the address is written to the permanent-removal
    ledger, so no backup snapshot and no restore can bring it back.
    """
    from . import backup as bk
    addr = _clean(email)
    c = (db.query(models.Contact).filter(models.Contact.email == addr)
         .execution_options(include_trashed=True).first())
    if c:
        (db.query(models.AudienceMember)
           .filter(models.AudienceMember.contact_id == c.id)
           .delete(synchronize_session=False))
        (db.query(models.SequenceEnrollment)
           .filter(models.SequenceEnrollment.email == addr)
           .delete(synchronize_session=False))
        db.delete(c)
    bk.record_purge(db, "contact", addr, (body.reason if body else "") or "permanently removed")
    db.add(models.Job(job_type="warning", title=f"Permanently removed · {addr}",
                      detail="tombstoned - no backup or restore can bring it back",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "purged": addr, "permanent": True}


@router.delete("/audiences/{audience_id}/purge", dependencies=[Depends(require_user)])
def purge_audience(audience_id: int, body: PurgeIn | None = None, db: Session = Depends(get_db)):
    """Remove an audience FOR GOOD, at any state (live or already in Trash)."""
    from . import backup as bk
    a = _audience_or_404(db, audience_id, include_trashed=True)
    name = a.name
    (db.query(models.AudienceMember)
       .filter(models.AudienceMember.audience_id == a.id)
       .delete(synchronize_session=False))
    db.delete(a)
    bk.record_purge(db, "audience", name, (body.reason if body else "") or "permanently removed")
    db.add(models.Job(job_type="warning", title=f"Permanently removed · {name}",
                      detail="tombstoned - no backup or restore can bring it back",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "purged": name, "permanent": True,
            "contacts_kept": "the contacts themselves were not removed"}


@router.delete("/purges/{purge_id}", dependencies=[Depends(require_user)])
def forget_purge(purge_id: int, db: Session = Depends(get_db)):
    """Release a tombstone, allowing the item to be restored from a backup again."""
    row = db.query(models.Purge).filter(models.Purge.id == int(purge_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    kind, ident = row.kind, row.identifier
    db.delete(row)
    db.commit()
    return {"ok": True, "forgot": f"{kind}:{ident}"}


# ------------------------------------------------- tag management
class TagsBulkIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emails: list = []
    add: list = []
    remove: list = []


@router.post("/audiences/{audience_id}/tags", dependencies=[Depends(require_user)])
def bulk_contact_tags(audience_id: int, body: TagsBulkIn, db: Session = Depends(get_db)):
    """Add and/or remove tags across selected contacts, INSIDE THIS AUDIENCE ONLY.

    Writes to the membership, so tagging a group here never touches the same people
    in any other list.
    """
    a = _audience_or_404(db, audience_id)
    add = normalise_tags(body.add)
    remove = normalise_tags(body.remove)
    emails = [_clean(e) for e in (body.emails or []) if _clean(e)]
    if not emails:
        raise HTTPException(status_code=400, detail="No contacts selected")
    if not add and not remove:
        raise HTTPException(status_code=400, detail="Choose at least one tag to add or remove")
    if len(emails) > 2000:
        raise HTTPException(status_code=400, detail="Select 2000 contacts or fewer at a time")

    contact_ids = [c.id for c in
                   db.query(models.Contact).filter(models.Contact.email.in_(emails)).all()]
    members = (db.query(models.AudienceMember)
               .filter(models.AudienceMember.audience_id == a.id,
                       models.AudienceMember.contact_id.in_(contact_ids)).all()
               if contact_ids else [])
    changed = 0
    for m in members:
        before = normalise_tags(m.tags)
        after = [t for t in before if t not in remove]
        for t in add:
            if t not in after:
                after.append(t)
        if after != before:
            m.tags = after
            changed += 1

    # A tag added here is defined for this audience from now on, even if the last
    # contact later loses it - that is what makes a deliberate delete possible.
    for t in add:
        register_tag(db, a.id, t)

    db.add(models.Job(
        job_type="sync", title=f"Tags updated · {a.name}",
        detail=f"{len(members)} contact(s) · +{', '.join(add) or '-'} · −{', '.join(remove) or '-'}"
               f" · {changed} changed",
        status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "audience": a.name, "contacts": len(members), "changed": changed,
            "added": add, "removed": remove, "tags": audience_tag_inventory(db, a.id)}


class TagRenameIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    old: str
    new: str = ""


@router.post("/audiences/{audience_id}/tags/rename", dependencies=[Depends(require_user)])
def rename_tag(audience_id: int, body: TagRenameIn, db: Session = Depends(get_db)):
    """Rename a tag across THIS audience.

    Rename only - deleting a tag is a separate, guarded action that refuses while any
    contact still carries it, so a rename can never be used to bypass that check.
    """
    a = _audience_or_404(db, audience_id)
    old = _clean(body.old)
    new = _clean(body.new)
    if not old:
        raise HTTPException(status_code=400, detail="Which tag should change?")
    if not new:
        raise HTTPException(
            status_code=400,
            detail="A new name is required. To delete a tag, use Remove - it only works "
                   "once no contact carries the tag.",
        )
    if new == old:
        return {"ok": True, "audience": a.name, "from": old, "to": new, "contacts_changed": 0}

    members = db.query(models.AudienceMember).filter(
        models.AudienceMember.audience_id == a.id).all()
    changed = 0
    for m in members:
        tags = normalise_tags(m.tags)
        if old not in tags:
            continue
        out = [t for t in tags if t != old]
        if new not in out:
            out.append(new)
        m.tags = out
        changed += 1

    # The registry follows the rename; if the target name already existed, the two
    # collapse into one entry rather than leaving a duplicate.
    target = (db.query(models.AudienceTag)
              .filter(models.AudienceTag.audience_id == a.id,
                      func.lower(models.AudienceTag.name) == new).first())
    source = (db.query(models.AudienceTag)
              .filter(models.AudienceTag.audience_id == a.id,
                      func.lower(models.AudienceTag.name) == old).first())
    if source:
        if target:
            db.delete(source)
        else:
            source.name = new

    db.add(models.Job(job_type="sync", title=f"Tag renamed · {a.name}",
                      detail=f"“{old}” → “{new}” · {changed} contact(s) changed",
                      status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "audience": a.name, "from": old, "to": new,
            "contacts_changed": changed, "tags": audience_tag_inventory(db, a.id)}


# --------------------------------------------------------- email validation
class ValidateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emails: list = []


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _apply_verdict(contact, result, stamp):
    contact.email_status = result["status"]
    contact.email_reason = result.get("reason", "")
    contact.email_checked_at = stamp
    return _contact_dict(contact)


@router.post("/validate-emails", dependencies=[Depends(require_user)])
def validate_emails(body: ValidateIn):
    """Check addresses without saving anything - for previewing a paste before import."""
    from . import email_validator as ev
    results = ev.validate_many(body.emails)
    return {"summary": ev.summarise(results), "results": results}


@router.post("/audiences/{audience_id}/validate", dependencies=[Depends(require_user)])
def validate_audience(audience_id: int, db: Session = Depends(get_db)):
    """Verify every contact in an audience and store the verdict on each one."""
    from . import email_validator as ev
    a = _audience_or_404(db, audience_id)
    ids = [m.contact_id for m in
           db.query(models.AudienceMember).filter(models.AudienceMember.audience_id == a.id).all()]
    contacts = _live_contacts(db, ids)
    if not contacts:
        return {"audience": a.name, "checked": 0, "checked_at": "", "summary": ev.summarise([]), "contacts": []}

    stamp = _stamp()
    results = ev.validate_many([c.email for c in contacts])
    by_email = {r["email"]: r for r in results}
    rows = [_apply_verdict(c, by_email[c.email], stamp) for c in contacts if c.email in by_email]
    db.commit()
    return {"audience": a.name, "checked": len(rows), "checked_at": stamp,
            "summary": ev.summarise(results), "contacts": rows}


@router.post("/contacts/validate-all", dependencies=[Depends(require_user)])
def validate_all_contacts(db: Session = Depends(get_db)):
    """Verify the whole contact book."""
    from . import email_validator as ev
    contacts = db.query(models.Contact).filter(models.Contact.deleted_at == "").all()
    if not contacts:
        return {"checked": 0, "checked_at": "", "summary": ev.summarise([])}
    stamp = _stamp()
    results = ev.validate_many([c.email for c in contacts])
    by_email = {r["email"]: r for r in results}
    for c in contacts:
        if c.email in by_email:
            _apply_verdict(c, by_email[c.email], stamp)
    db.commit()
    return {"checked": len(contacts), "checked_at": stamp, "summary": ev.summarise(results)}


@router.post("/contacts/{email}/validate", dependencies=[Depends(require_user)])
def validate_contact(email: str, db: Session = Depends(get_db)):
    from . import email_validator as ev
    c = db.query(models.Contact).filter(models.Contact.email == _clean(email)).first()
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found")
    out = _apply_verdict(c, ev.classify(c.email), _stamp())
    db.commit()
    return out


# --------------------------------------------------------------- unsubscribed
class RemoveFromAudiencesIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emails: list = []
    # Empty means "every audience this address belongs to".
    audience_ids: list = []


@router.get("/unsubscribed", dependencies=[Depends(require_user)])
def list_unsubscribed(q: str = "", limit: int = 1000, db: Session = Depends(get_db)):
    """Every address that asked not to be emailed again, with where it happened.

    One screen for a question that previously had no answer in the UI: "who
    unsubscribed, and from which send?". A suppression is the authoritative record
    (it blocks future sends globally); the sequence enrollment and the contact's own
    status are joined on so the list shows context rather than a bare address.
    """
    needle = _clean(q)

    suppressions = (db.query(models.Suppression)
                      .order_by(models.Suppression.created_at.desc()).all())

    # Which send each address unsubscribed from.
    campaigns_by_email = {}
    for email, slug in (db.query(models.SequenceEnrollment.email,
                                 models.SequenceEnrollment.campaign_slug)
                          .filter(models.SequenceEnrollment.status == "unsubscribed")
                          .all()):
        campaigns_by_email.setdefault(_clean(email), set()).add(slug or "")

    contacts = {_clean(c.email): c for c in db.query(models.Contact).all()}
    audience_names = {a.id: a.name for a in db.query(models.Audience).all()}
    memberships = {}
    for m in db.query(models.AudienceMember).all():
        memberships.setdefault(m.contact_id, []).append(m.audience_id)

    items = []
    for s in suppressions:
        email = _clean(s.email)
        if needle and needle not in email:
            continue
        contact = contacts.get(email)
        aud_ids = memberships.get(contact.id, []) if contact else []
        items.append({
            "email": email,
            "reason": s.reason or "unsubscribe",
            "source": s.source or "",
            "detail": s.detail or "",
            "unsubscribed_at": s.created_at.isoformat() if s.created_at else "",
            "campaigns": sorted(x for x in campaigns_by_email.get(email, set()) if x),
            "contact_status": (contact.status if contact else ""),
            "audiences": [{"id": i, "name": audience_names.get(i, f"#{i}")} for i in aud_ids],
        })
        if len(items) >= max(1, int(limit or 1000)):
            break

    by_reason = {}
    for s in suppressions:
        key = (s.reason or "unsubscribe").lower()
        by_reason[key] = by_reason.get(key, 0) + 1

    return {"total": len(suppressions), "items": items, "by_reason": by_reason}


@router.post("/unsubscribed/remove-from-audiences", dependencies=[Depends(require_user)])
def remove_unsubscribed_from_audiences(body: RemoveFromAudiencesIn,
                                        db: Session = Depends(get_db)):
    """Drop unsubscribed addresses out of the given audiences.

    This removes list membership only. The suppression row and the contact itself stay
    exactly as they are: an unsubscribe is a promise never to mail that address again,
    so "remove from list" must never look like "put them back".
    """
    emails = sorted({_clean(e) for e in (body.emails or []) if _clean(e)})
    if not emails:
        raise HTTPException(status_code=400, detail="Select at least one address")

    contacts = (db.query(models.Contact)
                  .filter(models.Contact.email.in_(emails)).all())
    contact_ids = [c.id for c in contacts]
    if not contact_ids:
        return {"ok": True, "removed": 0, "addresses": len(emails),
                "note": "Those addresses are not in any saved audience"}

    query = (db.query(models.AudienceMember)
               .filter(models.AudienceMember.contact_id.in_(contact_ids)))
    aud_ids = [int(a) for a in (body.audience_ids or []) if str(a).strip().isdigit()]
    if aud_ids:
        query = query.filter(models.AudienceMember.audience_id.in_(aud_ids))

    removed = query.delete(synchronize_session=False)
    db.add(models.Job(
        job_type="warning", title=f"Removed {len(emails)} unsubscribed address(es) from audiences",
        detail=(f"{removed} membership(s) removed"
                + (f" from audiences {aud_ids}" if aud_ids else " (all audiences)")),
        status="done", created_at=stamp()))
    db.commit()
    return {"ok": True, "removed": removed, "addresses": len(emails),
            "audience_scope": aud_ids or "all"}
