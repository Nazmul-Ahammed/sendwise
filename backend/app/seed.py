"""Sendwise seed - creates tables and loads demo data (idempotent)."""
import json
import os

from dotenv import load_dotenv

from .auth import hash_password
from .db import Base, SessionLocal, engine
from . import models

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

STAGE_TARGETS = [
    {"stage": 1, "focus": "Native Pattern Interrupt / Stack Question", "open_min": 0.15, "open_max": 0.18, "click_min": 0.015, "click_max": 0.02},
    {"stage": 2, "focus": "Split Track - Unopened vs Opened Pain Point", "open_min": 0.12, "open_max": 0.14, "click_min": 0.01, "click_max": 0.015},
    {"stage": 3, "focus": "Developer Shortcut / Speed Proof", "open_min": 0.10, "open_max": 0.12, "click_min": 0.01, "click_max": 0.012},
    {"stage": 4, "focus": "Visual Technical Proof / GTmetrix Teardown", "open_min": 0.10, "open_max": 0.12, "click_min": 0.01, "click_max": 0.015},
    {"stage": 5, "focus": "Interactive Demo / Feature Walkthrough", "open_min": 0.08, "open_max": 0.10, "click_min": 0.01, "click_max": 0.012},
    {"stage": 6, "focus": "Reactivation Incentive / 30% Dev Credit", "open_min": 0.08, "open_max": 0.10, "click_min": 0.015, "click_max": 0.02},
    {"stage": 7, "focus": "Final Breakup", "open_min": 0.08, "open_max": 0.10, "click_min": 0.012, "click_max": 0.015},
]

ACTUAL_OPENS = {1: 0.162, 2: 0.128, 3: 0.091, 4: 0.106, 5: 0.074, 6: 0.092, 7: 0.088}

# E = Email (message), S = Stage (flow step). Routing is PER EMAIL with 3 outcomes:
# open_click_next (opened + clicked) · open_no_click_next (opened, no click) · not_open_next (not opened).
# Value = next email ref, "" = Exit.
EMAIL_MAP = {
    1: [{"ref": "E1", "name": "Stack Question", "branch": "main",
         "open_click_next": "E2", "open_no_click_next": "E3", "not_open_next": "E4"}],
    2: [
        {"ref": "E2", "name": "Open+Click - engaged, speed proof", "branch": "open_click",
         "open_click_next": "E5", "open_no_click_next": "E6", "not_open_next": "E7"},
        {"ref": "E3", "name": "Open+Not Click - pain-point", "branch": "open_no_click",
         "open_click_next": "E5", "open_no_click_next": "E6", "not_open_next": "E7"},
        {"ref": "E4", "name": "Not Open - pain-point angle", "branch": "not_open",
         "open_click_next": "E5", "open_no_click_next": "E6", "not_open_next": "E7"},
    ],
    3: [
        {"ref": "E5", "name": "Speed Proof / Dev Shortcut", "branch": "main",
         "open_click_next": "E8", "open_no_click_next": "E9", "not_open_next": "E10"},
        {"ref": "E6", "name": "Speed Proof - warm", "branch": "main",
         "open_click_next": "E8", "open_no_click_next": "E9", "not_open_next": "E10"},
        {"ref": "E7", "name": "Speed Proof - cold", "branch": "main",
         "open_click_next": "E8", "open_no_click_next": "E9", "not_open_next": "E10"},
    ],
    4: [
        {"ref": "E8", "name": "GTmetrix Teardown", "branch": "main",
         "open_click_next": "E11", "open_no_click_next": "E11", "not_open_next": "E11"},
        {"ref": "E9", "name": "GTmetrix - warm", "branch": "main",
         "open_click_next": "E11", "open_no_click_next": "E11", "not_open_next": "E11"},
        {"ref": "E10", "name": "GTmetrix - cold", "branch": "main",
         "open_click_next": "E11", "open_no_click_next": "E11", "not_open_next": "E11"},
    ],
    5: [{"ref": "E11", "name": "Final Breakup / Reactivation", "branch": "main",
         "open_click_next": "", "open_no_click_next": "", "not_open_next": ""}],
    6: [],
    7: [],  # exit stages
}

# delay_value + delay_unit (hour/day/week) + delay_note
STAGE_DELAYS = {
    1: (0, "hour", "Entry - activation + 1 plugin"),
    2: (2, "day", "after S1 · based on E1"),
    3: (1, "day", "after S2 · based on E2/E3/E4"),
    4: (1, "day", "after S3"),
    5: (2, "day", "after S4 · then exits"),
    6: (0, "hour", "Exit - sequence ends"),
    7: (0, "hour", "Exit - sequence ends"),
}


def _stages():
    out = []
    for t in STAGE_TARGETS:
        actual = ACTUAL_OPENS.get(t["stage"], None)
        status = "On target"
        if actual is not None:
            if actual < t["open_min"]:
                status = "Below min"
            elif actual > t["open_max"]:
                status = "Above max"
        dv, du, dn = STAGE_DELAYS.get(t["stage"], (1, "day", ""))
        out.append({
            "stage": t["stage"],
            "focus": t["focus"],
            "delay_value": dv,
            "delay_unit": du,
            "delay_note": dn,
            "open_min": round(t["open_min"] * 100, 1),
            "open_max": round(t["open_max"] * 100, 1),
            "click_min": round(t["click_min"] * 100, 2),
            "click_max": round(t["click_max"] * 100, 2),
            "actual_open": round(actual * 100, 1) if actual else None,
            "status": status,
            "emails": EMAIL_MAP.get(t["stage"], []),
        })
    return out


def _gates():
    return [
        {"name": "Product knowledge sourced", "detail": "No unresolved facts", "pass": True},
        {"name": "Audience, consent, identity, address, unsubscribe", "detail": "All present · opt-in basis", "pass": True},
        {"name": "Offer, dates, links, personalization, tracking", "detail": "Validated", "pass": True},
        {"name": "Compliance + QA checks", "detail": "14 checks + SendClean credential validation", "pass": True},
        {"name": "Suppression + purchase exit", "detail": "Configured", "pass": True},
        {"name": "KPI targets + emergency thresholds", "detail": "All configured · within limits", "pass": True},
        {"name": "Audience & volume limits", "detail": "7,062 <= 7,062 max", "pass": True},
    ]


def _emails():
    return [
        {"stage": 1, "subject": "\"Your stack question, answered - without a ticket\"",
         "from": "Himel from Turbo Add-ons <hello@turbo-addons.com>", "status": "QA passed"},
        {"stage": 2, "subject": "Unopened: the slow-site bill is compounding", "from": "Himel from Turbo Add-ons <hello@turbo-addons.com>", "status": "QA passed"},
        {"stage": 3, "subject": "60 seconds to a faster stack", "from": "Himel from Turbo Add-ons <hello@turbo-addons.com>", "status": "QA passed"},
        {"stage": 4, "subject": "GTmetrix before / after - the teardown", "from": "Himel from Turbo Add-ons <hello@turbo-addons.com>", "status": "QA passed"},
        {"stage": 5, "subject": "See Turbo Add-ons do the work", "from": "Himel from Turbo Add-ons <hello@turbo-addons.com>", "status": "QA passed"},
        {"stage": 6, "subject": "30% dev credit - build it faster", "from": "Himel from Turbo Add-ons <hello@turbo-addons.com>", "status": "QA passed"},
        {"stage": 7, "subject": "Last one from us", "from": "Himel from Turbo Add-ons <hello@turbo-addons.com>", "status": "QA passed"},
    ]


def _qa():
    items = [
        ("Brand voice", "Doctrine + terminology matched"),
        ("Product facts sourced", "Product knowledge reference present"),
        ("No unverified claims", "Blocked per safety config"),
        ("Links + tracking", "UTM scheme · click tracking on"),
        ("Personalization fallback", "[[FNAME]] fallback set"),
        ("Unsubscribe present", "Required by compliance gate"),
        ("Sender identity + address", "Physical address in footer"),
        ("SendClean credentials validated", "Owner ID + token + SMTP user"),
    ]
    return [{"name": n, "detail": d, "pass": True} for n, d in items]


SETTINGS = {
    "timezone": "Asia/Dhaka",
    "email_provider": "sendclean",
    "sendclean_owner_id": "",
    "sendclean_api_token": "",
    "sendclean_api_token_enc": "",
    "sendclean_smtp_user_name": "",
    "default_from_name": "Nazmul Ahammed",
    "default_reply_to": "nazmul.ahammed@ibos.io",
    "maximum_recipients_per_send": "200",
    "reporting_sheet": "1wPX4kwnYcI_xIiuRZ9ih-muqnruG8WKz4SCirGKu7OA",
    "report_recipients": json.dumps(["mubasshir@ibos.io", "cmo@ibos.io", "mizanurrahaman@ibos.io", "nazmul.ahammed.nz@gmail.com"]),
    "report_email_subject": "Email Campaign Performance Report - {date}",
    "report_email_format": "Dear All,\n\nHere is the email campaign report below:",
    "daily_sheet_update": "09:50",
    "daily_email": "10:00",
    "report_schedule_mode": "daily",
    "report_schedule_time": "10:00",
    "report_schedule_date": "",
    "sheet_update_frequency": "daily",
    "sheet_update_time": "09:50",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_app_password": "",
    "smtp_from": "",
    "sequence_dispatch_interval_minutes": "15",
    "trend_intelligence_enabled": "false",
    "trend_email_mode": "draft_only",
    "trend_schedule_time": "08:00",
    "trend_schedule_timezone": "Asia/Dhaka",
    "trend_rotation_enabled": "true",
    "trend_manual_all_products": "false",
    "trend_product_keys": json.dumps(["managerium", "peopledesk", "primevat", "trackforce", "turbo_addons", "turbo_guard", "travilo", "aeroapps", "aerodrop"]),
    "trend_source_cache_ttl_hours": "12",
    "trend_google_trends_cache_ttl_hours": "72",
    "primary_kpis": json.dumps(["click_rate", "open_rate_directional"]),
    "products": json.dumps([
        "Turbo Add-ons", "Turbo Guard", "Travilo", "Family Guard", "Managerium ERP", "PeopleDesk HRMS",
        "PrimeVAT", "TrackForce", "AeroApps (Shopify)", "AeroDrop", "iBOS Services",
    ]),
    "audience_sender_map": json.dumps([]),
    "target_delivery_rate": "0.95",
    "target_click_rate": "0.013",
    "target_blended_open_rate": "0.115",
    "maximum_bounce_rate": "0.02",
    "maximum_unsubscribe_rate": "0.005",
    "maximum_complaint_rate": "0.001",
    "minimum_delivered_sample": "500",
    "evaluation_window_hours": "72",
    "maximum_auto_launch_recipients": "7062",
    "stage_targets": json.dumps(STAGE_TARGETS),
}


def _sequence_rules():
    return [
        {"name": "Exit on purchase", "detail": "Purchase detected -> member exits sequence", "pass": True},
        {"name": "Suppression rules", "detail": "Unsubscribe + complaint suppressed", "pass": True},
        {"name": "Conflicting sequences", "detail": "Prevent duplicate journeys per member", "pass": True},
        {"name": "Complaint pause", "detail": "Threshold breach -> auto-pause", "pass": True},
        {"name": "CRM conversion target", "detail": "No CRM hookup - unavailable in SendClean-only mode", "pass": False},
    ]


TURBO_SEQUENCE = {
    "slug": "turbo-addons-activated-1plus",
    "name": "Turbo Add-ons Activated 1+ (7-stage)",
    "description": "Confirmed iBOS lifecycle for activated Turbo Add-ons users - pattern interrupt to breakup.",
    "trigger": "Activation + 1 plugin · purchase-exit guarded",
    "stages": _stages(),
    "rules": _sequence_rules(),
}

TRAVILO_SEQUENCE = {
    "slug": "travilo-demo-16aug",
    "name": "Travilo 16-Aug Demo (3-stage)",
    "description": "Demo nurture for Travilo 16 Aug segment signups.",
    "trigger": "Demo signup · 16 Aug segment",
    "stages": [
        {"stage": 1, "focus": "Workflow Question", "delay": "Day 0", "open_min": 12.0, "open_max": 16.0, "click_min": 1.0, "click_max": 2.0, "actual_open": 13.4, "status": "On target"},
        {"stage": 2, "focus": "System Proof", "delay": "Day 2", "open_min": 10.0, "open_max": 14.0, "click_min": 1.0, "click_max": 1.5, "actual_open": None, "status": "On target"},
        {"stage": 3, "focus": "Demo CTA", "delay": "Day 4", "open_min": 8.0, "open_max": 12.0, "click_min": 1.0, "click_max": 1.5, "actual_open": None, "status": "On target"},
    ],
    "rules": _sequence_rules(),
}


NINE_EMAIL_FRAMEWORK = {
    "slug": "ibos-9-email-branch-framework",
    "name": "iBOS Default 9-Email Branch Flow",
    "description": "E1 opens the journey; openers receive E2 and non-openers receive E3. Each path then branches by open/click behavior into E4–E9.",
    "trigger": "Approved audience entry · purchase/conversion guarded",
    "stages": [
        {"stage": 1, "focus": "Initial value / context", "delay_value": 0, "delay_unit": "day", "delay_note": "Immediate on entry",
         "open_min": None, "open_max": None, "click_min": None, "click_max": None, "actual_open": None, "status": "On target",
         "emails": [{"ref": "E1", "name": "Initial value / context", "branch": "main",
                     "open_click_next": "E2", "open_no_click_next": "E2", "not_open_next": "E3"}]},
        {"stage": 2, "focus": "E1 opened? → opener/non-opener split", "delay_value": 3, "delay_unit": "day", "delay_note": "After E1",
         "open_min": None, "open_max": None, "click_min": None, "click_max": None, "actual_open": None, "status": "On target",
         "emails": [
             {"ref": "E2", "name": "Problem recognition for E1 openers", "branch": "opened_e1", "open_click_next": "E4", "open_no_click_next": "E5", "not_open_next": "E6"},
             {"ref": "E3", "name": "New angle for E1 non-openers", "branch": "not_opened_e1", "open_click_next": "E7", "open_no_click_next": "E8", "not_open_next": "E9"},
         ]},
        {"stage": 3, "focus": "E2/E3 behavior branches", "delay_value": 3, "delay_unit": "day", "delay_note": "After E2 or E3",
         "open_min": None, "open_max": None, "click_min": None, "click_max": None, "actual_open": None, "status": "On target",
         "emails": [
             {"ref": "E4", "name": "E2 opened + CTA clicked", "branch": "e2_open_click", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E5", "name": "E2 opened + CTA not clicked", "branch": "e2_open_no_click", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E6", "name": "E2 not opened", "branch": "e2_not_open", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E7", "name": "E3 opened + CTA clicked", "branch": "e3_open_click", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E8", "name": "E3 opened + CTA not clicked", "branch": "e3_open_no_click", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E9", "name": "E3 not opened", "branch": "e3_not_open", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
         ]},
        {"stage": 4, "focus": "Exit / conversion check", "delay_value": 3, "delay_unit": "day", "delay_note": "After E4–E9 · then exit",
         "open_min": None, "open_max": None, "click_min": None, "click_max": None, "actual_open": None, "status": "On target", "emails": []},
    ],
    "rules": [
        {"name": "Pre-send conversion guard", "detail": "Converted contacts exit before every next email", "pass": True},
        {"name": "Behavior branch flow", "detail": "E1 → E2/E3; E2 → E4/E5/E6; E3 → E7/E8/E9", "pass": True},
        {"name": "Unique UTM per CTA", "detail": "utm_content unique per email/CTA (skill 81)", "pass": True},
        {"name": "Suppression rules", "detail": "Unsubscribe + complaint suppressed", "pass": True},
        {"name": "Sequence conflict control", "detail": "Prevent duplicate journeys for the same contact", "pass": True},
    ],
}


TURBO_GUARD_COFFEE_SEQUENCE = {
    "slug": "turbo-guard-peace-of-mind-coffee-offer",
    "name": "Turbo Guard: Peace Of Mind. Same Price.",
    "description": "Nine-email behavior flow for the approved US$5.55 first-year Turbo Guard Pro campaign concept, including the qualified PHP web-shell education angle.",
    "trigger": "Approved opted-in WordPress owner or freelancer audience entry; exclude Pro purchasers and active conflicting journeys",
    "stages": [
        {"stage": 1, "focus": "Offer reveal: Peace Of Mind. Same Price.", "delay_value": 0, "delay_unit": "hour", "delay_note": "Immediate on approved entry",
         "open_min": None, "open_max": None, "click_min": None, "click_max": None, "actual_open": None, "status": "Draft",
         "emails": [{"ref": "E1", "name": "Peace of mind, same price offer reveal", "branch": "main",
                     "open_click_next": "E2", "open_no_click_next": "E2", "not_open_next": "E3"}]},
        {"stage": 2, "focus": "Behavior split: risk education or business-care reframe", "delay_value": 2, "delay_unit": "day", "delay_note": "After E1",
         "open_min": None, "open_max": None, "click_min": None, "click_max": None, "actual_open": None, "status": "Draft",
         "emails": [
             {"ref": "E2", "name": "PHP web-shell risk and supported evidence", "branch": "opened_e1",
              "open_click_next": "E4", "open_no_click_next": "E5", "not_open_next": "E6"},
             {"ref": "E3", "name": "Give the website you built the same care", "branch": "not_opened_e1",
              "open_click_next": "E7", "open_no_click_next": "E8", "not_open_next": "E9"},
         ]},
        {"stage": 3, "focus": "Intent-matched decision support", "delay_value": 3, "delay_unit": "day", "delay_note": "After E2 or E3",
         "open_min": None, "open_max": None, "click_min": None, "click_max": None, "actual_open": None, "status": "Draft",
         "emails": [
             {"ref": "E4", "name": "Clicked risk email: Pro cleanup and monitoring proof", "branch": "e2_open_click", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E5", "name": "Opened risk email: Free versus Pro objection", "branch": "e2_open_no_click", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E6", "name": "Did not reopen: concise offer details", "branch": "e2_not_open", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E7", "name": "Clicked care email: checkout decision checklist", "branch": "e3_open_click", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E8", "name": "Opened care email: value beyond price", "branch": "e3_open_no_click", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
             {"ref": "E9", "name": "No engagement: respectful final offer note", "branch": "e3_not_open", "open_click_next": "", "open_no_click_next": "", "not_open_next": ""},
         ]},
        {"stage": 4, "focus": "Exit and conversion check", "delay_value": 0, "delay_unit": "hour", "delay_note": "Exit after the selected E4-E9 branch",
         "open_min": None, "open_max": None, "click_min": None, "click_max": None, "actual_open": None, "status": "Draft", "emails": []},
    ],
    "rules": [
        {"name": "Pre-send purchase guard", "detail": "Turbo Guard Pro purchasers exit before every next email", "pass": True},
        {"name": "Behavior branch flow", "detail": "E1 to E2/E3; E2 to E4/E5/E6; E3 to E7/E8/E9", "pass": True},
        {"name": "Unique UTM per CTA", "detail": "Each email CTA receives a unique utm_content value", "pass": True},
        {"name": "Suppression and sequence conflict", "detail": "Unsubscribe, complaint, bounce, and conflicting active journeys are excluded", "pass": True},
        {"name": "Offer implementation confirmation", "detail": "Exact dates, eligibility, renewal amount, tax, refund policy, checkout, and purchase attribution remain launch blockers", "pass": False},
        {"name": "Third-party brand approval", "detail": "Starbucks logo use requires approval and must not imply a partnership", "pass": False},
    ],
}


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            db.add(models.User(
                email=os.getenv("ADMIN_EMAIL", "admin@sendwise.io"),
                password_hash=hash_password(os.getenv("ADMIN_PASSWORD", "admin123")),
                name="Nazmul Ahammed",
                role="admin",
            ))

        for key, value in SETTINGS.items():
            if db.query(models.Setting).filter(models.Setting.key == key).first() is None:
                db.add(models.Setting(key=key, value=value))

        # Flush so the reconcile below can SEE the rows just added. Without this the
        # pending inserts are invisible to the query, the loop treats them as missing and
        # inserts them again, and the batch dies on the primary key.
        db.flush()

        # Reconcile additive product settings for existing installations without
        # replacing operator choices or inventing audience/sender mappings.
        for key, required in (("products", "Turbo Guard"), ("trend_product_keys", "turbo_guard")):
            row = db.query(models.Setting).filter(models.Setting.key == key).first()
            try:
                values = json.loads(row.value) if row and row.value else []
            except (json.JSONDecodeError, TypeError):
                values = []
            if not isinstance(values, list):
                values = []
            changed = False
            if key == "products" and "Turbo Guards" in values:
                values = [required if value == "Turbo Guards" else value for value in values]
                changed = True
            if required not in values:
                values.append(required)
                changed = True
            if changed:
                # A first run may not have this row at all. Create it instead of
                # dereferencing None, which used to abort seeding and the whole boot.
                if row is None:
                    db.add(models.Setting(key=key, value=json.dumps(values)))
                else:
                    row.value = json.dumps(values)

        if db.query(models.Sequence).count() == 0:
            db.add(models.Sequence(
                slug=TURBO_SEQUENCE["slug"], name=TURBO_SEQUENCE["name"],
                description=TURBO_SEQUENCE["description"], trigger=TURBO_SEQUENCE["trigger"],
                stages_json=TURBO_SEQUENCE["stages"], rules_json=TURBO_SEQUENCE["rules"],
                created_at="2026-08-30",
            ))
            db.add(models.Sequence(
                slug=TRAVILO_SEQUENCE["slug"], name=TRAVILO_SEQUENCE["name"],
                description=TRAVILO_SEQUENCE["description"], trigger=TRAVILO_SEQUENCE["trigger"],
                stages_json=TRAVILO_SEQUENCE["stages"], rules_json=TRAVILO_SEQUENCE["rules"],
                created_at="2026-08-27",
            ))
        canonical = db.query(models.Sequence).filter(models.Sequence.slug == NINE_EMAIL_FRAMEWORK["slug"]).first()
        if not canonical:
            canonical = models.Sequence(slug=NINE_EMAIL_FRAMEWORK["slug"], created_at="2026-09-01")
            db.add(canonical)
        canonical.name = NINE_EMAIL_FRAMEWORK["name"]
        canonical.description = NINE_EMAIL_FRAMEWORK["description"]
        canonical.trigger = NINE_EMAIL_FRAMEWORK["trigger"]
        canonical.stages_json = NINE_EMAIL_FRAMEWORK["stages"]
        canonical.rules_json = NINE_EMAIL_FRAMEWORK["rules"]

        coffee = db.query(models.Sequence).filter(models.Sequence.slug == TURBO_GUARD_COFFEE_SEQUENCE["slug"]).first()
        if not coffee:
            coffee = models.Sequence(slug=TURBO_GUARD_COFFEE_SEQUENCE["slug"], created_at="2026-10-28")
            db.add(coffee)
        coffee.name = TURBO_GUARD_COFFEE_SEQUENCE["name"]
        coffee.description = TURBO_GUARD_COFFEE_SEQUENCE["description"]
        coffee.trigger = TURBO_GUARD_COFFEE_SEQUENCE["trigger"]
        coffee.stages_json = TURBO_GUARD_COFFEE_SEQUENCE["stages"]
        coffee.rules_json = TURBO_GUARD_COFFEE_SEQUENCE["rules"]

        if db.query(models.Campaign).count() == 0:
            db.add_all([
                models.Campaign(
                    slug="activated-1plus-open-sequence",
                    title="activated-1plus-open-sequence",
                    status="live",
                    audience_name="Turbo_Addons | Deactivate_2500",
                    audience_count=7062,
                    product="Turbo Add-ons - WordPress speed plugin",
                    offer="Native pattern-interrupt: stack question → speed proof → 30% dev credit reactivation",
                    sender_name="Himel from Turbo Add-ons",
                    sender_email="hello@turbo-addons.com",
                    sequence_type="7-stage lifecycle · behavior branches · exit on purchase",
                    launched_at="2026-08-30",
                    open_rate=11.9,
                    click_rate=1.4,
                    delivery_rate=96.8,
                    sequence_json=_stages(),
                    emails_json=_emails(),
                    qa_json=_qa(),
                    gates_json=_gates(),
                ),
                models.Campaign(
                    slug="header-footer-upgrade-sequence",
                    title="header-footer-upgrade-sequence",
                    status="scheduled",
                    audience_name="Turbo_Addons | Header/Footer users",
                    audience_count=4120,
                    product="Turbo Add-ons - Header & Footer",
                    offer="Header/Footer upgrade path",
                    sender_name="Himel from Turbo Add-ons",
                    sender_email="hello@turbo-addons.com",
                    sequence_type="4-stage lifecycle",
                    launched_at="2026-08-28",
                    open_rate=None,
                    click_rate=None,
                    delivery_rate=None,
                    sequence_json=_stages(),
                    emails_json=_emails(),
                    qa_json=_qa(),
                    gates_json=_gates(),
                ),
                models.Campaign(
                    slug="travilo-demo-16aug-sequence",
                    title="travilo-demo-16aug-sequence",
                    status="exited",
                    audience_name="Travilo demo signups",
                    audience_count=1982,
                    product="Travilo demo",
                    offer="Demo walkthrough",
                    sender_name="Himel from Turbo Add-ons",
                    sender_email="hello@turbo-addons.com",
                    sequence_type="3-stage demo sequence",
                    launched_at="2026-08-27",
                    open_rate=13.4,
                    click_rate=1.1,
                    delivery_rate=97.1,
                    sequence_json=_stages(),
                    emails_json=_emails(),
                    qa_json=_qa(),
                    gates_json=_gates(),
                ),
                models.Campaign(
                    slug="e3-e7-scheduled",
                    title="e3-e7-scheduled",
                    status="sent",
                    audience_name="Turbo_Addons | Deactivate_2500",
                    audience_count=6500,
                    product="Turbo Add-ons",
                    offer="Stages 3-7 dispatch",
                    sender_name="Himel from Turbo Add-ons",
                    sender_email="hello@turbo-addons.com",
                    sequence_type="7-stage lifecycle",
                    launched_at="2026-08-27",
                    open_rate=10.2,
                    click_rate=0.9,
                    delivery_rate=96.2,
                    sequence_json=_stages(),
                    emails_json=_emails(),
                    qa_json=_qa(),
                    gates_json=_gates(),
                ),
            ])

        if db.query(models.Job).count() == 0:
            db.add_all([
                models.Job(job_type="report", title="Daily report · dispatched", detail="Email sent to 4 recipients · sheet updated", status="done", created_at="10:00"),
                models.Job(job_type="sync", title="Reporting sheet sync", detail="09:50 daily update · complete", status="done", created_at="09:50"),
                models.Job(job_type="dispatch", title="Sequence dispatch · stage 2 → branch", detail="Unopened vs opened · behavior-filtered", status="done", created_at="09:45"),
                models.Job(job_type="qa", title="Campaign QA · 14 checks", detail="Brand, fact, link, deliverability, compliance", status="done", created_at="08:12"),
                models.Job(job_type="warning", title="Bounce near threshold", detail="1.9% vs max 2% · watch flagged", status="done", created_at="07:30"),
            ])

        db.commit()
        print("Seed complete")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
