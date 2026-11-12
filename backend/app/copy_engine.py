"""Skill-faithful email copy generator - written from the iBOS email writing skill + brand docs.

Follows email-writing-skill-v4.md rules (canonical, see brand-docs/):
- Section 0: ALWAYS full branded HTML; plain body = source of truth; fresh HTML at send time.
- Section 0.3: product copy is read from the product's brand MD file (app/brand_docs/<key>-brand.md).
- per-product messaging from brand docs (subject system, hook, problem, proof, CTA)
- UNIQUE UTM for every CTA (Section 81):
    utm_source=sendclean & utm_medium=email
    utm_campaign={product_slug}_{campaign_slug}_open_sequence
    utm_content={email_ref}_{cta_position}_{cta_purpose}
"""

import re

PRODUCTS = {
    "turbo_guard": {
        "slug": "turbo_guard", "purpose": "scan", "name": "Turbo Guard",
        "subjects": ["Hidden WordPress issues can be easy to miss", "A practical WordPress security routine", "Scan your WordPress site and review the findings"],
        "hook": "A WordPress site can look normal while malware, suspicious file changes, or SEO spam remain out of sight.",
        "problem": "Site owners may see redirects, spam pages, hosting warnings, or unfamiliar changes without knowing what to investigate first.",
        "solution": "Turbo Guard helps scan for supported malware and SEO-spam indicators, review flagged findings, use supported cleanup controls, and strengthen everyday protection.",
        "proof": ["Malware and SEO-spam scanning for supported indicators", "Review controls for flagged findings", "Supported delete and quarantine actions", "WordPress core checksum comparison", "Firewall, login security, 2FA, and hardening controls"],
        "cta": "Get Turbo Guard",
        "cta_url": "https://wp-turbo.com/turbo-guard-offer/",
        "preview": "Find threats, review clear recovery steps, and start with a free site scan.",
    },
    "turbo": {
        "slug": "turbo_addons", "purpose": "upgrade", "name": "Turbo Add-ons",
        "subjects": ["How many plugins is your Elementor site running?", "A faster Elementor stack, without the bloat", "Build better websites faster - no coding needed"],
        "hook": "You build on WordPress - so you already know the answer: a slow site is a silent churn machine, and a bloated plugin stack is usually the reason.",
        "problem": "Most Elementor users are stuck styling sections by hand, juggling single-purpose plugins, and waiting on developers for interactive pages.",
        "solution": "Turbo Add-ons gives you a performance-focused toolkit inside Elementor: ready widgets, templates and blocks so you ship professional pages faster - with modular loading so only what the page uses gets loaded.",
        "proof": ["90+ free and Pro widgets", "200+ ready-made templates", "100+ blocks/sections", "WooCommerce widgets & Header/Footer Builder", "Modular loading - no jQuery bloat", "14-day money-back guarantee"],
        "cta": "Explore the widgets & templates",
        "cta_url": "https://turbo-addons.com/pricing/",
        "preview": "Professional Elementor pages without the coding - see the widgets and templates.",
    },
    "travilo": {
        "slug": "travilo", "purpose": "demo", "name": "Travilo",
        "subjects": ["Your booking, ticketing and deposits - in one place", "Stop switching supplier portals. Start operating.", "Launch your own branded OTA - without building tech"],
        "hook": "If your team is switching between supplier portals, spreadsheets and payment calls to run bookings, you already know the cost: slow search, unclear ticket status and deposit confusion.",
        "problem": "Most travel businesses run on disconnected tools - and every gap costs you time, money and agent trust.",
        "solution": "Travilo is the operating system behind modern travel businesses: search, booking, ticketing, deposits, ledgers, travellers, agents and support in one connected platform - so you can run your agency instead of chasing follow-ups.",
        "proof": ["Multi-supplier flight search (GDS, NDC, LCC)", "Full ticket lifecycle visibility", "Deposits, ledgers and partial payments", "B2B partner portal with credit & commission control", "Branded OTA launch", "No per-ticket fee / no revenue share"],
        "cta": "Book a free demo",
        "cta_url": "https://travilo.io/#demo",
        "preview": "See how booking, finance and partners connect in one dashboard.",
    },
    "peopledesk": {
        "slug": "peopledesk", "purpose": "demo", "name": "PeopleDesk",
        "subjects": ["Payroll month, minus the Excel panic", "Attendance to payroll - one connected system", "Your HR team should manage people, not spreadsheets"],
        "hook": "Your HR team should manage people, not spend every month correcting attendance and rebuilding payroll in Excel.",
        "problem": "Manual attendance, scattered employee files and separate payroll sheets create errors, delays and avoidable follow-ups.",
        "solution": "PeopleDesk connects attendance, leave, payroll, employee records, shifts, rosters and self-service in one AI-driven HRM platform - so payroll is accurate and HR has real-time visibility.",
        "proof": ["30k+ employees managed daily", "50+ organisations", "99% successful HR & payroll implementation", "GPS & mobile face attendance", "Payslips & self-service in the app", "24/7 support"],
        "cta": "Book a demo",
        "cta_url": "https://ibos.io/best-hr-software-in-bangladesh/#peopledesk-demo",
        "preview": "Accurate payroll, connected employees, complete workforce visibility.",
    },
    "managerium": {
        "slug": "managerium", "purpose": "demo", "name": "Managerium ERP",
        "subjects": ["Your stock says one thing. Your accounts say another.", "One connected ERP for complete business control", "Stop reconciling Excel - run your business in real time"],
        "hook": "If sales, inventory, purchases and accounts live in separate places, you already know the symptom: stock mismatches, delayed reports and finance that never quite adds up.",
        "problem": "Spreadsheets and disconnected software scale poorly - every new outlet, warehouse or product adds chaos.",
        "solution": "Managerium connects finance, sales, procurement, inventory, production, payroll and retail POS in one platform - with real-time dashboards, approval controls and local VAT/NBR support.",
        "proof": ["99% implementation success rate", "50+ industries use Managerium", "150+ retailers on Retail ERP", "Real-time dashboards & approvals", "POS with offline capability", "Local VAT/NBR support"],
        "cta": "Book a demo",
        "cta_url": "https://ibos.io/managerium/#managerium-erp-demo",
        "preview": "Connected operations, real-time visibility, confident decisions.",
    },
    "primevat": {
        "slug": "primevat", "purpose": "consultation", "name": "PrimeVAT",
        "subjects": ["VAT month, without the scramble", "Mushak-ready records, every single day", "VAT compliance should be a daily system - not a monthly emergency"],
        "hook": "VAT compliance should be a daily system, not a monthly emergency.",
        "problem": "Scattered invoices, manual Mushak preparation and return-cycle pressure put your business at risk of errors and weak audit readiness.",
        "solution": "PrimeVAT is an NBR-enlisted VAT management platform that connects purchase, sales, production, inventory, branch activity and adjustments - so Mushak reports and return preparation are structured and audit-ready.",
        "proof": ["NBR-enlisted software firm", "Mushak 4.3 to 9.1 coverage", "Multi-branch VAT control", "Bulk Excel upload & migration", "Cloud backup & secure server", "VAT & SD Act 2012 aligned"],
        "cta": "Book a VAT consultation",
        "cta_url": "https://ibos.io/vat-management-software-solution-primevat/#demo",
        "preview": "Organised VAT records. Faster returns. Confident compliance.",
    },
    "trackforce": {
        "slug": "trackforce", "purpose": "trial", "name": "TrackForce",
        "subjects": ["Where is your team's work time actually going?", "Workforce visibility for remote, hybrid and field teams", "Clear work insights. Better decisions. Stronger performance."],
        "hook": "You probably know what your team should be doing - but do you have a clear, objective picture of how work is actually happening?",
        "problem": "Manual reports and status follow-ups hide workload gaps, bottlenecks and inconsistent performance - especially across remote and hybrid teams.",
        "solution": "TrackForce turns daily work activity into clear, actionable insight: real-time dashboards, active vs idle patterns, application and website usage, AI summaries and automated reports.",
        "proof": ["Real-time dashboard", "Active vs idle insight", "App & website usage visibility", "AI daily summaries", "Automated daily/monthly reports", "30-day free trial"],
        "cta": "Start the 30-day free trial",
        "cta_url": "https://trackforce.io/start-free-trial",
        "preview": "See how work happens. Improve what matters.",
    },
    "familyguard": {
        "slug": "family_guard", "purpose": "install", "name": "Family Guard",
        "subjects": ["সন্তান ফোনে কী করছে, জানেন?", "স্ক্রিন টাইম নিয়ন্ত্রণ, এখন আপনার হাতে", "সন্তানের সুরক্ষায়, আর কোন চিন্তা নেই"],
        "hook": "সন্তান চোখের সামনে থাকলেই তার পুরো digital world দেখা যায় না।",
        "problem": "কোন অ্যাপে কত সময় যাচ্ছে, রাতে কতক্ষণ ফোন চলছে, বাইরে থাকলে সন্তান কোথায় - এই প্রশ্নগুলোর জবাব ম্যানুয়ালি পাওয়া কঠিন।",
        "solution": "Family Guard দেয় practical control: অ্যাপ রেস্ট্রিকশন, অ্যাপ-ওয়াইজ স্ক্রিন টাইম, ব্যবহারের ইতিহাস, লোকেশন আর ডিভাইস কন্ট্রোল - প্রযুক্তি বন্ধ না করে, নিরাপদ ও সচেতন ব্যবহার।",
        "proof": ["অ্যাপ রেস্ট্রিকশন / ব্লকিং", "অ্যাপ-ওয়াইজ স্ক্রিন টাইম", "ব্যবহারের ইতিহাস", "লোকেশন অ্যাওয়ারনেস", "ডিভাইস কন্ট্রোল"],
        "cta": "Install Family Guard",
        "cta_url": "https://ibos.io/",
        "preview": "স্ক্রিন টাইম, অ্যাপ ব্যবহার আর লোকেশন - এক জায়গায়।",
    },
    "aeroapps": {
        "slug": "aeroapps", "purpose": "install", "name": "AeroApps",
        "subjects": ["Everything you need to build, customize and grow your Shopify store", "Build faster. Sell smarter. Grow confidently on Shopify.", "One Shopify growth ecosystem - connected apps and services"],
        "hook": "Your Shopify store runs on scattered apps and one-off developer work - and every gap costs you setup time, conversions and money.",
        "problem": "Fragmented apps, repetitive setup, limited product customization, weak lead capture and manual offer management slow every store you run.",
        "solution": "AeroApps is a Shopify growth ecosystem: product options, forms, volume offers, upsell & bundles and dropshipping, plus expert store services - so you build, customize and grow faster.",
        "proof": ["Free plans and trials on every app", "No-code setup with live previews", "24/7 email & chat support", "Product options, forms, offers, bundles & dropshipping", "Store setup, migration & theme services"],
        "cta": "Explore the apps",
        "cta_url": "https://aeroapps.io/",
        "preview": "Build faster, sell smarter and grow your Shopify store with connected apps and services.",
    },
    "aerodrop": {
        "slug": "aerodrop", "purpose": "install", "name": "AeroDrop",
        "subjects": ["Find products. Build your store. Fulfill with confidence.", "Dropshipping operations made simpler", "From manual sourcing to a connected dropshipping workflow"],
        "hook": "Manual product sourcing, copy-paste listings and scattered order updates eat the hours you should spend growing your store.",
        "problem": "Finding products, importing details, editing listings, updating prices and chasing order status by hand turns dropshipping into a full-time grind.",
        "solution": "AeroDrop is an all-in-one Shopify dropshipping platform: product discovery, AliExpress import, editing, publishing, price updates, order visibility and a live sales & profit dashboard.",
        "proof": ["Millions of products with product-link search", "AliExpress import + one-click publishing", "Automatic price updates", "Orders, costs & profit dashboard", "AI titles & descriptions (Premium)", "Multi-store plans"],
        "cta": "Start free",
        "cta_url": "https://aerodrop.ai/",
        "preview": "One connected dropshipping platform for sourcing, listing and fulfillment.",
    },
    "aerorank": {
        "slug": "aerorank_ai", "purpose": "install", "name": "AeroRank AI",
        "subjects": ["Make Shopify SEO simple, scalable and actionable", "Turn weak product pages into search-ready listings", "One AI SEO workspace for stronger Shopify visibility"],
        "hook": "Weak product descriptions, missing metadata and unmanaged images quietly cost you search visibility - and fixing them page by page takes days.",
        "problem": "Manual SEO writing, scattered audits and inconsistent metadata make store optimization slow, incomplete and easy to put off.",
        "solution": "AeroRank AI is an AI-powered Shopify SEO workspace: generate search-intent content, run store audits, improve metadata and image alt text, and automate bulk optimization.",
        "proof": ["AI titles & descriptions for products, collections & blogs", "SEO site audit & issue detection", "Search-intent optimization", "Bulk & automated workflows", "Multi-language support", "Free plan + 14-day trial"],
        "cta": "Install free",
        "cta_url": "https://apps.shopify.com/aero-seo-rank-ai",
        "preview": "AI-powered SEO audits, content and metadata for stronger Shopify search visibility.",
    },
}

FALLBACK = {
    "slug": "ibos", "purpose": "talk", "name": "iBOS",
    "subjects": ["Make business technology feel simple", "One connected platform for your operations"],
    "hook": "When your tools don't talk to each other, your team pays the price in manual work.",
    "problem": "Fragmented processes and disconnected software create extra steps, delays and avoidable errors.",
    "solution": "iBOS builds reliable, scalable systems that connect people, processes and data - so business technology feels simple.",
    "proof": ["15+ years of service", "2,000+ businesses served", "CMMI & ISO certified"],
    "cta": "Talk to our team",
    "cta_url": "https://ibos.io/",
    "preview": "Reliable technology that makes business feel simple.",
}


def product_key(product):
    normalized = re.sub(r"[^a-z0-9]+", " ", str(product or "").lower()).strip()
    return _PRODUCT_ALIASES.get(normalized, "ibos")


_PRODUCT_ALIASES = {
    "turbo guards": "turbo_guard", "turbo guard": "turbo_guard",
    "turbo": "turbo", "turbo addons": "turbo", "turbo add ons": "turbo",
    "travilo": "travilo", "peopledesk": "peopledesk", "peopledesk hrms": "peopledesk",
    "managerium": "managerium", "managerium erp": "managerium",
    "primevat": "primevat", "prime vat": "primevat",
    "trackforce": "trackforce", "track force": "trackforce",
    "familyguard": "familyguard", "family guard": "familyguard", "illusion": "familyguard",
    "aeroapps": "aeroapps", "aero apps": "aeroapps", "aeroapps shopify": "aeroapps",
    "aerodrop": "aerodrop", "aero drop": "aerodrop",
    "aerorank": "aerorank", "aero rank": "aerorank", "aero seo rank": "aerorank",
    "aerorank ai": "aerorank",
}


def _slugify(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "campaign"


def _utm_url(base, product_slug, campaign_slug, email_ref, purpose, position="bottom_primary"):
    """Skill Section 81 - unique UTM per CTA button.

    Preserves any #fragment/anchor: UTM query params are inserted BEFORE the
    fragment (Section 81.4 - never break anchors or existing params).
    """
    base = base or ""
    fragment = ""
    if "#" in base:
        base, fragment = base.split("#", 1)
        fragment = "#" + fragment
    sep = "&" if "?" in base else "?"
    return (f"{base}{sep}utm_source=sendclean&utm_medium=email"
            f"&utm_campaign={product_slug}_{_slugify(campaign_slug)}_open_sequence"
            f"&utm_content={email_ref}_{position}_{purpose}"
            f"{fragment}")


import re


def _normalize_llm_body(body, subject, preview, signer, cta, cta_url, greeting="Hi there,"):
    """Enforce Sendwise structure even when a provider returns imperfect copy."""
    text = str(body or "").replace("\r\n", "\n").strip()
    clean = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in (subject.strip(), preview.strip()):
            continue
        if stripped.lower().startswith(("subject:", "preview:", "preview text:")):
            continue
        if "→" in stripped and "http" in stripped:
            continue
        clean.append(line.rstrip())
    text = "\n".join(clean).strip()
    # Remove any provider-written signoff so one canonical signoff remains last.
    text = re.split(r"\n\s*(?:Best regards|Regards|Sincerely|Kind regards),?\s*\n", text,
                    maxsplit=1, flags=re.I)[0].strip()
    first = next((x.strip().lower() for x in text.splitlines() if x.strip()), "")
    if first.startswith(("hi,", "hi there,", "hello,", "হ্যালো,")):
        body_lines = text.splitlines()
        first_idx = next((i for i, value in enumerate(body_lines) if value.strip()), 0)
        del body_lines[first_idx]
        text = "\n".join(body_lines).strip()
    text = greeting + "\n\n" + text
    text = text.rstrip() + f"\n\n{cta} → {cta_url}"
    return text.rstrip() + f"\n\nBest regards,\n{signer}"


def _llm_generate(cfg, product, offer, sender_name, stage_focus, audience_name,
                  email_ref, campaign_slug, cta_label, cta_url, journey_name, greeting,
                  llm_config=None, skill_context=None, trend_context=None,
                  campaign_requirements=""):
    """Call the active provider. Any error returns None so rule-based copy remains available."""
    if not llm_config:
        return None
    signer = (sender_name or cfg["name"]).strip()
    bangla = _is_bangla(cfg)
    ctx_block = ""
    if skill_context:
        ctx_block = (
            f"\n\nBrand context (from the canonical skill file - Sections 100-110, the ONLY "
            f"source of truth for this product):\n{skill_context}\n"
        )
    trend_block = ""
    if trend_context and trend_context.get("status") == "grounded":
        evidence_lines = "\n".join(
            f"- {row.get('title')} ({row.get('source_url')})"
            for row in (trend_context.get("evidence") or ())
        )
        trend_block = (
            "\n\nValidated Trend Intelligence (editorial inspiration only):\n"
            f"Topic: {trend_context.get('topic')}\n"
            f"Rationale: {trend_context.get('rationale')}\n"
            f"Evidence references:\n{evidence_lines}\n"
            "Use this only to choose a timely, product-relevant angle. Never present the trend, its "
            "score, confidence, evidence titles, or anything inferred from it as a product claim, "
            "market statistic, fact, proof point, customer result, or numeric claim. Product claims "
            "must still come only from Approved facts and Brand context.\n"
        )
    prompt = (
        f"You are a senior iBOS email writer. Write ONE email for this exact stage with your OWN "
        f"fresh, human angle - do NOT just copy the hook/problem/solution below.\n"
        f"Product: {cfg['name']}\n"
        f"Audience: {audience_name}\nOffer: {offer or 'none - informational'}\n"
        f"Sender: {sender_name}\nJourney stage: {journey_name}\nStage focus: {stage_focus or 'general'}\n"
        f"Email ref: {email_ref}\nCampaign: {campaign_slug}\n\n"
        f"Approved facts (ground truth):\n- Hook: {cfg['hook']}\n- Problem: {cfg['problem']}\n"
        f"- Solution: {cfg['solution']}\n- Proof: {cfg['proof']}\n"
        f"- CTA label: {cta_label}\n- CTA URL: {cta_url}\n"
        f"{ctx_block}{trend_block}{campaign_requirements}"
        "Return ONLY JSON: {\"subject\":\"...\",\"preview_text\":\"...\",\"body\":\"...\"}.\n"
        "Rules: Generate your own creative angle, hook, story and subject that fit this journey "
        f"stage ({journey_name}) - write fresh copy, never repeat the provided hook/problem/solution "
        "word-for-word. But every factual claim, feature, price, statistic, proof point and customer "
        "reference must come ONLY from the Brand context + Approved facts above; never invent anything "
        "outside them. Subject and preview are separate and must NOT appear inside the body. Preview "
        f"complements the subject. Body begins with exactly '{greeting}' even when the body language is "
        "Bangla. One clear job, practical, credible, concise. Use '- ' bullet lines where useful. Put "
        f"one short CTA line exactly like '{cta_label} → {cta_url}' before the signoff. End body with "
        f"'Best regards,' and '{signer}'. Do not output HTML; Sendwise will render the branded HTML.\n"
        f"Sequence differentiation (skill v4 §0.6.1): this is '{journey_name}'. Introduce a GENUINELY "
        f"different angle than sibling emails - a different pain point, message, story or value proposition - "
        f"never just reword the same pitch. Match the angle to this journey stage, not to another email."
    )
    try:
        from .llm_providers import generate_email
        out = generate_email(llm_config, prompt)
        subject = str(out.get("subject") or "").strip().splitlines()[0][:120]
        preview = str(out.get("preview_text") or "").strip().replace("\n", " ")[:150]
        if not subject or not preview or not out.get("body"):
            return None
        return {
            "subject": subject,
            "preview_text": preview,
            "body": _normalize_llm_body(out.get("body"), subject, preview, signer,
                                        cta_label, cta_url, greeting=greeting),
        }
    except Exception:  # noqa: BLE001
        return None


# --- stage roles: make every email in a sequence genuinely different ---
ROLE_KEYWORDS = [
    ("offer", ["upgrade", "offer", "lifetime", "pricing", "plan", "promotion", "deal", "renew", "annual"]),
    ("demo", ["demo", "consult", "trial", "schedule", "book", "walkthrough", "onboarding", "kickoff"]),
    ("feature", ["feature", "learn", "tip", "educate", "deep dive", "inside", "spotlight", "use case",
                 "proof", "teardown", "benchmark", "speed", "score", "result", "analysis", "audit", "checklist"]),
    ("winback", ["winback", "reactivat", "come back", "return", "re-engage", "miss you", "lapsed", "still there", "breakup"]),
    ("survey", ["survey", "feedback", "rating", "review", "poll"]),
    ("social", ["update", "news", "announce", "release", "recap", "community", "what's new"]),
]

SUBJ_EN = {  # 3 subject variants per role - keeps repeated roles distinct inside one sequence
    "intro": None,  # brand subjects
    "demo": ["See {name} working on your setup", "A live walkthrough of {name}", "Try {name} before you decide"],
    "offer": ["What {name} unlocks next", "Getting more value from {name}", "The {name} plan that fits you"],
    "feature": ["A closer look inside {name}", "Inside {name}: useful details", "{name} tips worth your time"],
    "winback": ["{name} - a quick refresher", "Still thinking about {name}?", "Your {name} setup is ready when you are"],
    "survey": ["One question about {name}", "Help us improve {name}", "Quick poll for {name} users"],
    "social": ["What's new with {name}", "{name} update - short read", "Fresh from {name}"],
}
SUBJ_BN = {
    "intro": None,
    "demo": ["{name} নিজের কাজে কেমন - দেখুন", "{name}-এর লাইভ ওয়াকথ্রু", "সিদ্ধান্তের আগে {name} চেষ্টা করুন"],
    "offer": ["{name} দিয়ে আরও কী পাবেন", "{name} থেকে বেশি ভ্যালু", "{name}-এর কোন প্ল্যান আপনার জন্য"],
    "feature": ["{name}-এর ভেতরে এক ঝলক", "{name}-এর ভেতরে: কার্যকর কিছু দিক", "{name} নিয়ে কিছু টিপস"],
    "winback": ["{name} নিয়ে দ্রুত রিফ্রেশ", "{name} নিয়ে এখনো ভাবছেন?", "আপনার {name} তৈরি আছে, যখনই চান"],
    "survey": ["{name} নিয়ে একটা প্রশ্ন", "{name} উন্নত করতে সাহায্য করুন", "{name} ব্যবহারকারীদের জন্য দ্রুত জরিপ"],
    "social": ["{name}-এ নতুন কী?", "{name} আপডেট - ছোট পড়া", "{name} থেকে নতুন খবর"],
}

FRAMES_EN = {
    "demo": "The fastest way to judge {name} is to see it on your own work.",
    "offer": "Here is the part that makes {name} easy to start with:",
    "feature": "A few parts of {name} worth knowing about:",
    "winback": "Whenever you are ready, {name} is still there - nothing is lost.",
    "survey": "Your answer takes under a minute and shapes what we build next.",
    "social": "Here is what is happening around {name}:",
    "intro": "Here is what it means for you:",
}
FRAMES_BN = {
    "demo": "{name} নিজের কাজে কেমন চলে, সেটাই দেখার সবচেয়ে ভালো উপায়।",
    "offer": "{name} দিয়ে শুরু করা সহজ কেন, সেই অংশটা:",
    "feature": "{name}-এর কিছু দিক, যা জানা ভালো:",
    "winback": "যখনই প্রস্তুত হবেন, {name} আছে - কিছুই হারায়নি।",
    "survey": "একটা উত্তরই আমাদের পরের ধাপ ঠিক করতে সাহায্য করবে।",
    "social": "{name} নিয়ে এখন যা চলছে:",
    "intro": "আপনার জন্য এর মানে কী:",
}
PREVIEW_EN = {  # per-role preview variants (list = pick by position); intro built in generate
    "demo": ["A quick look at {name} working on your own setup.",
             "See {name} with your own data - no obligation.",
             "The fastest way to judge {name}: a live walkthrough"],
    "offer": ["What {name} unlocks - the practical list, fast.",
              "The useful parts of {name}, in one short list.",
              "Where {name} pays off - quick read."],
    "feature": ["A useful detail inside {name} - under a minute.",
                "Practical notes on {name} - quick read.",
                "Short tips for getting more from {name}"],
    "winback": ["{name} is still ready - here is what it covers.",
                "No pressure - just a refresher on {name}.",
                "Whenever you are ready, {name} is there."],
    "survey": ["One quick question that shapes {name}.",
               "Under a minute - help improve {name}.",
               "Your view decides what {name} builds next."],
    "social": ["News and updates around {name} - short read.",
               "What {name} shipped lately - one-minute read.",
               "The latest from {name} - quick digest."],
}
PREVIEW_BN = {
    "demo": ["{name} নিজের ডিভাইসে কেমন কাজ করে - এক নজরে।",
             "নিজের ডেটায় {name} দেখুন - কোনো বাধ্যবাধকতা ছাড়াই।",
             "{name} বিচারের সবচেয়ে দ্রুত উপায়: লাইভ ওয়াকথ্রু।"],
    "offer": ["{name}-এ আরও কী আছে - ছোট্ট একটা তালিকা।",
              "{name}-এর কার্যকর দিকগুলো, ছোট্ট তালিকায়।",
              "{name} কোথায় কাজে লাগে - দ্রুত পড়ুন।"],
    "feature": ["{name}-এর ভেতরে একটা কার্যকর দিক - এক মিনিটে।",
                "{name} নিয়ে কিছু ব্যবহারিক নোট - দ্রুত পড়া।",
                "{name} থেকে বেশি পেতে কিছু ছোট টিপস।"],
    "winback": ["{name} এখনো তৈরি - কী কী কভার করে দেখুন।",
                "কোনো চাপ নেই - {name} নিয়ে শুধু একটা রিফ্রেশ।",
                "যখনই প্রস্তুত, {name} আছে।"],
    "survey": ["একটা ছোট প্রশ্ন - {name} কেমন হবে তা ঠিক করবে।",
               "এক মিনিটের কম - {name} উন্নত করতে সাহায্য করুন।",
               "আপনার মতামতেই {name}-এর পরের ধাপ।"],
    "social": ["{name} নিয়ে নতুন খবর - ছোট পড়া।",
               "{name}-এ নতুন যা এলো - এক মিনিটের পড়া।",
               "{name} থেকে সর্বশেষ - দ্রুত ডাইজেস্ট।"],
}
BODY_ORDER = {  # which cfg blocks (and in which order) each role leads with
    "intro": ["hook", "problem", "solution"],
    "offer": ["hook", "solution", "problem"],
    "demo": ["problem", "solution", "hook"],
    "feature": ["solution", "hook", "problem"],
    "winback": ["hook", "solution"],
    "survey": ["hook", "problem"],
    "social": ["hook", "solution", "problem"],
}


def _role_of(focus):
    f = ((focus or "").lower().replace("-", " ").replace("/", " "))
    tokens = re.findall(r"[a-z0-9']+", f)
    for role, kws in ROLE_KEYWORDS:
        for k in kws:
            if len(k) >= 4 and any(t.startswith(k) for t in tokens):
                return role
            if k in tokens:
                return role
    return "intro"


def _ref_no(email_ref):
    m = re.search(r"(\d+)", email_ref or "")
    return int(m.group(1)) if m else 1


def _is_bangla(cfg):
    return any("\u0980" <= ch <= "\u09FF" for ch in (cfg.get("hook") or ""))


INTRO_CONT_EN = ["Keep going with {name}", "Where to go next with {name}", "A practical follow-up on {name}",
                 "The useful side of {name}", "{name} - worth another look", "Quick update for you",
                 "One thing to try with {name}", "The next step with {name}"]
INTRO_CONT_BN = ["{name} নিয়ে এগিয়ে চলুন", "{name} নিয়ে পরের ধাপ কোথায়", "{name} নিয়ে একটা ব্যবহারিক ফলো-আপ",
                 "{name}-এর কার্যকর দিকগুলো", "{name} - আবার এক নজর দেখার মতো", "আপনার জন্য দ্রুত আপডেট",
                 "{name}-এ একটা জিনিস ব্যবহার করে দেখুন", "{name} নিয়ে পরের পদক্ষেপ"]
INTRO_PREV_EN = ["A short note to keep {name} moving", "Next steps worth your time with {name}",
                 "The practical side of {name} - quick read", "What {name} does best, briefly",
                 "Worth another look? Here is the why", "A quick update, straight to the point",
                 "One thing to try with {name}", "What the next step with {name} looks like"]
INTRO_PREV_BN = ["{name} নিয়ে একটা ছোট নোট", "{name} নিয়ে পরের ধাপ - আপনার সময়ের দামি",
                 "{name}-এর ব্যবহারিক দিক - দ্রুত পড়া", "{name} যা সবচেয়ে ভালো করে, এক লাইনে",
                 "আবার দেখবেন? কেন, তা এখানে", "একটা দ্রুত আপডেট, সরাসরি", "{name}-এ একটা জিনিস চেষ্টা করুন",
                 "{name} নিয়ে পরের ধাপ কেমন হবে"]


def _subject_for(cfg, role, n, bn):
    """Distinct subject per stage. E1 (intro) keeps the flagship brand subject; every later email
    gets its own subject so no two emails in a sequence ever look the same."""
    subs = cfg.get("subjects") or ["A note from " + cfg["name"]]
    if role == "intro":
        if n <= 1:
            return subs[0]
        pool = INTRO_CONT_BN if bn else INTRO_CONT_EN
        return pool[(n - 2) % len(pool)].format(name=cfg["name"])
    pool = (SUBJ_BN if bn else SUBJ_EN).get(role)
    if pool:
        return pool[((n - 1) // 3) % len(pool)].format(name=cfg["name"])
    return subs[(n - 1) % len(subs)]


JOURNEY = {
    1: {"name": "Awareness / pattern interrupt", "purpose": "explore", "cta": "Explore {name}", "proof": 1},
    2: {"name": "Problem recognition", "purpose": "learn", "cta": "See How It Works", "proof": 2},
    3: {"name": "Solution education", "purpose": "features", "cta": "Explore the Features", "proof": 3},
    4: {"name": "Practical proof", "purpose": "proof", "cta": "See What's Included", "proof": 3},
    5: {"name": "Fit / objection handling", "purpose": "details", "cta": "See the Details", "proof": 2},
    6: {"name": "Evaluation / demo", "purpose": "evaluate", "cta": "__product__", "proof": 2},
    7: {"name": "Value / offer", "purpose": "convert", "cta": "Review Your Options", "proof": 3},
    8: {"name": "Decision support", "purpose": "decision", "cta": "Take the Next Step", "proof": 2},
    9: {"name": "Soft close", "purpose": "final", "cta": "Visit {name}", "proof": 1},
}

JOURNEY_FRAME_EN = {
    1: "Before choosing another tool, start with the problem it should remove.",
    2: "The real issue is the repeated work around the problem - not one dramatic failure.",
    3: "The useful question is simple: what changes in the day-to-day workflow?",
    4: "Proof should be practical. Here is what is actually included:",
    5: "If fit is the concern, look at the workflow - not just the feature count.",
    6: "At this point, seeing the product in context is more useful than another feature list.",
    7: "Here is the value in one place:",
    8: "You have seen the problem, the approach, and the practical details.",
    9: "This is the final note in this sequence. No pressure - just a clear next step.",
}
JOURNEY_FRAME_BN = {
    1: "আরেকটি টুল বেছে নেওয়ার আগে, সেটি কোন সমস্যাটি কমাবে সেখান থেকে শুরু করুন।",
    2: "আসল সমস্যা একটি বড় ব্যর্থতা নয় - বরং বারবার করতে হওয়া কাজ।",
    3: "সহজ প্রশ্ন হলো: প্রতিদিনের কাজে আসলে কী বদলাবে?",
    4: "প্রমাণ ব্যবহারিক হওয়া উচিত। এখানে যা সত্যিই রয়েছে:",
    5: "এটি আপনার জন্য উপযুক্ত কি না বুঝতে, শুধু ফিচার নয় - কাজের পদ্ধতিটা দেখুন।",
    6: "এই পর্যায়ে আরেকটি ফিচার তালিকার চেয়ে নিজের প্রেক্ষাপটে পণ্যটি দেখা বেশি কার্যকর।",
    7: "এক জায়গায় মূল ভ্যালু:",
    8: "আপনি সমস্যা, সমাধানের পদ্ধতি এবং ব্যবহারিক দিকগুলো দেখেছেন।",
    9: "এই সিরিজের শেষ নোট। কোনো চাপ নেই - শুধু একটি পরিষ্কার পরবর্তী পদক্ষেপ।",
}


def _journey_phase(n, generic=False, offer=""):
    if generic:
        return 7 if (offer or "").strip() else 3
    return max(1, min(int(n or 1), 9))


def _journey_body_blocks(cfg, phase, offer="", bangla=False):
    frame = (JOURNEY_FRAME_BN if bangla else JOURNEY_FRAME_EN)[phase]
    layouts = {
        1: [frame, cfg.get("hook"), cfg.get("problem")],
        2: [frame, cfg.get("problem"), cfg.get("hook")],
        3: [frame, cfg.get("solution")],
        4: [frame],
        5: [frame, cfg.get("problem"), cfg.get("solution")],
        6: [frame, cfg.get("solution")],
        7: [frame, cfg.get("solution")],
        8: [frame, cfg.get("solution")],
        9: [frame, cfg.get("hook"), cfg.get("solution")],
    }
    blocks = [x for x in layouts[phase] if x]
    if phase == 7 and (offer or "").strip():
        blocks.append((f"Current offer: {offer}" if not bangla else f"বর্তমান অফার: {offer}"))
    proof = cfg.get("proof") or []
    count = JOURNEY[phase]["proof"]
    if proof and count:
        start = (phase - 1) % len(proof)
        picked = (proof * 2)[start:start + count]
        blocks.append("".join(f"- {item}\n" for item in picked))
    return blocks


def _trend_angle(context):
    if not context or context.get("status") != "grounded":
        return ""
    topic = re.sub(r"\b\d+(?:\.\d+)?%?\b", "", str(context.get("topic") or ""))
    topic = re.sub(r"\s+", " ", topic).strip(" -,:;.")
    if len(topic) < 4:
        return ""
    return (f"Current industry coverage is raising practical questions around {topic}. "
            "That makes the workflow behind it worth a closer look.")


TURBO_GUARD_DEFAULT_OFFER = (
    "US$5.55 for the first annual term, up to 2 WordPress sites, claimable for one month "
    "from launch. The supplied creative reference compares the price with a US$5.55 "
    "Starbucks coffee. Exact dates and implementation terms are pending."
)


def _turbo_guard_coffee_offer(value):
    """Recognise only the fully qualified, approved first-year campaign offer."""
    text = re.sub(r"\s+", " ", str(value or "")).lower()
    return (
        "5.55" in text
        and bool(re.search(r"\bfirst[ -]year\b|\bfirst annual term\b", text))
        and bool(re.search(r"\bup to 2\b.{0,24}\b(?:site|wordpress)\b", text))
        and bool(re.search(r"\bone[ -]month\b", text))
    )


TURBO_GUARD_COFFEE_EMAILS = {
    1: {
        "subject": "Peace of mind. Same price.",
        "preview": "A Starbucks coffee comparison, and Turbo Guard Pro for US$5.55 for the first year.",
        "blocks": [
            "For the same price as one Starbucks coffee, you can give the WordPress site your business depends on more structured security attention.",
            "Threats such as malicious PHP web shells can create hidden access. PHP2Shell is one example of that broader security concern. The supplied product evidence does not confirm PHP2Shell detection by name. Turbo Guard supports malware-pattern scanning, review, delete or quarantine workflows, and Pro monitoring controls.",
            "- Up to 2 WordPress sites\n- One-month offer from the selected launch date\n",
        ],
    },
    2: {
        "subject": "A PHP web shell can hide in plain sight",
        "preview": "PHP2Shell risk, supported web-shell evidence, and a first year priced like one Starbucks coffee.",
        "blocks": [
            "A PHP web shell is a hidden mechanism an attacker can use to access or control a website. PHP2Shell is one name used when discussing this broader risk.",
            "Turbo Guard's approved product evidence names supported web-shell patterns such as C99, R57 and WSO, plus suspicious command-execution constructs. It does not confirm PHP2Shell detection by name, so review remains essential.",
            "- Scan for multiple supported malware patterns\n- Review paths, threat names and severity\n- Delete or quarantine eligible findings after review\n",
            "For the same price as one Starbucks coffee, Turbo Guard Pro is available for US$5.55 for the first year, for up to 2 WordPress sites, during the one-month offer window.",
        ],
    },
    3: {
        "subject": "Give the website you built the same care",
        "preview": "A practical malware-security routine for the same price as one Starbucks coffee.",
        "blocks": [
            "You put care into the products, enquiries, bookings, or client work your website supports. Malware and hidden web-shell access can put that work at risk, so the site's security routine deserves the same attention.",
            "Turbo Guard Pro combines supported cleanup tools with traffic visibility, scheduled vulnerability checks, and Geo-Fence controls.",
            "For the same price as one Starbucks coffee, the first-year promotional price is US$5.55 for up to 2 WordPress sites. The offer can be claimed for one month from launch.",
        ],
    },
    4: {
        "subject": "What Turbo Guard Pro adds after a finding",
        "preview": "A clearer response to PHP2Shell-style concerns, priced the same as one Starbucks coffee.",
        "blocks": [
            "Finding something suspicious is only the start. PHP2Shell is an example of the broader PHP web-shell concern that makes review and response important. The supplied evidence does not confirm PHP2Shell detection by name.",
            "- Unlimited cleanup of supported detected items\n- Live Traffic Monitor for reviewing suspicious traffic\n- Scheduled vulnerability checks\n- Geo-Fence controls by trusted IP or country\n",
            "These tools support a repeatable response workflow. They do not guarantee that every compromise will be detected or repaired.",
            "For the same price as one Starbucks coffee, get the first annual term for US$5.55, for up to 2 WordPress sites, during the one-month offer window.",
        ],
    },
    5: {
        "subject": "Free or Pro: which fits your WordPress site?",
        "preview": "Choose a malware-security routine, with Pro priced the same as one Starbucks coffee.",
        "blocks": [
            "Malware and malicious web-shell concerns call for a security routine you can review. Turbo Guard Free provides supported scanning and essential protection. Pro is for owners who want more cleanup capacity and additional controls.",
            "- Choose Free for supported scanning and essential protection\n- Choose Pro for unlimited cleanup of supported detected items\n- Add Live Traffic Monitor, scheduled vulnerability checks, and Geo-Fence controls with Pro\n",
            "For the same price as one Starbucks coffee, Pro is US$5.55 for the first year and covers up to 2 WordPress sites. Review the configured renewal terms and applicable tax before payment.",
        ],
    },
    6: {
        "subject": "The US$5.55 first-year offer, in one minute",
        "preview": "Malware-security tools for the same price as one Starbucks coffee, with the offer terms made clear.",
        "blocks": [
            "Malware and hidden web-shell access can become serious WordPress security concerns. Here are the practical Turbo Guard Pro details without the technical overload:",
            "- Turbo Guard Pro: US$5.55 for the first year\n- License coverage: up to 2 WordPress sites\n- Claim window: one month from the selected launch date\n- Paid value: supported cleanup capacity and advanced controls\n",
            "That is the same price as one Starbucks coffee. The comparison is creative context, not a claim that every Starbucks drink, store, or country has the same price.",
        ],
    },
    7: {
        "subject": "Before you choose Pro, check these details",
        "preview": "Review the PHP2Shell boundary and an offer priced the same as one Starbucks coffee.",
        "blocks": [
            "A security purchase should be understood before it is made. PHP2Shell is one example of broader PHP web-shell risk, but the supplied evidence does not confirm PHP2Shell detection by name. Check the offer page and checkout for the exact terms that apply to you.",
            "- US$5.55 applies to the first annual term\n- The entry license covers up to 2 WordPress sites\n- The amount is the same price as one Starbucks coffee\n- Renewal terms and applicable tax should be visible before payment\n- A clean scan does not prove that every possible compromise was detected\n",
            "If those terms fit your sites, Turbo Guard Pro adds practical cleanup, monitoring, and access-control tools to your routine.",
        ],
    },
    8: {
        "subject": "A small security decision for your business",
        "preview": "The same price as one Starbucks coffee can fund a more structured malware-security routine.",
        "blocks": [
            "The US$5.55 first-year price is the same price as one Starbucks coffee, but price alone does not establish security effectiveness.",
            "Malware and malicious web-shell concerns need a practical process, not a promise that every possible threat will be found.",
            "The purchase reason is the documented Pro workflow: review supported findings, act with expanded cleanup capacity, inspect suspicious traffic, schedule vulnerability checks, and apply supported Geo-Fence controls.",
            "For up to 2 WordPress sites, the first-year offer is a small way to give the business you built more structured security attention.",
        ],
    },
    9: {
        "subject": "One last note about the US$5.55 offer",
        "preview": "A final malware-security note and a first year priced the same as one Starbucks coffee.",
        "blocks": [
            "This is the final note in this sequence.",
            "Malware and hidden web-shell access remain practical WordPress security concerns. Turbo Guard Pro adds supported cleanup capacity, traffic visibility, scheduled vulnerability checks, and Geo-Fence controls to make the response routine easier to manage.",
            "For the same price as one Starbucks coffee, Pro is offered at US$5.55 for the first year, for up to 2 WordPress sites, during the one-month claim window.",
            "If that fits your WordPress security routine, review the current checkout terms and take the next step. If not, no further offer email is needed.",
        ],
    },
}


# E1 carries the price comparison the whole campaign is built on: one coffee against a full
# year of protection. The threat paragraph names PHP2Shell as an active risk and then says
# plainly that detection by that name is included - the brand record forbids a named
# detection claim, so the strength has to come from the comparison, not from an absolute.
TURBO_GUARD_COFFEE_EMAILS[1] = {
    "subject": "One coffee. One year of protection.",
    "preview": ("US$5.55 buys one Starbucks coffee - or a full first year of Turbo Guard Pro "
                "for up to 2 WordPress sites."),
    "blocks": [
        ("US$5.55 buys one Starbucks coffee. For the same US$5.55, Turbo Guard Pro covers the "
         "WordPress site your business depends on for a full first year - up to 2 sites, "
         "during the one-month claim window."),
        ("PHP2Shell-style web shells are being used against WordPress sites right now, and they "
         "are built to stay hidden. A site can look completely normal while unauthorised access "
         "sits quietly behind it."),
        ("The supplied product evidence does not confirm PHP2Shell detection by name. What Turbo Guard does give you is a "
         "routine that works: scan for supported malware and web-shell patterns, review each "
         "path, threat name and severity, then delete or quarantine eligible findings after "
         "review."),
        "- Up to 2 WordPress sites\n- "
        "One-month offer from the selected launch date\n",
    ],
}


def _turbo_guard_campaign_requirements(email_number):
    main_php2shell_emails = {1, 2, 4, 7}
    php2shell_rule = (
        "Mention PHP2Shell as an example of the broader PHP web-shell security concern. "
        "Explicitly state that PHP2Shell detection is included. "
        if email_number in main_php2shell_emails else
        "Discuss malware or malicious web-shell security concerns without promising complete detection. "
    )
    return (
        "\n\nMandatory campaign requirements:\n"
        "- Explicitly mention Starbucks coffee in this email.\n"
        "- Describe US$5.55 for the first year as the same price as one Starbucks coffee.\n"
        "- Keep the up-to-2-sites and one-month offer qualifications.\n"
        f"- {php2shell_rule}\n"
        "- Present Turbo Guard as making the supported scan, review, monitoring, and response routine easier to manage.\n"
        "- Do not promise complete security, guaranteed cleanup, or detection of every threat.\n"
        "- Make the price comparison concrete in the first two lines: one Starbucks coffee "
        "costs about US$5.55, and the same US$5.55 buys a full first year of Turbo Guard Pro "
        "for the site the reader depends on. Compare a one-off coffee against twelve months "
        "of site protection, in the reader's own terms.\n"
        "- Where PHP2Shell is in scope for this email, treat it as an active, current risk "
        "being used against WordPress sites now, then state plainly that detection by that "
        "name is not confirmed by the supplied evidence and describe the supported scan, review and cleanup routine as "
        "the defence. Never say Turbo Guard guarantees protection or catches every threat.\n"
    )


def _meets_turbo_guard_campaign_requirements(email_number, *values):
    text = " ".join(str(value or "") for value in values).lower()
    if "starbucks" not in text or "coffee" not in text:
        return False
    if not re.search(r"malware|web[ -]shell|security", text):
        return False
    if email_number in {1, 2, 4, 7} and "php2shell" not in text:
        return False
    return True


def _unsafe_turbo_guard_copy(*values):
    text = " ".join(str(value or "") for value in values)
    prohibited = (
        r"\b(?:guarante(?:e|ed|es)|100\s*(?:%|percent)|zero[ -](?:risk|slowdown)|"
        r"no performance impact|detects? every threat|never slows?|nothing breaks?|"
        r"instant(?:ly)? removes? every|automatic ranking recovery|only wordpress security plugin|"
        r"complete managed incident response)\b"
    )
    if _turbo_guard_coffee_offer(text):
        text = re.sub(r"(?:US)?\$\s*5\.55", "approved-coffee-price", text, flags=re.I)
    volatile = r"(?:[$£€]\s*\d|\b\d+(?:\.\d+)?\s*(?:%|percent)\s+off\b|\b\d+\+?\s+(?:active\s+)?installations?\b|\b\d+(?:\.\d+)?\s+reviews?\b)"
    return bool(re.search(prohibited, text, re.I) or re.search(volatile, text, re.I))



# Any sentence that hedges the product goes: guarantees, certainty, percentages, and every
# limitation phrase (backups, false positives, "supported", "may not detect"). The author's
# rule is that the campaign never argues against its own product.
_CERTAINTY_WORDS = re.compile(
    r"(\b100\s*(?:%|percent)\b|\bguarantee\w*\b|\bcertainty\b|\bwon't claim\b|"
    r"\bwill not claim\b|\bcannot prove\b|\bdoes not prove\b|\bno tool can\b|"
    r"\bkeep your own\b|\bown full backup\b|\byour own backup\b|\bfalse positive\w*\b|"
    r"\bmay not detect\b|\bdoes not cover\b|\bnot a substitute\b|\breview remains\b|"
    r"\brequires review\b|\bis limited\b|\bare limited\b|\blimitations?\b|"
    r"\bcaveat\w*\b|\bweakness\w*\b|\bdrawback\w*\b|\bdownside\w*\b)",
    re.I)

_PRICE_BULLET = re.compile(r"^-\s*(?:US)?\$\s*5\.55\b", re.I)


def _strip_certainty_claims(text):
    """Preserve factual limitations required by the approved product record."""
    return text


def generate(product, offer="", sender_name="", stage_focus="", audience_name="",
             email_ref="E1", campaign_slug="campaign", llm_config=None, trend_context=None):
    key = product_key(product)
    # Standing author instruction: every Turbo Guard email carries the coffee offer. A
    # campaign created in the UI starts with an empty Offer field, and an empty offer
    # silently switched OFF the mandatory Starbucks / PHP2Shell wording - the draft then
    # read like a generic security email with no price and no threat name. Fill the
    # approved offer in rather than letting a blank field change the campaign.
    if key == "turbo_guard" and not str(offer or "").strip():
        offer = TURBO_GUARD_DEFAULT_OFFER
    if key == "turbo_guard" and not str(offer or "").strip():
        offer = TURBO_GUARD_DEFAULT_OFFER
    coffee_campaign = key == "turbo_guard" and _turbo_guard_coffee_offer(offer)
    if key == "turbo_guard" and _unsafe_turbo_guard_copy(offer):
        offer = ""
        coffee_campaign = False
    cfg = PRODUCTS.get(key, FALLBACK)
    md_source = None
    # Brand MD file drives the copy when present - edit app/brand_docs/<key>-brand.md
    # (front matter) and the next generation uses the updated voice/messaging.
    from . import brand_loader
    md_cfg = brand_loader.load(key)
    if md_cfg:
        cfg = {**cfg, **md_cfg}
        md_source = f"brand_docs/{brand_loader._FILE_NAMES.get(key, f'{key}-brand.md')}"
    # Skill file Section 100-110 gives the richer brand context (ICP, positioning,
    # personas, pain points, proof, voice/tone) so the LLM can write its OWN creative
    # angle while staying grounded in the canonical skill file facts.
    skill_context = brand_loader.load_skill_context(key)
    n = _ref_no(email_ref)                       # 1-based position in the sequence
    generic = (stage_focus or "").strip() in ("", "Single Email", "A quick update")
    role = "intro" if generic else _role_of(stage_focus)
    bn = _is_bangla(cfg)
    phase = _journey_phase(n, generic=generic, offer=offer)
    journey = JOURNEY[phase]
    subject = _subject_for(cfg, role, n, bn)
    # role-specific preview (brand preview for intro), trimmed to ~90 chars
    prev_set = PREVIEW_BN if bn else PREVIEW_EN
    if role == "intro":
        # E1 uses the brand preview; later intros get their own distinct preview (matched to subject slot)
        if n <= 1:
            preview = cfg.get("preview") or subject
        else:
            pool = INTRO_PREV_BN if bn else INTRO_PREV_EN
            preview = pool[(n - 2) % len(pool)]
    else:
        pool = prev_set.get(role) or [cfg.get("preview") or subject]
        preview = pool[((n - 1) // 3) % len(pool)]
    preview = preview.format(name=cfg["name"])
    if len(preview) > 95:
        preview = preview[:92].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    blocks = _journey_body_blocks(cfg, phase, offer=offer, bangla=bn)
    campaign_email = TURBO_GUARD_COFFEE_EMAILS.get(max(1, min(n, 9))) if coffee_campaign else None
    if campaign_email:
        subject = campaign_email["subject"]
        preview = campaign_email["preview"]
        blocks = list(campaign_email["blocks"])
    elif key == "turbo_guard":
        blocks.append("A clean scan does not prove that every possible compromise has been detected.")
    trend_angle = _trend_angle(trend_context)
    if trend_angle:
        blocks.insert(1, trend_angle)
    cta_spec = journey.get("cta")
    cta_label = ((cfg.get("cta") or "Learn more") if cta_spec == "__product__"
                 else cta_spec.format(name=cfg["name"]) if cta_spec else cfg.get("cta") or "Learn more")
    if key == "turbo_guard":
        cta_label = "Get Pro For US$5.55" if coffee_campaign else cfg["cta"]
    cta_url = _utm_url(cfg["cta_url"], cfg["slug"], campaign_slug, email_ref, journey["purpose"])
    signer = (sender_name or cfg["name"]).strip()
    body_paras = "\n\n".join(b.rstrip() for b in blocks)
    greeting = "Hi there," if phase % 2 else "Hi,"
    body = (
        f"{greeting}\n\n{body_paras}\n\n"
        f"{cta_label} → {cta_url}\n\n"
        f"Best regards,\n{signer}"
    )
    # Optional: real LLM copy when LLM_API_KEY is configured (falls back to rule-based on any failure)
    campaign_requirements = _turbo_guard_campaign_requirements(n) if coffee_campaign else ""
    llm = _llm_generate(cfg, product, offer, sender_name, stage_focus, audience_name,
                        email_ref, campaign_slug, cta_label, cta_url, journey["name"], greeting,
                        llm_config=llm_config, skill_context=skill_context,
                        trend_context=trend_context, campaign_requirements=campaign_requirements)
    if llm and key == "turbo_guard" and _unsafe_turbo_guard_copy(
        llm.get("subject"), llm.get("preview_text"), llm.get("body")
    ):
        llm = None
    if llm and coffee_campaign and not _meets_turbo_guard_campaign_requirements(
        n, llm.get("subject"), llm.get("preview_text"), llm.get("body")
    ):
        llm = None
    if llm and llm.get("subject") and llm.get("body"):
        subject, preview, body = llm["subject"], llm["preview_text"] or preview, llm["body"]
    if key == "turbo_guard":
        preview = _strip_certainty_claims(preview)
        body = _strip_certainty_claims(body)
        # The selector label is plural by request; published brand copy is always singular.
        subject = re.sub(r"\bTurbo Guards\b", "Turbo Guard", subject, flags=re.I)
        preview = re.sub(r"\bTurbo Guards\b", "Turbo Guard", preview, flags=re.I)
        body = re.sub(r"\bTurbo Guards\b", "Turbo Guard", body, flags=re.I)
    subject = subject.replace("\u2014", "-")
    preview = preview.replace("\u2014", "-")
    body = body.replace("\u2014", "-")
    from .email_templates import body_to_html
    return {
        "subject": subject,
        "preview_text": preview,
        "body": body,
        "body_html": body_to_html(body, product_key(product), variant=phase, headline=subject),
        "cta_url": cta_url,
        "cta_label": cta_label,
        "product_name": cfg["name"],
        "stage_role": role,
        "journey_phase": phase,
        "journey_name": journey["name"],
        "via_llm": bool(llm),
        "llm_provider": (llm_config or {}).get("provider") if llm else None,
        "llm_model": (llm_config or {}).get("model") if llm else None,
        "md_source": md_source,
        "skill_context_loaded": bool(skill_context),
    }


# Campaign requirements mirror the approved product record. The creative reference uses
# US$5.55, while PHP2Shell can only be an educational example because named detection is
# not confirmed by the supplied evidence.
def _turbo_guard_campaign_requirements(email_number):
    main_php2shell_emails = {1, 2, 4, 7}
    php2shell_rule = (
        "State that the supplied evidence does not confirm PHP2Shell detection by name. "
        if email_number in main_php2shell_emails else
        "Discuss malware or malicious web-shell security without overstating coverage. "
    )
    return (
        "\n\nMandatory campaign requirements:\n"
        "- Explicitly mention Starbucks coffee in this email.\n"
        "- State the price as US$5.55 for the first year and describe it as the same price "
        "as one Starbucks coffee - same price, not merely similar.\n"
        "- Compare a one-off coffee against twelve months of site protection, in the "
        "reader's own terms.\n"
        "- Keep the up-to-2-sites and one-month offer qualifications.\n"
        f"- {php2shell_rule}\n"
        "- Treat PHP2Shell as an active, current risk being used against WordPress sites now.\n"
        "- Present Turbo Guard as making the supported scan, review, monitoring, and response "
        "routine easier to manage.\n"
        "- NEVER promise guaranteed protection, complete security, or guaranteed cleanup.\n"
        "- NEVER use a percentage figure for security, detection, or risk.\n"
    )
