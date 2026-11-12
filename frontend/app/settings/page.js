"use client";

import { useEffect, useState } from "react";
import AppShell from "../../components/AppShell";
import { api } from "../../lib/api";

const LLM_OPTIONS = [
  { key: "rule_based", label: "Brand MD engine", sub: "No API cost · automatic fallback" },
  { key: "openai", label: "ChatGPT / OpenAI", sub: "GPT models" },
  { key: "deepseek", label: "DeepSeek", sub: "DeepSeek Chat / Reasoner" },
  { key: "gemini", label: "Google Gemini", sub: "Gemini Flash / Pro" },
  { key: "anthropic", label: "Anthropic Claude", sub: "Claude models" },
];

function fmt(v) {
  if (v === null || v === undefined || v === "") return "-";
  return v;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [reportForm, setReportForm] = useState(null);
  const [saveBusy, setSaveBusy] = useState(false);
  const [senderForm, setSenderForm] = useState(null);
  const [llm, setLlm] = useState(null);
  const [llmForm, setLlmForm] = useState({ provider: "rule_based", api_key: "", model: "", base_url: "" });
  const [llmBusy, setLlmBusy] = useState(false);
  const [llmTest, setLlmTest] = useState(null);
  const [selfCheck, setSelfCheck] = useState(null);
  const [checking, setChecking] = useState(false);
  const [emailProviderForm, setEmailProviderForm] = useState(null);
  // Today's real report, so the preview below shows live numbers instead of fixed ones.
  const [liveReport, setLiveReport] = useState(null);
  const [emailProviderBusy, setEmailProviderBusy] = useState(false);
  const [emailProviderStatus, setEmailProviderStatus] = useState(null);
  const [emailProviderTest, setEmailProviderTest] = useState(null);

  useEffect(() => {
    api.llmConfig().then((c) => {
      setLlm(c);
      const key = c.active_provider || "rule_based";
      const p = c.providers?.[key] || {};
      setLlmForm({ provider: key, api_key: "", model: p.model || "", base_url: p.base_url || "" });
    }).catch((e) => setError(e.message));
      api.emailProviderStatus().then(setEmailProviderStatus).catch(() => {});
      api.reports().then(setLiveReport).catch(() => setLiveReport(null));
    api.settings().then((s) => {
      setSettings(s);
      setReportForm({
        report_recipients: Array.isArray(s.report_recipients) ? s.report_recipients.join(", ") : s.report_recipients,
        reporting_sheet: s.reporting_sheet || "",
        report_email_subject: s.report_email_subject || "",
        report_email_format: s.report_email_format || "",
        daily_sheet_update: s.daily_sheet_update || "",
        daily_email: s.daily_email || "",
        sheet_update_frequency: s.sheet_update_frequency || "daily",
        sheet_update_time: s.sheet_update_time || "09:50",
        timezone: s.timezone || "Asia/Dhaka",
        reporting_sheet_tab: s.reporting_sheet_tab || "Campaign Report",
        sheet_webhook_url: s.sheet_webhook_url || "",
        sheet_webhook_secret: s.sheet_webhook_secret || "",
        smtp_host: s.smtp_host || "",
        smtp_port: s.smtp_port || "587",
        smtp_user: s.smtp_user || "",
        smtp_app_password: s.smtp_app_password || "",
        smtp_from: s.smtp_from || "",
      });
      setSenderForm({
        default_from_name: s.default_from_name || "",
        default_reply_to: s.default_reply_to || "",
      });
      setEmailProviderForm({
        sendclean_owner_id: s.sendclean_owner_id || "",
        sendclean_api_token: "",
        sendclean_smtp_user_name: s.sendclean_smtp_user_name || "",
        sendclean_configured: Boolean(s.sendclean_api_token_configured),
      });
    }).catch((e) => setError(e.message));
  }, []);

  async function saveSender() {
    setSaveBusy(true);
    setError("");
    try {
      await Promise.all([
        api.patchSetting("default_from_name", senderForm.default_from_name),
        api.patchSetting("default_reply_to", senderForm.default_reply_to),
      ]);
      const fresh = await api.settings();
      setSettings(fresh);
      setFlash("Default sender saved - used as default in the send wizard");
      setTimeout(() => setFlash(""), 4000);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaveBusy(false);
    }
  }

  async function saveReport() {
    setSaveBusy(true);
    setError("");
    try {
      const ops = [];
      for (const [key, value] of Object.entries(reportForm)) {
        ops.push(api.patchSetting(key, key === "report_recipients" ? JSON.stringify(value.split(",").map((x) => x.trim()).filter(Boolean)) : value));
      }
      await Promise.all(ops);
      const fresh = await api.settings();
      setSettings(fresh);
      setFlash("Reporting, sheet & email settings saved - the agent uses these for the daily report");
      setTimeout(() => setFlash(""), 4000);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaveBusy(false);
    }
  }

  function refreshEmailProviderForm(s) {
    setEmailProviderForm({
      sendclean_owner_id: s.sendclean_owner_id || "",
      sendclean_api_token: "",
      sendclean_smtp_user_name: s.sendclean_smtp_user_name || "",
      sendclean_configured: Boolean(s.sendclean_api_token_configured),
    });
  }

  async function testEmailProviderConnection() {
    setEmailProviderBusy(true);
    setError("");
    setEmailProviderTest(null);
    try {
      const tested = await api.testEmailProvider({
        provider: "sendclean",
        owner_id: emailProviderForm.sendclean_owner_id,
        token: emailProviderForm.sendclean_api_token,
      });
      setEmailProviderTest(tested);
    } catch (e) {
      setEmailProviderTest({ ok: false, error: e.message });
    } finally {
      setEmailProviderBusy(false);
    }
  }

  async function saveEmailProvider() {
    setEmailProviderBusy(true);
    setError("");
    try {
      await api.patchSetting("sendclean_owner_id", emailProviderForm.sendclean_owner_id || "");
      if (emailProviderForm.sendclean_api_token) await api.patchSetting("sendclean_api_token", emailProviderForm.sendclean_api_token);
      await api.patchSetting("sendclean_smtp_user_name", emailProviderForm.sendclean_smtp_user_name || "");
      await api.patchSetting("email_provider", "sendclean");
      const fresh = await api.settings();
      setSettings(fresh);
      refreshEmailProviderForm(fresh);
      setEmailProviderStatus(await api.emailProviderStatus());
      setFlash("SendClean saved as the active email provider");
      setTimeout(() => setFlash(""), 4000);
    } catch (e) {
      setError(e.message);
    } finally {
      setEmailProviderBusy(false);
    }
  }

  const sheetId = reportForm?.reporting_sheet || "";
  // Preview with TODAY's real numbers, never fixed ones - a preview that shows made-up
  // figures is how the placeholder values survived this long.
  const previewDate = liveReport?.date || new Date().toISOString().slice(0, 10);
  const previewMetrics = liveReport?.metrics || {};
  const asPct = (v) => (v === undefined || v === null ? "n/a" : `${v}%`);
  const previewSubject = (reportForm?.report_email_subject || "").replace("{date}", previewDate);
  const previewBody = (reportForm?.report_email_format || "")
    .replace("{date}", previewDate)
    .replace("{delivery_rate}", asPct(previewMetrics.delivery_rate))
    .replace("{click_rate}", asPct(previewMetrics.click_rate))
    .replace("{open_rate}", asPct(previewMetrics.open_rate))
    .replace("{sent}", String(previewMetrics.sent ?? "n/a"))
    .replace("{delivered}", String(previewMetrics.delivered ?? "n/a"))
    .replace("{sheet_link}", sheetId ? "docs.google.com/spreadsheets/d/" + sheetId : "(sheet not set)");

  async function previewTemplate() {
    setSaveBusy(true);
    setError("");
    try {
      const html = await api.templatePreview();
      const w = window.open("", "_blank");
      if (w) {
        w.document.write(html);
        w.document.close();
      } else {
        setError("Popup blocked - allow popups for this site");
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setSaveBusy(false);
    }
  }

  async function toggleTrendIntelligence() {
    const enabled = settings.trend_intelligence_enabled === true || settings.trend_intelligence_enabled === "true";
    const next = enabled ? "false" : "true";
    try {
      await api.patchSetting("trend_intelligence_enabled", next);
      setSettings({ ...settings, trend_intelligence_enabled: next });
      setFlash(`Trend Intelligence → ${next === "true" ? "ON" : "OFF"}`);
      setTimeout(() => setFlash(""), 3500);
    } catch (e) {
      setError(e.message);
    }
  }

  function chooseLlmProvider(key) {
    const p = llm?.providers?.[key] || {};
    setLlmForm({ provider: key, api_key: "", model: p.model || "", base_url: p.base_url || "" });
    setLlmTest(null);
  }

  async function saveLlm() {
    setLlmBusy(true);
    setError("");
    setLlmTest(null);
    try {
      const fresh = await api.saveLlmConfig({ ...llmForm, make_active: true });
      setLlm(fresh);
      const p = fresh.providers?.[llmForm.provider] || {};
      setLlmForm((f) => ({ ...f, api_key: "", model: p.model || f.model, base_url: p.base_url || f.base_url }));
      setFlash(`${llmForm.provider === "rule_based" ? "Brand MD engine" : p.label} is now the active writing provider`);
      setTimeout(() => setFlash(""), 5000);
    } catch (e) {
      setError(e.message);
      try { setLlm(await api.llmConfig()); } catch { /* keep current state */ }
    } finally {
      setLlmBusy(false);
    }
  }

  async function testLlmConnection() {
    setLlmBusy(true);
    setError("");
    setLlmTest(null);
    try {
      const result = await api.testLlm({ ...llmForm, make_active: false });
      setLlmTest(result);
    } catch (e) {
      setLlmTest({ ok: false, response: e.message });
    } finally {
      setLlmBusy(false);
    }
  }

  /** Writes one real sample email through the same code path the button uses. */
  async function runSelfCheck() {
    setChecking(true);
    setSelfCheck(null);
    setError("");
    try {
      setSelfCheck(await api.llmSelfCheck());
    } catch (e) {
      setSelfCheck({ ok: false, error: e.message });
    } finally {
      setChecking(false);
    }
  }

  if (!settings && !error) {
    return (
      <AppShell active="/settings">
        <div className="loading"><span className="spinner" /> Loading settings…</div>
      </AppShell>
    );
  }

  const sections = [
    {
      title: "Performance targets",
      rows: [
        ["Primary KPIs", Array.isArray(settings.primary_kpis) ? settings.primary_kpis.join(", ") : fmt(settings.primary_kpis)],
        ["Delivery rate", `${(settings.target_delivery_rate * 100).toFixed(0)}%`],
        ["Click rate", `${(settings.target_click_rate * 100).toFixed(1)}%`],
        ["Blended open", `${(settings.target_blended_open_rate * 100).toFixed(1)}%`],
        ["Max bounce", `${(settings.maximum_bounce_rate * 100).toFixed(0)}%`],
        ["Max unsubscribe", `${(settings.maximum_unsubscribe_rate * 100).toFixed(1)}%`],
        ["Max complaint", `${(settings.maximum_complaint_rate * 100).toFixed(1)}%`],
        ["Min delivered sample", fmt(settings.minimum_delivered_sample)],
        ["Eval window", `${fmt(settings.evaluation_window_hours)}h`],
      ],
    },
    {
      title: "Schedule",
      rows: [
        ["Timezone", fmt(settings.timezone)],
        ["Sheet sync", fmt(settings.daily_sheet_update)],
        ["Daily email", fmt(settings.daily_email)],
      ],
    },
    {
      title: "Reporting",
      rows: [
        ["Sheet", fmt(settings.reporting_sheet)],
        ["Recipients", Array.isArray(settings.report_recipients) ? settings.report_recipients.join(", ") : fmt(settings.report_recipients)],
      ],
    },
  ];
  const activeLlmKey = llm?.active_provider || "rule_based";
  const activeLlm = llm?.providers?.[activeLlmKey];
  const activeConnected = activeLlmKey === "rule_based" || activeLlm?.connection_status === "connected";
  const trendEnabled = settings.trend_intelligence_enabled === true || settings.trend_intelligence_enabled === "true";

  return (
    <AppShell active="/settings">
      <div className="page-head">
        <div>
          <div className="crumb">System / <b>Settings</b></div>
          <h1>Settings</h1>
        </div>
      </div>

      {error && <div className="note n-warn">⚠ {error}</div>}
      {flash && <div className="note n-success">✓ {flash}</div>}

      {settings && (
        <>
          <div className="card" style={{ marginBottom: 16, border: `1px solid ${trendEnabled ? "var(--success-border)" : "var(--border)"}` }}>
            <div className="card-h">
              <div>
                <h3>Trend Intelligence & Email Agent</h3>
                <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 3 }}>
                  Google Trends + Reddit + Google News থেকে independently verified topic detect করে marketing email draft করবে
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className={`badge ${trendEnabled ? "b-success" : "b-neutral"}`}>
                  <span className="dot" />{trendEnabled ? "TREND INTEL ON" : "TREND INTEL OFF"}
                </span>
                <span className={`switch ${trendEnabled ? "on" : ""}`} onClick={toggleTrendIntelligence} />
              </div>
            </div>

            <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: 9, marginBottom: 14 }}>
              {[
                ["Sources", "Google Trends · Reddit · Google News"],
                ["Validation", "Multiple independent sources"],
                ["Scoring", "0–100 · ≥80 = email opportunity"],
                ["Output", "Trend report + email draft"],
                ["Sending", "Draft only · never auto-send"],
              ].map(([k, v]) => (
                <div key={k} style={{ padding: "9px 10px", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", background: "var(--surface-2)" }}>
                  <div style={{ fontSize: 10.5, textTransform: "uppercase", color: "var(--ink-4)", fontWeight: 750 }}>{k}</div>
                  <div style={{ fontSize: 12.5, color: "var(--ink-2)", fontWeight: 650, marginTop: 2 }}>{v}</div>
                </div>
              ))}
            </div>

            <div className="note n-info" style={{ marginTop: 0 }}>
              <span><b>OFF থাকলে</b> process inactive - কোনো research / email generate হবে না। ON করলে scheduled trend analysis চলবে। Trend কখনো এক source দেখে ধরা হয় না - multiple source + evidence দরকার।</span>
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <div>
                <h3>AI writing provider</h3>
                <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 3 }}>
                  ✨ Draft with Agent ei provider use korbe · provider fail korle Brand MD engine fallback
                </div>
              </div>
              <span className={`badge ${activeConnected ? "b-success" : "b-danger"}`} style={{ fontSize: 12, padding: "6px 10px" }}>
                <span className="dot" />
                ACTIVE · {activeLlmKey === "rule_based" ? "Brand MD engine · READY" : `${activeLlm?.label || activeLlmKey} · ${(activeLlm?.connection_status || "UNTESTED").toUpperCase()}`}
              </span>
            </div>

            <div style={{ padding: "12px 14px", borderRadius: "var(--radius)", marginBottom: 14, border: `1px solid ${activeConnected ? "var(--success-border)" : "var(--danger-border)"}`, background: activeConnected ? "var(--success-bg)" : "var(--danger-bg)", display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <div>
                <div style={{ fontSize: 13.5, fontWeight: 800, color: activeConnected ? "var(--success)" : "var(--danger)" }}>
                  {activeConnected ? "✓ Live agent writing provider is ready" : "⚠ Active provider needs attention"}
                </div>
                <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>
                  {activeLlmKey === "rule_based" ? "Draft with Agent বর্তমানে Brand MD engine use করছে." : `${activeLlm?.label} · ${activeLlm?.model} · last tested ${activeLlm?.last_tested_at || "not yet"}`}
                </div>
              </div>
              <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>Fallback: Brand MD engine</span>
            </div>

            {/* End-to-end proof that "Draft with Agent" will actually produce copy. */}
            <div className="inline">
              <button className="btn btn-secondary" onClick={runSelfCheck} disabled={checking}>
                {checking ? "Running self-check… (~30s)" : "🧪 Run Agent self-check"}
              </button>
              <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
                Writes one real sample email through the exact path the button uses.
              </span>
            </div>

            {selfCheck ? (
              selfCheck.ok ? (
                <div className="note n-success" style={{ marginTop: 0, marginBottom: 14 }}>
                  <span>
                    <b>Self-check passed in {selfCheck.seconds}s</b> - {selfCheck.provider} / {selfCheck.model}
                    {" · "}{selfCheck.body_chars} chars body, {selfCheck.html_chars} chars HTML
                    {selfCheck.brand_md ? ` · brand doc: ${selfCheck.brand_md}` : ""}.<br />
                    <span className="mono" style={{ fontSize: 11.5 }}>Sample subject: “{selfCheck.subject}”</span>
                  </span>
                </div>
              ) : (
                <div className="note n-warn" style={{ marginTop: 0, marginBottom: 14 }}>
                  <span>
                    <b>Self-check failed.</b> {selfCheck.error || `No usable copy came back (${selfCheck.body_chars || 0} chars body).`}
                    {" "}Draft with Agent will fall back to the Brand MD engine - check the provider key above.
                  </span>
                </div>
              )
            ) : null}

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 9, marginBottom: 16 }}>
              {LLM_OPTIONS.map((o) => {
                const configured = o.key === "rule_based" || llm?.providers?.[o.key]?.configured;
                const connection = o.key === "rule_based" ? "ready" : llm?.providers?.[o.key]?.connection_status;
                const selected = llmForm.provider === o.key;
                const active = llm?.active_provider === o.key;
                return (
                  <button key={o.key} onClick={() => chooseLlmProvider(o.key)} style={{ textAlign: "left", cursor: "pointer", padding: "11px 12px", borderRadius: "var(--radius)", border: `1px solid ${selected ? "var(--brand-500)" : "var(--border)"}`, background: selected ? "var(--brand-50)" : "var(--surface)", boxShadow: selected ? "var(--ring)" : "none" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 5, fontWeight: 750, fontSize: 13 }}>
                      <span>{o.label}</span>{active && <span style={{ color: "var(--brand-600)", fontSize: 10 }}>ACTIVE</span>}
                    </div>
                    <div style={{ color: "var(--ink-3)", fontSize: 11.5, marginTop: 3 }}>{o.sub}</div>
                    <div style={{ color: connection === "connected" || connection === "ready" ? "var(--success)" : connection === "failed" ? "var(--danger)" : configured ? "var(--warning)" : "var(--ink-4)", fontSize: 10.5, fontWeight: 750, marginTop: 6 }}>
                      {active ? `● ACTIVE · ${connection === "connected" || connection === "ready" ? "CONNECTED" : String(connection || "UNTESTED").toUpperCase()}` : connection === "connected" ? "● CONNECTED" : connection === "failed" ? "● CONNECTION FAILED" : configured ? "● KEY SAVED · TEST REQUIRED" : "○ API key needed"}
                    </div>
                  </button>
                );
              })}
            </div>

            {llmForm.provider !== "rule_based" ? (
              <div style={{ display: "grid", gap: 12 }}>
                <div className="grid" style={{ gridTemplateColumns: "1fr 1.4fr" }}>
                  <div className="field"><label>Model ID</label>
                    <select className="input mono" value={(llm?.providers?.[llmForm.provider]?.models || []).includes(llmForm.model) ? llmForm.model : "__custom__"} onChange={(e) => setLlmForm({ ...llmForm, model: e.target.value === "__custom__" ? "" : e.target.value })}>
                      {(llm?.providers?.[llmForm.provider]?.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
                      <option value="__custom__">Custom model ID…</option>
                    </select>
                    {!(llm?.providers?.[llmForm.provider]?.models || []).includes(llmForm.model) && (
                      <input className="input mono" style={{ marginTop: 7 }} value={llmForm.model} onChange={(e) => setLlmForm({ ...llmForm, model: e.target.value })} placeholder="Exact model ID from your provider" />
                    )}
                  </div>
                  <div className="field"><label>API base URL</label>
                    <input className="input mono" value={llmForm.base_url} onChange={(e) => setLlmForm({ ...llmForm, base_url: e.target.value })} placeholder="https://.../v1" />
                  </div>
                </div>
                <div className="field"><label>API key {llm?.providers?.[llmForm.provider]?.configured ? "(configured - blank রাখলে current key থাকবে)" : "*"}</label>
                  <input className="input mono" type="password" autoComplete="new-password" value={llmForm.api_key} onChange={(e) => setLlmForm({ ...llmForm, api_key: e.target.value })} placeholder={llm?.providers?.[llmForm.provider]?.configured ? "••••••••  leave blank to keep existing key" : "Paste provider API key"} />
                </div>
                <div className="note n-info" style={{ marginTop: 0 }}>
                  <span><b>Secure:</b> key HTTPS diye backend e jay, SECRET_KEY diye encrypted অবস্থায় PostgreSQL e save hoy, frontend e kokhono return hoy na. Model ID editable - account e available exact ID use korun.</span>
                </div>
              </div>
            ) : (
              <div className="note n-info" style={{ marginTop: 0 }}>
                <span><b>Brand MD engine:</b> kono external API cost nai. Product MD facts + email-writing-skill-v4 rules diye deterministic copy, HTML and CTA banay. AI provider unavailable hole etai automatic fallback.</span>
              </div>
            )}

            <div style={{ display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap", marginTop: 14 }}>
              {llmForm.provider !== "rule_based" && <button className="btn btn-secondary btn-sm" disabled={llmBusy} onClick={testLlmConnection}>{llmBusy ? "Testing…" : "Test connection"}</button>}
              <button className="btn btn-primary btn-sm" disabled={llmBusy} onClick={saveLlm}>{llmBusy ? "Testing & activating…" : llmForm.provider === "rule_based" ? "Activate Brand MD engine" : "Save, Test & Activate"}</button>
              {llmTest && <span className={`badge ${llmTest.ok ? "b-success" : "b-danger"}`} style={{ fontSize: 11.5 }}>{llmTest.ok ? `✓ Connected · ${llmTest.model}` : `⚠ ${llmTest.response}`}</span>}
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <div>
                <h3>Email provider</h3>
                <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 3 }}>
                  SendClean-only transactional email integration
                </div>
              </div>
              <span className={`badge ${emailProviderStatus?.configured ? "b-success" : "b-danger"}`} style={{ fontSize: 12, padding: "6px 10px" }}>
                <span className="dot" />
                ACTIVE · SendClean
              </span>
            </div>

            {emailProviderForm && (
              <div style={{ display: "grid", gap: 12 }}>
                <div className="field">
                  <label>SendClean Owner ID</label>
                  <input className="input mono" autoComplete="off" value={emailProviderForm.sendclean_owner_id} onChange={(e) => setEmailProviderForm({ ...emailProviderForm, sendclean_owner_id: e.target.value })} placeholder="Paste SendClean Owner Id" />
                </div>
                <div className="field">
                  <label>SendClean API token</label>
                  <input className="input mono" type="password" autoComplete="new-password" value={emailProviderForm.sendclean_api_token} onChange={(e) => setEmailProviderForm({ ...emailProviderForm, sendclean_api_token: e.target.value })} placeholder={emailProviderForm.sendclean_configured ? "Configured - paste only to replace" : "Paste SendClean API token"} />
                </div>
                <div className="field">
                  <label>SendClean SMTP username</label>
                  <input className="input mono" autoComplete="off" value={emailProviderForm.sendclean_smtp_user_name} onChange={(e) => setEmailProviderForm({ ...emailProviderForm, sendclean_smtp_user_name: e.target.value })} placeholder="Required for transactional sends" />
                </div>
                <div className="note n-warn" style={{ marginTop: 0 }}>
                  <span><b>Capability limit:</b> SendClean documents transactional sends, message lookup, domains and webhooks. It does not document audience lists, marketing campaigns or behavior-branch APIs, so those operations fail closed.</span>
                </div>
                <div style={{ display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap" }}>
                  <button className="btn btn-secondary btn-sm" disabled={emailProviderBusy} onClick={testEmailProviderConnection}>{emailProviderBusy ? "Testing…" : "Test connection"}</button>
                  <button className="btn btn-primary btn-sm" disabled={emailProviderBusy} onClick={saveEmailProvider}>{emailProviderBusy ? "Saving…" : "Save provider"}</button>
                  {emailProviderTest?.ok && <span className="badge b-success" style={{ fontSize: 11.5 }}>✓ Connected · {emailProviderTest.account?.name || "SendClean account"}</span>}
                  {emailProviderTest && emailProviderTest.ok === false && <span className="badge b-danger" style={{ fontSize: 11.5 }}>⚠ {emailProviderTest.error || "Connection failed"}</span>}
                  {emailProviderStatus && <span className="badge b-neutral" style={{ fontSize: 11.5 }}>{emailProviderStatus.marketing_campaigns_supported ? "Marketing campaigns enabled" : "Transactional-only · campaigns blocked"}</span>}
                </div>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-h">
              <h3>Email template (design)</h3>
              <button className="btn btn-secondary btn-sm" disabled={saveBusy} onClick={previewTemplate}>Open template preview ↗</button>
            </div>
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center" }}>
              <div style={{ width: 44, height: 44, borderRadius: 10, background: "linear-gradient(135deg,#6C2BD9,#4C1D95)", display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", fontWeight: 800 }}>⚡</div>
              <div style={{ display: "grid", gap: 6 }}>
                <div className="inline">
                  <span className="badge b-neutral" style={{ fontFamily: "var(--mono)" }}>Primary #6C2BD9</span>
                  <span className="badge b-neutral" style={{ fontFamily: "var(--mono)" }}>CTA #FF6B2C</span>
                  <span className="badge b-neutral" style={{ fontFamily: "var(--mono)" }}>Text #1A1A2E</span>
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>Turbo Addons branded HTML email system (table-based, inline CSS, email-safe) - saved in <span className="mono">email_templates.py</span>. Report emails now send with this branded HTML + plain-text fallback.</div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <h3>Default sender</h3>
              <button className="btn btn-primary btn-sm" disabled={saveBusy} onClick={saveSender}>{saveBusy ? "Saving…" : "Save Defaults"}</button>
            </div>
            {senderForm && (
              <div className="stack">
                <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                  <div className="field"><label>Sender name (from)</label>
                    <input className="input" value={senderForm.default_from_name} onChange={(e) => setSenderForm({ ...senderForm, default_from_name: e.target.value })} />
                  </div>
                  <div className="field"><label>Reply-to email</label>
                    <input className="input" value={senderForm.default_reply_to} onChange={(e) => setSenderForm({ ...senderForm, default_reply_to: e.target.value })} />
                  </div>
                </div>
                <div className="note n-info" style={{ marginTop: 0 }}>
                  <span>These become the defaults in the <b>send wizard</b> - you can still override per send.</span>
                </div>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-h">
              <h3>Reporting & emailing</h3>
              <button className="btn btn-primary btn-sm" disabled={saveBusy} onClick={saveReport}>{saveBusy ? "Saving…" : "Save All Changes"}</button>
            </div>
            {reportForm && (
              <div style={{ display: "grid", gap: 22 }}>
                <div>
                  <h3 style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 10 }}>📊 Report sheet</h3>
                  <div className="grid" style={{ gridTemplateColumns: "2fr 1fr 1fr 1fr" }}>
                    <div className="field"><label>Google Sheets ID (sheet change korte edite koro)</label>
                      <input className="input mono" value={reportForm.reporting_sheet} onChange={(e) => setReportForm({ ...reportForm, reporting_sheet: e.target.value })} />
                    </div>
                    <div className="field"><label>Update frequency</label>
                      <select className="input" value={reportForm.sheet_update_frequency} onChange={(e) => setReportForm({ ...reportForm, sheet_update_frequency: e.target.value })}>
                        <option value="daily">Daily</option>
                        <option value="hourly">Hourly (every 60 min)</option>
                        <option value="weekly">Weekly</option>
                      </select>
                    </div>
                    <div className="field"><label>Sheet update time</label>
                      <input className="input mono" type="time" value={reportForm.sheet_update_time} onChange={(e) => setReportForm({ ...reportForm, sheet_update_time: e.target.value, daily_sheet_update: e.target.value })} />
                    </div>
                    <div className="field"><label>Report email time</label>
                      <input className="input mono" type="time" value={reportForm.daily_email} onChange={(e) => setReportForm({ ...reportForm, daily_email: e.target.value })} />
                    </div>
                  </div>
                  <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 10 }}>
                    <div className="field"><label>Timezone</label>
                      <select className="input" value={reportForm.timezone} onChange={(e) => setReportForm({ ...reportForm, timezone: e.target.value })}>
                        <option value="Asia/Dhaka">Asia/Dhaka</option>
                        <option value="UTC">UTC</option>
                        <option value="America/New_York">America/New_York</option>
                        <option value="Europe/London">Europe/London</option>
                      </select>
                    </div>
                    <div className="field"><label>Sheet tab name</label>
                      <input className="input" value={reportForm.reporting_sheet_tab} onChange={(e) => setReportForm({ ...reportForm, reporting_sheet_tab: e.target.value })} />
                    </div>
                  </div>
                  <div className="grid" style={{ gridTemplateColumns: "2fr 1fr", marginTop: 10 }}>
                    <div className="field"><label>Apps Script Web App URL</label>
                      <input className="input mono" value={reportForm.sheet_webhook_url} onChange={(e) => setReportForm({ ...reportForm, sheet_webhook_url: e.target.value })} placeholder="https://script.google.com/macros/s/.../exec" />
                    </div>
                    <div className="field"><label>Webhook secret</label>
                      <input className="input" type="password" value={reportForm.sheet_webhook_secret} onChange={(e) => setReportForm({ ...reportForm, sheet_webhook_secret: e.target.value })} placeholder="Same as Apps Script SENDWISE_SECRET" />
                    </div>
                  </div>
                  <div className="inline">
                    {sheetId && (
                      <a className="btn btn-secondary btn-sm" href={`https://docs.google.com/spreadsheets/d/${sheetId}/edit`} target="_blank" rel="noreferrer">Open sheet →</a>
                    )}
                    <span className="badge b-neutral">Sheet: {reportForm.sheet_update_time} · Email: {reportForm.daily_email} · {reportForm.timezone}</span>
                  </div>
                </div>

                <div>
                  <h3 style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 10 }}>✉️ Report email format (subject + body)</h3>
                  <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
                    <div style={{ display: "grid", gap: 12 }}>
                      <div className="field"><label>Subject - {"{date}"} placeholder</label>
                        <input className="input" value={reportForm.report_email_subject} onChange={(e) => setReportForm({ ...reportForm, report_email_subject: e.target.value })} />
                      </div>
                      <div className="field"><label>Body - placeholders: {"{date} {delivery_rate} {click_rate} {open_rate} {sheet_link}"}</label>
                        <textarea className="input" rows={4} value={reportForm.report_email_format} onChange={(e) => setReportForm({ ...reportForm, report_email_format: e.target.value })} />
                      </div>
                      <div className="field"><label>Recipients (comma separated)</label>
                        <input className="input" value={reportForm.report_recipients} onChange={(e) => setReportForm({ ...reportForm, report_recipients: e.target.value })} />
                      </div>
                    </div>
                    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
                      <div style={{ padding: "10px 14px", background: "var(--surface-2)", borderBottom: "1px solid var(--border)", fontSize: 12, fontWeight: 700, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: ".05em" }}>Live preview</div>
                      <div style={{ padding: "14px 16px", fontSize: 13.5, lineHeight: 1.6 }}>
                        <div style={{ fontWeight: 700, marginBottom: 2 }}>{previewSubject}</div>
                        <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 10 }}>From: {reportForm.smtp_from || reportForm.report_recipients.split(",")[0]?.trim() || "-"} · To: {reportForm.report_recipients}</div>
                        <div style={{ whiteSpace: "pre-wrap", color: "var(--ink-2)" }}>{previewBody}</div>
                      </div>
                    </div>
                  </div>
                </div>

                <div>
                  <h3 style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 10 }}>📨 Real delivery (SMTP / Gmail)</h3>
                  <div className="grid" style={{ gridTemplateColumns: "2fr 1fr 2fr 2fr 2fr" }}>
                    <div className="field"><label>From email</label>
                      <input className="input" value={reportForm.smtp_from} onChange={(e) => setReportForm({ ...reportForm, smtp_from: e.target.value })} placeholder="you@gmail.com" />
                    </div>
                    <div className="field"><label>SMTP host</label>
                      <input className="input mono" value={reportForm.smtp_host} onChange={(e) => setReportForm({ ...reportForm, smtp_host: e.target.value })} placeholder="smtp.gmail.com" />
                    </div>
                    <div className="field"><label>SMTP port</label>
                      <input className="input mono" value={reportForm.smtp_port} onChange={(e) => setReportForm({ ...reportForm, smtp_port: e.target.value })} />
                    </div>
                    <div className="field"><label>SMTP user (email)</label>
                      <input className="input" value={reportForm.smtp_user} onChange={(e) => setReportForm({ ...reportForm, smtp_user: e.target.value })} placeholder="you@gmail.com" />
                    </div>
                    <div className="field"><label>App password</label>
                      <input className="input" type="password" value={reportForm.smtp_app_password} onChange={(e) => setReportForm({ ...reportForm, smtp_app_password: e.target.value })} placeholder="16-digit app password" />
                    </div>
                  </div>
                  <div className="note n-info" style={{ marginTop: 10 }}>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></svg>
                    <span><b>Real Gmail delivery:</b> Google account e <b>2-Step Verification on</b> kore → <b>Security → App passwords</b> → "Mail" app → 16-digit password ta ekhane dite hobe. Save korlei "Send Report Now" real email pathabe.</span>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="grid three-col mt">
            {sections.map((s) => (
              <div className="card" key={s.title}>
                <div className="card-h"><h3>{s.title}</h3></div>
                <dl className="kv" style={{ gridTemplateColumns: "120px 1fr" }}>
                  {s.rows.map(([k, v]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt>{k}</dt>
                      <dd style={{ wordBreak: "break-word" }}>{v}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
          </div>
        </>
      )}
    </AppShell>
  );
}
