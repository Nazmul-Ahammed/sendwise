from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean
from sqlalchemy.types import JSON
from sqlalchemy.sql import func

from .db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, default="Operator")
    role = Column(String, default="admin")


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, nullable=False)
    status = Column(String, default="draft")  # draft|approved|live|paused|exited|scheduled|sent
    audience_name = Column(String)
    audience_count = Column(Integer)
    product = Column(String)
    offer = Column(String)
    sender_name = Column(String)
    sender_email = Column(String)
    sequence_type = Column(String)
    # Which sequence template this send was built from ("single" for a one-off).
    # Stored as a slug so the sequence's own activity view can find its sends.
    sequence_slug = Column(String, default="", index=True)
    launched_at = Column(String)
    open_rate = Column(Float)
    click_rate = Column(Float)
    delivery_rate = Column(Float)
    sequence_json = Column(JSON, default=list)
    emails_json = Column(JSON, default=list)
    qa_json = Column(JSON, default=list)
    gates_json = Column(JSON, default=list)
    audience_options = Column(JSON, default=dict)  # {mode, manual_emails[], verified_count, verified_at}
    send_mode = Column(String, default="direct")  # direct | draft
    send_test_first = Column(String, default="false")  # true | false
    provider_message_id = Column(String, default="")  # SendClean X-Unique-Id after a real send
    delivery_json = Column(JSON, default=dict)  # {batch, sent_at, recipients:[{email, x_unique_id, status}]}
    # Soft delete: a campaign is moved to Trash, never erased.
    deleted_at = Column(String, default="", index=True)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    job_type = Column(String)  # dispatch|report|qa|audit|launch|sync|warning
    title = Column(String)
    detail = Column(String)
    status = Column(String, default="done")  # queued|running|done|failed
    created_at = Column(String)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text)


class Sequence(Base):
    __tablename__ = "sequences"
    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    trigger = Column(String, default="Manual / campaign entry")
    stages_json = Column(JSON, default=list)
    rules_json = Column(JSON, default=list)
    created_at = Column(String, default="")
    # Soft delete: a sequence template is moved to Trash, never erased.
    deleted_at = Column(String, default="", index=True)


class TrendRun(Base):
    __tablename__ = "trend_runs"
    id = Column(Integer, primary_key=True)
    status = Column(String, default="running", nullable=False)
    mode = Column(String, default="draft_only", nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True))
    products_scanned = Column(Integer, default=0)
    opportunities_found = Column(Integer, default=0)
    drafts_created = Column(Integer, default=0)
    source_health_json = Column(JSON, default=dict)
    error_json = Column(JSON, default=list)


class TrendOpportunity(Base):
    __tablename__ = "trend_opportunities"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=False, index=True)
    product_key = Column(String, nullable=False, index=True)
    product_name = Column(String, nullable=False)
    topic = Column(String, nullable=False)
    normalized_topic = Column(String, nullable=False, index=True)
    score = Column(Integer, nullable=False)
    score_json = Column(JSON, default=dict)
    confidence = Column(String, nullable=False)
    independent_source_count = Column(Integer, default=0)
    selected = Column(Boolean, default=False, nullable=False)
    fatigue_blocked = Column(Boolean, default=False, nullable=False)
    fatigue_reason = Column(String, default="")
    draft_json = Column(JSON, default=dict)
    qa_json = Column(JSON, default=list)
    approval_status = Column(String, default="draft", nullable=False)  # draft|approved|rejected
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TrendEvidence(Base):
    __tablename__ = "trend_evidence"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=False, index=True)
    opportunity_id = Column(Integer, index=True)
    product_key = Column(String, nullable=False, index=True)
    source = Column(String, nullable=False)
    topic = Column(String, nullable=False)
    title = Column(Text, nullable=False)
    source_url = Column(Text, nullable=False)
    published_at = Column(DateTime(timezone=True))
    observed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    evidence_json = Column(JSON, default=dict)


class TrendSourceCache(Base):
    __tablename__ = "trend_source_cache"
    id = Column(Integer, primary_key=True)
    cache_key = Column(String, unique=True, index=True, nullable=False)
    source = Column(String, nullable=False)
    payload_json = Column(JSON, default=dict)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class Suppression(Base):
    """One row per address that must never be emailed again.

    SendClean provides no unsubscribe or suppression API, so Sendwise owns this:
    it is checked before every sequence send and blocks the address globally.
    """
    __tablename__ = "suppressions"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    reason = Column(String, default="unsubscribe")  # unsubscribe|hard_bounce|complaint|manual
    source = Column(String, default="")
    detail = Column(String, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EngagementEvent(Base):
    """One provider-reported open or click, captured the moment it happens.

    SendClean's webhook is the only realtime channel available to us: every other
    reading of tracking data is a pull that happens when something asks for it. These
    rows are the push side, so a click or an open lands here within seconds of
    happening rather than at the next poll.

    Append-only by design. An engagement event is a record that something occurred,
    so the table carries no `deleted_at` and the soft-delete guard deliberately leaves
    it alone: hiding history would defeat the purpose of keeping it.

    `is_machine` is our own reading *at capture time*. A mail provider's image proxy
    fetches the tracking pixel so it can cache the message, and that is not a person
    reading it; the same distinction `open_signal` makes when reading a message row.
    The raw payload is stored alongside so the classification can be re-derived if the
    rules change and so an unexpected field name is discoverable after the fact.
    """
    __tablename__ = "engagement_events"
    id = Column(Integer, primary_key=True)
    email = Column(String, default="", index=True)
    event_type = Column(String, default="", index=True)      # open | click
    x_unique_id = Column(String, default="", index=True)
    campaign_slug = Column(String, default="", index=True)   # parsed from x_unique_id
    ref = Column(String, default="")                         # E1, E2 ...
    is_machine = Column(Boolean, default=False, index=True)
    machine_reason = Column(String, default="")
    ip = Column(String, default="")
    user_agent = Column(String, default="")
    occurred_at = Column(String, default="")                 # provider's timestamp, if sent
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    raw_json = Column(JSON, default=dict)


class Purge(Base):
    """A permanent-removal ledger - the one thing a restore must always respect.

    Everything else in Sendwise is recoverable: deletes are soft and a backup
    restore can bring anything back. This table is the deliberate exception. When
    the operator permanently removes something, its identifier is written here, and
    every restore consults this list FIRST and skips anything on it.

    That makes a permanent delete genuinely permanent: no Trash entry, no backup
    snapshot and no restore can resurrect it.
    """
    __tablename__ = "purges"
    id = Column(Integer, primary_key=True)
    kind = Column(String, index=True, nullable=False)        # contact|audience|campaign|sequence
    identifier = Column(String, index=True, nullable=False)  # email | audience name | slug
    reason = Column(String, default="permanently removed")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Backup(Base):
    """An automatic snapshot Sendwise takes of itself.

    No manual step and no external service: the scheduler asks "is a backup due?"
    on its existing tick and on startup, so snapshots happen on their own. The
    payload holds every audience, contact, tag, suppression and enrollment -
    including trashed rows - so a restore can rebuild the data exactly.
    """
    __tablename__ = "backups"
    id = Column(Integer, primary_key=True)
    label = Column(String, default="auto")        # startup|auto|pre-restore|manual
    size_bytes = Column(Integer, default=0)
    checksum = Column(String, default="")
    counts_json = Column(JSON, default=dict)
    payload_json = Column(JSON, default=dict)
    # Where the on-disk mirror was written, when one was. Empty means the snapshot lives
    # only in the database, so a restore knows whether a file copy exists to fall back on.
    file_path = Column(String, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SequenceEnrollment(Base):
    """Per-recipient progress through a branched sequence.

    `current_ref` is the last email actually SENT to this address; the next ref is
    decided from that email's real open/click data when `next_due_at` arrives.
    """
    __tablename__ = "sequence_enrollments"
    id = Column(Integer, primary_key=True)
    campaign_slug = Column(String, index=True, nullable=False)
    sequence_slug = Column(String, default="")
    email = Column(String, index=True, nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    current_ref = Column(String, default="")      # last SENT ref ("" = nothing sent yet)
    stage = Column(Integer, default=1)            # sequence stage number
    branch = Column(String, default="main")       # main|opened|not_opened|clicked|...
    status = Column(String, default="active")     # active|completed|unsubscribed|suppressed|failed
    last_sent_at = Column(String, default="")
    next_due_at = Column(String, default="")      # ISO-8601 UTC
    last_x_unique_id = Column(String, default="")
    sends = Column(Integer, default=0)
    history_json = Column(JSON, default=list)
    # Delivery safety. `pending_ref` is written and COMMITTED before a send leaves, so
    # a crash mid-send is visible afterwards instead of silently repeating. Nothing
    # with a pending_ref is ever auto-resent: a duplicate email is worse than a missing
    # one, so it is flagged for a human. `attempts` bounds retries of a transient error.
    pending_ref = Column(String, default="")
    pending_at = Column(String, default="")
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Contact(Base):
    """A saved recipient Sendwise owns (SendClean has no contact/audience API)."""
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, default="")
    tags = Column(JSON, default=list)             # free-form labels, e.g. ["travilo","trial"]
    status = Column(String, default="subscribed")  # subscribed|unsubscribed|suppressed
    source = Column(String, default="")            # manual|import|campaign|webhook
    # Deliverability check, cached so a send never has to re-resolve DNS.
    email_status = Column(String, default="unverified")  # valid|invalid|no_mx|disposable|role|unknown
    email_reason = Column(String, default="")
    email_checked_at = Column(String, default="")
    # Soft delete: a contact is moved to Trash, never erased. Its tags live on it,
    # so tags can never disappear either.
    deleted_at = Column(String, default="", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Audience(Base):
    """A named saved list of contacts, reusable across campaigns."""
    __tablename__ = "audiences"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, default="")
    # Soft delete: a list is moved to Trash together with its membership, and can
    # be restored exactly as it was.
    deleted_at = Column(String, default="", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AudienceTag(Base):
    """A tag DEFINED for one audience - it exists even while nothing uses it.

    Tags are derived from memberships, so without a registry a tag would vanish the
    moment its last contact lost it, and could never be "created then deleted".
    This table gives each audience its own explicit tag list, which is what makes a
    deliberate delete possible - and what lets it refuse to delete a tag that is
    still attached to a contact.
    """
    __tablename__ = "audience_tags"
    id = Column(Integer, primary_key=True)
    audience_id = Column(Integer, index=True, nullable=False)
    name = Column(String, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AudienceMember(Base):
    """A contact's place in one audience - and that audience's OWN tags for them.

    Tags live here rather than on the contact on purpose. Tagging is a per-list
    decision: "customer" in the Travilo list has nothing to do with "customer" in
    the TrackForce list, so a tag created in one audience never leaks into another.
    The same person can therefore carry different tags in different lists.
    """
    __tablename__ = "audience_members"
    id = Column(Integer, primary_key=True)
    audience_id = Column(Integer, index=True, nullable=False)
    contact_id = Column(Integer, index=True, nullable=False)
    tags = Column(JSON, default=list)   # scoped to THIS audience
    added_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SendRecord(Base):
    """The delivery ledger: one row per recipient we actually handed to SendClean.

    SendClean has no per-recipient event API and no aggregate report, so every number
    this app shows used to be pulled live from the provider - or guessed. That made the
    record of a send only as good as the provider answering at that moment, and a send
    whose data could not be read back looked like a send that never happened.

    This table is Sendwise's own copy of the facts it is certain of, because it wrote
    them itself: which address, which step, which X-Unique-Id, and whether the provider
    accepted it. It is written at send time, in the same transaction as the send claim,
    so it survives a crashed request, a provider outage and a rotated API token.

    Open and click are deliberately NOT stored here. They are captured by our own pixel
    and click redirect into `engagement_events`, and copying a derived count into the
    ledger would create a second, older truth. A report joins the two.

    Append-oriented: `status` only moves forward (sent -> suppressed/failed/bounced) and
    a row is never deleted, so the ledger can always answer "how many did we send?" even
    when the provider cannot be asked.
    """
    __tablename__ = "send_records"
    id = Column(Integer, primary_key=True)
    campaign_slug = Column(String, default="", index=True)
    ref = Column(String, default="", index=True)             # E1..E9, "" for a one-off
    email = Column(String, default="", index=True)
    x_unique_id = Column(String, default="", index=True)     # provider id for this send
    batch = Column(String, default="", index=True)           # launch batch, groups a run
    status = Column(String, default="sent", index=True)      # sent|failed|suppressed|skipped
    reason = Column(String, default="")
    provider_status = Column(String, default="")
    provider_message = Column(String, default="")
    attempts = Column(Integer, default=1)
    first_sent_at = Column(String, default="")
    last_update_at = Column(String, default="")
    raw_json = Column(JSON, default=dict)


class DeliveryStatus(Base):
    """The provider's own verdict for one sent message.

    SendClean is the only party that knows whether a message was delivered or bounced.
    This table is that answer, stored per X-Unique-Id, and it is the source of truth for a
    campaign's Delivered and Bounced figures. It exists because the provider webhook never
    delivered an event to this deployment - a direct message lookup returned "hard_bounce"
    while `suppressions` held no bounce row at all - so the verdict is pulled and kept.

    `status` uses our vocabulary: delivered | hard_bounce | soft_bounce | queued | unknown.
    """
    __tablename__ = "delivery_status"
    id = Column(Integer, primary_key=True)
    x_unique_id = Column(String, default="", index=True)
    campaign_slug = Column(String, default="", index=True)
    ref = Column(String, default="")
    email = Column(String, default="", index=True)
    status = Column(String, default="", index=True)
    raw_status = Column(String, default="")
    checked_at = Column(String, default="")
