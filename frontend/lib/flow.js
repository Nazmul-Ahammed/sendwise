export function delayLabel(s) {
  if (!s) return "";
  const v = s.delay_value;
  const u = s.delay_unit || "day";
  if (v === 0 || (v === undefined && s.delay)) {
    return s.delay || "Immediate";
  }
  const unit = v === 1 ? u : `${u}s`;
  return `${v} ${unit}`;
}

export const BRANCH_OPTIONS = [
  { value: "main", label: "Main" },
  { value: "open_click", label: "Open+Click" },
  { value: "open_no_click", label: "Open+Not Click" },
  { value: "not_open", label: "Not Open" },
];

// 3 routing outcomes per email
export const ROUTE_FIELDS = [
  { key: "open_click_next", label: "Open+Click", color: "var(--success)", badge: "b-success" },
  { key: "open_no_click_next", label: "Open+Not Click", color: "var(--info)", badge: "b-info" },
  { key: "not_open_next", label: "Not Open", color: "var(--warning)", badge: "b-warning" },
];

export function allEmailRefs(stages) {
  const refs = [];
  (stages || []).forEach((s) => (s.emails || []).forEach((e) => refs.push(e.ref)));
  return refs;
}

export function emailName(stages, ref) {
  if (!ref) return "";
  for (const s of stages || []) {
    for (const e of s.emails || []) {
      if (e.ref === ref) return e.name;
    }
  }
  return ref;
}

export const EXIT_LABEL = "Exit";
