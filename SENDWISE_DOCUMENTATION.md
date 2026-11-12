# Sendwise - Email Marketing Orchestrator (Documentation)

**iBOS / AKIJ Resource · Production System Reference**

> Last updated: 15 September 2026 · Single domain: `https://sendwise.ibos.agency`

---

## 1. Overview

Sendwise is the email-marketing orchestration platform for iBOS (a concern of AKIJ Resource). It manages lifecycle campaigns, cold/outbound, lead generation, and growth communication across all iBOS products. It is a **three-component self-hosted application** with a built-in **Trend Intelligence & Email Agent** that researches real market signals and turns them into email opportunities.

---

## 2. Architecture

| Component | Technology | Port | Container |
|---|---|---|---|
| Frontend | Next.js (App Router) | 3000 | `sendwise-frontend` |
| Backend | FastAPI (Python 3.12) | 8000 | `sendwise-api` |
| Database | PostgreSQL | 5434 | `sendwise-postgres` |

- Hosted on **openship self-hosted** server `ibos-34` (`103.197.205.34`).
- Single domain routing: `/` → frontend, `/api/*` → backend (path-based edge routing).
- No CORS issues (same-origin), no cloud-edge dependency.

### Access

- Dashboard: `https://sendwise.ibos.agency`
- Login: `admin@sendwise.io` / `admin123`
- Backend health: `https://sendwise.ibos.agency/api/health`

---

## 3. LLM / AI Providers

**Current active provider:**

| Field | Value |
|---|---|
| Provider | **OpenAI** |
| Model | **gpt-5** |
| Status | connected ✅ |

**Available providers** (configurable in Settings → AI writing provider):

| Provider | Configured | Default model |
|---|---|---|
| OpenAI | ✅ active | gpt-5 |
| DeepSeek | ❌ not configured | deepseek-chat |
| Google Gemini | ❌ not configured | gemini-2.0-flash |
| Anthropic Claude | ❌ not configured | claude-sonnet-4-5 |
| **Brand MD engine (rule-based)** | ✅ always available | fallback |

- API keys are **encrypted at rest** and never returned by the API.
- If the active provider fails, the system automatically falls back to the **Brand MD engine** (deterministic, no API cost).

---

## 4. Products (11)

| Product | Key | Geography | Notes |
|---|---|---|---|
| Turbo Add-ons | `turbo` | Global | Elementor addon toolkit |
| **Turbo Guard** | `turbo_guard` | Global | WordPress security & malware recovery |
| Travilo | `travilo` | Bangladesh | Travel operating system |
| Managerium ERP | `managerium` | Bangladesh | All-in-one ERP/CRM |
| PeopleDesk HRMS | `peopledesk` | Bangladesh | AI HR & payroll |
| PrimeVAT | `primevat` | Bangladesh | VAT management |
| TrackForce | `trackforce` | Global | Employee monitoring |
| AeroApps (Shopify) | `aeroapps` | Global | Shopify app suite |
| AeroDrop | `aerodrop` | Global | Dropshipping platform |
| Family Guard | `familyguard` | Bangladesh | Parental control |
| iBOS Services | - | - | Broad digital services |

Each product has its own **brand voice, ICP, positioning, competitors, keywords, proof, and claim-governance** from the iBOS email-writing skill (`email-writing-skill-v4.md`, Sections 100–110) and per-product brand MD files.

---

## 5. Core Capabilities

### 5.1 Campaign Management
- Create campaigns with audience mode: **list / saved segment / manual emails**.
- Lifecycle sequences: reusable 9-email branch framework (E1 → E2/E3 → E4–E9) with stage targets.
- Status flow: `draft → approved → ready → scheduled → live → sent / paused / exited`.
- Audience + sender are locked after campaign creation.
- Per-stage open/click targets and adaptive timing.

### 5.2 Email Copywriting
- Two engines: **LLM** (active provider) and **Brand MD engine** (rule-based).
- Grounded in product brand facts (feature, ICP, pain points, proof, voice/tone).
- Unique **UTM tracking** on every CTA button (source/medium/campaign/content/purpose), fragment-safe.
- Branded HTML email system (table-based, inline CSS, email-safe) + plain-text fallback.
- Unsafe-claim guards (block "guarantee", "100%", fabricated stats, volatile pricing).

### 5.3 Trend Intelligence & Email Agent
The flagship capability. Scheduled and on-demand research that converts market signals into email opportunities.

**Workflow:** Business Skill → Competitor Discovery → Data Collection → Trend Detection → Validation → Scoring → Email Strategy → Email Writing → Approval/Send (draft-only by default).

**Data sources (free, keyless):**
- Google Trends (pytrends + daily RSS fallback)
- Reddit (search RSS)
- Google News (RSS, geo-aware)
- Bing Search (RSS) - general web evidence
- Quora (question discovery via Bing RSS)
- Facebook (official Page discovery + identity verification via public search)
- Competitor website/blog RSS autodiscovery (feed + sitemap)

**Intelligence engine:**
- Topic normalization + duplicate clustering
- Cross-source validation (multi-source evidence required)
- Weighted 0–100 trend scoring (relevance 30, momentum 20, cross-source 20, intent 10, competitor 10, freshness 10)
- Low/Medium/High confidence
- Freshness filtering + strict product isolation
- Campaign-fatigue protection (no repeated recent topics)
- Persistent runs, opportunities, evidence, and drafts (PostgreSQL)

**Email action modes:**
- `draft_only` (default - no send)
- `approval_required`
- `auto_send` (disabled unless all gates pass)

**Integration with normal campaigns:** every "Draft with Agent / Generate Copy" first obtains the product-matched Trend Intelligence opportunity and grounds the email angle in it (never as a product claim). Provenance is persisted per email.

### 5.4 Campaign Analytics & Reporting
- Dashboard: KPIs, charts, stage rates, thresholds, launch gates.
- Reports: delivery, clicks, opens (blended/proxy-excluded), bounce, unsubscribe, complaint, revenue.
- Google Sheets reporting (sheet ID + tab) + daily report email.
- Daily schedule: sheet update 09:50, report email 10:00 (Asia/Dhaka).
- Apps Script webhook for sheet auto-update (optional).

### 5.5 Sequence Optimization
- Weekly audit (Sunday 10:00) + audit report (10:15).
- Controlled optimization (Sunday 11:00) when a statistically actionable gap exists.
- One-variable-per-experiment where practical.

### 5.6 Mailchimp - removed
- Mailchimp is no longer present in the codebase. The `mailchimp_client` module, the Autopilot, the Contact
  Cleanup feature, the behavior dispatcher and the Google-Sheet campaign rebuild have been deleted.
- The only remaining reference is a guard in `PATCH /api/settings` that rejects any `mailchimp*` key.
- `campaigns.mailchimp_campaign_id` was renamed to `provider_message_id`.

### 5.7 SendClean (the only provider)
- **Active provider:** Sendwise is SendClean-only; provider selection is not configurable.
- Credential validation uses `POST /v1.0/account/viewUserDetail` with `owner_id` and `token` in the JSON body.
- Endpoints: `GET /api/email-provider/status`, `POST /api/email-provider/test`,
  `GET /api/sendclean/overview`, `GET /api/sendclean/domains/check`,
  `GET /api/sendclean/messages`, `POST /api/sendclean/send`.
- Supported documented capabilities: transactional send, template send, per-message lookup,
  SMTP users, sending domains, tracking domains, webhooks.
- **Not supported and blocked:** audience lists, segments, marketing campaigns, automations,
  per-recipient event streams, aggregate reporting, contact suppression/archiving.
  Unsanctioned operations raise `ProviderCapabilityError` and fail closed - there is no fallback provider.
- "Campaigns" are now send records: an explicit recipient list (max 200), a sender, one subject/body,
  delivered in `direct` or `draft` mode. Each real send stores its SendClean `X-Unique-Id` as
  `provider_message_id` and is traceable via `GET /api/sendclean/messages`.
- API tokens are stored encrypted at rest and redacted from `GET /api/settings`; Settings tests the
  connection before saving.

### 5.8 Scheduling (openship jobs)
| Job | Cadence | Time |
|---|---|---|
| Daily report email | daily | 10:00 Dhaka |
| Trend Intelligence | daily | 08:00 Dhaka |

Removed with Mailchimp: sequence/behavior dispatch (15 min), sheet rebuild (09:50), weekly Mailchimp audit
and the weekly optimization action.

---

## 6. Data Sources Summary (Trend)

| Source | Status | Credential |
|---|---|---|
| Google Trends | ✅ working (rate-limited) | free (pytrends) |
| Reddit | ✅ working (rate-limited) | free (RSS) |
| Google News | ✅ working | free (RSS) |
| Bing Search | ✅ working | free (RSS) |
| Quora (indexed Q) | ✅ discovery only | free (via Bing) |
| Facebook Page discovery | ✅ discovery only | free (public search) |
| Competitor blog | ✅ discovery | free (RSS/sitemap) |
| Google Search Console | ⚠️ needs OAuth | Google account |
| Facebook organic posts | ⚠️ needs Meta access | Meta App + Pages Public Content Access |
| Search API (SerpApi/Brave) | ⚠️ needs key | API key (optional) |

---

## 7. Safety & Compliance

- **Launch gates** must all pass before auto-launch (facts, audience/consent, sender, links, compliance, checklist, suppression, thresholds).
- **No auto-send** by default. Trend mode is forced `draft_only`.
- **Evidence-based writing**: no fabricated stats, percentages, market sizes, or growth claims.
- **Claim governance**: prohibited/qualified/validate labels enforced per product.
- **Measurement**: open rate is not the sole signal (privacy prefetching); prioritize delivery, clicks, replies, conversion, revenue, unsubscribe, complaint, bounce.
- **No winner** declared before configured sample size + evaluation window.

---

## 8. Known Limitations / Open Items

1. **Google Trends** can be blocked (`/sorry` anti-bot) under heavy repeated use - mitigated by 72h cache, cooldown, and RSS fallback.
2. **Reddit** public RSS is rate-limited (429) - mitigated by pacing + cache; reliable unlimited needs Reddit OAuth app.
3. **Facebook organic monitoring** requires Meta App approval.
4. **Google Search Console** requires OAuth/service-account access.
5. **Turbo Guard** has no approved sender/audience identity yet - sender must be set manually or mapped.
6. **Apps Script webhook** for reporting sheet auto-update is pending user-provided URL + secret.
7. **Mailchimp write** is read-only in the current third-party MCP; writable campaigns need the project Mailchimp write integration for auto-launch.

---

## 9. Operations Reference

- **Turn Trend Intelligence on/off:** Settings → Trend Intelligence & Email Agent toggle.
- **Change LLM provider:** Settings → AI writing provider.
- **Run trend analysis manually:** `POST /api/cron/trend-intelligence` (cron-secret protected).
- **View latest trend run:** `GET /api/trend-intelligence/latest`.
- **View trend history:** `GET /api/trend-intelligence/history`.
- **Approve/reject a trend opportunity:** `PATCH /api/trend-intelligence/opportunities/{id}`.

---

## 10. Product CTA Map

| Product | Primary CTA | URL |
|---|---|---|
| Turbo Add-ons | Explore widgets | `https://turbo-addons.com/pricing/` |
| Turbo Guard | Scan Your Site Free | `https://wordpress.org/plugins/turbo-guard/` |
| Travilo | Book a free demo | `https://travilo.io/#demo` |
| PeopleDesk | Book a demo | `https://ibos.io/best-hr-software-in-bangladesh/#peopledesk-demo` |
| Managerium | Book a demo | `https://ibos.io/managerium/#managerium-erp-demo` |
| PrimeVAT | Book a demo | `https://ibos.io/vat-management-software-solution-primevat/#demo` |
| TrackForce | Start free trial | `https://trackforce.io/start-free-trial` |
| AeroApps | Explore | `https://aeroapps.io/` |
| AeroDrop | Explore | `https://aerodrop.ai/` |
| AeroRank | Explore | `https://apps.shopify.com/aero-seo-rank-ai` |
