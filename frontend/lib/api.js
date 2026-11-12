export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function getToken() {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("sw_token");
}

export function setToken(token) {
  if (typeof window !== "undefined") window.localStorage.setItem("sw_token", token);
}

export function clearToken() {
  if (typeof window !== "undefined") window.localStorage.removeItem("sw_token");
}

export function logout() {
  clearToken();
  if (typeof window !== "undefined") window.location.href = "/login";
}

/** FastAPI returns `detail` as a string, or a list of field errors. Never show "[object Object]". */
export function formatDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (typeof d === "string") return d;
        const loc = Array.isArray(d?.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
        return `${loc ? loc + ": " : ""}${d?.msg || JSON.stringify(d)}`;
      })
      .join(" · ");
  }
  return typeof detail === "object" ? JSON.stringify(detail) : String(detail);
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = formatDetail(data.detail) || detail;
    } catch {
      /* ignore */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export const api = {
  login: (email, password) => request("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  dashboard: () => request("/api/dashboard"),
  campaigns: () => request("/api/campaigns"),
  campaign: (slug) => request(`/api/campaigns/${slug}`),
  campaignDeliveryReport: (slug) => request(`/api/campaigns/${slug}/delivery-report`),
  // Realtime per-recipient engagement, filled by the provider webhook.
  campaignEngagement: (slug) => request(`/api/campaigns/${slug}/engagement`),
  // Click map: the sent HTML plus per-link click counts, ready to be overlaid.
  campaignClickmap: (slug) => request(`/api/campaigns/${slug}/clickmap`),
  syncCampaignEngagement: (slug) =>
    request(`/api/campaigns/${slug}/engagement/sync`, { method: "POST" }),
  sequenceProgress: (slug) => request(`/api/campaigns/${slug}/sequence-progress`),
  sequenceOverview: () => request("/api/sequence-overview"),
  sequences: () => request("/api/sequences"),
  sequence: (slug) => request(`/api/sequences/${encodeURIComponent(slug)}`),
  createSequence: (payload) => request("/api/sequences", { method: "POST", body: JSON.stringify(payload) }),
  updateSequence: (slug, payload) => request(`/api/sequences/${encodeURIComponent(slug)}`, { method: "PUT", body: JSON.stringify(payload) }),
  duplicateSequence: (slug) => request(`/api/sequences/${encodeURIComponent(slug)}/duplicate`, { method: "POST" }),
  sequenceActivity: (slug) => request(`/api/sequences/${encodeURIComponent(slug)}/activity`),
  sequenceSelfCheck: () => request("/api/sequences/self-check"),
  sequenceSelfCheckFix: () => request("/api/sequences/self-check/fix", { method: "POST" }),
  dispatchSequences: (dryRun = false) => request(`/api/sequences/dispatch-now?dry_run=${dryRun ? 1 : 0}`, { method: "POST" }),
  // payload: {emails:[…]} or {audience_id, tag_filter, tag_match}
  enrollSequence: (slug, payload) => request(`/api/campaigns/${slug}/enroll`, { method: "POST", body: JSON.stringify(payload || {}) }),
  suppressions: () => request("/api/suppressions"),
  addSuppression: (email, reason = "manual") => request("/api/suppressions", { method: "POST", body: JSON.stringify({ email, reason }) }),
  registerSendcleanWebhook: () => request("/api/sendclean/webhook/register", { method: "POST" }),
  createCampaign: (payload) => request("/api/campaigns", { method: "POST", body: JSON.stringify(payload) }),
  deleteCampaign: (slug) => request(`/api/campaigns/${slug}`, { method: "DELETE" }),
  approve: (slug) => request(`/api/campaigns/${slug}/approve`, { method: "POST" }),
  ready: (slug) => request(`/api/campaigns/${slug}/ready`, { method: "POST" }),
  generateCopy: (slug) => request(`/api/campaigns/${slug}/generate-copy`, { method: "POST" }),
  sendTest: (slug) => request(`/api/campaigns/${slug}/send-test`, { method: "POST" }),
  updateEmails: (slug, emails) => request(`/api/campaigns/${slug}/emails`, { method: "PATCH", body: JSON.stringify({ emails }) }),
  launch: (slug) => request(`/api/campaigns/${slug}/launch`, { method: "POST" }),
  pause: (slug) => request(`/api/campaigns/${slug}/pause`, { method: "POST" }),
  emailProviderStatus: () => request("/api/email-provider/status"),
  testEmailProvider: (payload) => request("/api/email-provider/test", { method: "POST", body: JSON.stringify(payload) }),
  sendcleanOverview: () => request("/api/sendclean/overview"),
  sendcleanCheckDomain: (domain) => request(`/api/sendclean/domains/check?domain=${encodeURIComponent(domain)}`),
  sendcleanMessages: (xUniqueId) => request(`/api/sendclean/messages?x_unique_id=${encodeURIComponent(xUniqueId)}`),
  sendcleanSend: (payload) => request("/api/sendclean/send", { method: "POST", body: JSON.stringify(payload) }),
  reports: () => request("/api/reports"),
  refreshDelivery: () => request("/api/delivery/refresh", { method: "POST" }),
  // Send the report right now. `to` overrides the configured recipients when given.
  sendReport: (to) => request("/api/reports/send", {
    method: "POST",
    body: JSON.stringify(Array.isArray(to) && to.length ? { to } : {}),
  }),
  // Report schedule: { mode: "daily" | "once" | "off", time: "HH:MM", date: "YYYY-MM-DD" }
  setReportSchedule: (payload) => request("/api/reports/schedule", {
    method: "POST", body: JSON.stringify(payload || {}),
  }),
  jobs: () => request("/api/jobs"),
  retryJob: (id) => request(`/api/jobs/${id}/retry`, { method: "POST" }),
  settings: () => request("/api/settings"),
  patchSetting: (key, value) => request("/api/settings", { method: "PATCH", body: JSON.stringify({ key, value }) }),
  // Saved audiences, contacts and tags. Sendwise owns this; SendClean has no list API.
  audiences: () => request("/api/audiences"),
  createAudience: (name, description = "") => request("/api/audiences", { method: "POST", body: JSON.stringify({ name, description }) }),
  updateAudience: (id, payload) => request(`/api/audiences/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteAudience: (id) => request(`/api/audiences/${id}`, { method: "DELETE" }),
  audienceContacts: (id, tags = "", tagMatch = "any", q = "") => {
    const t = Array.isArray(tags) ? tags.join(",") : (tags || "");
    const search = (q || "").trim() ? `&q=${encodeURIComponent(q.trim())}` : "";
    return request(`/api/audiences/${id}/contacts?tags=${encodeURIComponent(t)}&tag_match=${encodeURIComponent(tagMatch)}${search}`);
  },
  addAudienceContacts: (id, payload) => request(`/api/audiences/${id}/contacts`, { method: "POST", body: JSON.stringify(payload) }),
  removeAudienceContacts: (id, payload) => request(`/api/audiences/${id}/contacts/remove`, { method: "POST", body: JSON.stringify(payload) }),
  // Everyone who asked not to be emailed again, plus list-membership cleanup.
  unsubscribed: (q = "") => request(`/api/unsubscribed${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  removeUnsubscribedFromAudiences: (payload) =>
    request("/api/unsubscribed/remove-from-audiences", { method: "POST", body: JSON.stringify(payload) }),
  contacts: (tags = "", tagMatch = "any") => {
    const t = Array.isArray(tags) ? tags.join(",") : (tags || "");
    return request(`/api/contacts?tags=${encodeURIComponent(t)}&tag_match=${encodeURIComponent(tagMatch)}`);
  },
  tags: () => request("/api/tags"),
  createContact: (payload) => request("/api/contacts", { method: "POST", body: JSON.stringify(payload) }),
  updateContact: (email, payload) => request(`/api/contacts/${encodeURIComponent(email)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteContact: (email) => request(`/api/contacts/${encodeURIComponent(email)}`, { method: "DELETE" }),
  // Deliverability checks - syntax, MX, disposable, role.
  validateEmails: (emails) => request("/api/validate-emails", { method: "POST", body: JSON.stringify({ emails }) }),
  validateAudience: (id) => request(`/api/audiences/${id}/validate`, { method: "POST" }),
  validateAllContacts: () => request("/api/contacts/validate-all", { method: "POST" }),
  validateContact: (email) => request(`/api/contacts/${encodeURIComponent(email)}/validate`, { method: "POST" }),
  // Tags are per-audience: a tag in one list never appears in another.
  audienceTags: (id) => request(`/api/audiences/${id}/tags`),
  createAudienceTag: (id, name) => request(`/api/audiences/${id}/tags/create`, {
    method: "POST", body: JSON.stringify({ name }),
  }),
  deleteAudienceTag: (id, tag) => request(`/api/audiences/${id}/tags/${encodeURIComponent(tag)}`, {
    method: "DELETE",
  }),
  bulkTags: (audienceId, emails, add, remove) => request(`/api/audiences/${audienceId}/tags`, {
    method: "POST", body: JSON.stringify({ emails, add, remove }),
  }),
  renameTag: (audienceId, oldTag, newTag) => request(`/api/audiences/${audienceId}/tags/rename`, {
    method: "POST", body: JSON.stringify({ old: oldTag, new: newTag }),
  }),
  // Nothing is ever hard-deleted - these move things to Trash and back.
  trash: () => request("/api/trash"),
  restoreAudience: (id) => request(`/api/audiences/${id}/restore`, { method: "POST" }),
  restoreContact: (email) => request(`/api/contacts/${encodeURIComponent(email)}/restore`, { method: "POST" }),
  restoreCampaign: (slug) => request(`/api/campaigns/${slug}/restore`, { method: "POST" }),
  restoreSequence: (slug) => request(`/api/sequences/${slug}/restore`, { method: "POST" }),
  restoreAllTrash: () => request("/api/trash/restore-all", { method: "POST" }),
  backupCounts: () => request("/api/backup/counts"),
  // Automatic backups - Sendwise takes these itself; these methods only read them.
  backups: () => request("/api/backups"),
  backupNow: () => request("/api/backups/now", { method: "POST" }),
  restoreBackup: (id) => request(`/api/backups/${id}/restore`, { method: "POST" }),
  deleteBackup: (id) => request(`/api/backups/${id}`, { method: "DELETE" }),
  backupPayload: (id) => request(`/api/backups/${id}`),
  // Permanent removal - the one thing a backup restore will never bring back.
  purges: () => request("/api/purges"),
  purgeContact: (email) => request(`/api/contacts/${encodeURIComponent(email)}/purge`, { method: "DELETE" }),
  purgeAudience: (id) => request(`/api/audiences/${id}/purge`, { method: "DELETE" }),
  purgeCampaign: (slug) => request(`/api/campaigns/${slug}/purge`, { method: "DELETE" }),
  purgeSequence: (slug) => request(`/api/sequences/${slug}/purge`, { method: "DELETE" }),
  forgetPurge: (id) => request(`/api/purges/${id}`, { method: "DELETE" }),
  backupExport: async () => {
    const res = await fetch(`${API_BASE}/api/backup/export`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error("Backup failed");
    return res.json();
  },
  llmConfig: () => request("/api/llm/config"),
  saveLlmConfig: (payload) => request("/api/llm/config", { method: "PATCH", body: JSON.stringify(payload) }),
  testLlm: (payload) => request("/api/llm/test", { method: "POST", body: JSON.stringify(payload) }),
  llmSelfCheck: () => request("/api/llm/selfcheck", { method: "POST" }),
  templatePreview: async () => {
    const res = await fetch(`${API_BASE}/api/templates/preview`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error("Preview failed");
    return res.text();
  },
};
