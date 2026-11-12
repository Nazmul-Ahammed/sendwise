"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Logo from "./Logo";
import { getToken, logout } from "../lib/api";

const NAV = [
  {
    section: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: "grid" },
      { href: "/campaigns", label: "Sends", icon: "mail" },
    ],
  },
  {
    section: "Automation",
    items: [
      { href: "/audiences", label: "Audiences", icon: "users" },
      { href: "/unsubscribed", label: "Unsubscribed", icon: "shield" },
      { href: "/sequences", label: "Sequences", icon: "flow" },
    ],
  },
  {
    section: "Intelligence",
    items: [
      { href: "/engagement", label: "Live engagement", icon: "target" },
      { href: "/reports", label: "Reports", icon: "chart" },
      { href: "/jobs", label: "Jobs & Activity", icon: "clock" },
    ],
  },
  {
    section: "System",
    items: [
      { href: "/trash", label: "Trash & backup", icon: "shield" },
      { href: "/settings", label: "Settings", icon: "gear" },
    ],
  },
];

const ICONS = {
  grid: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></svg>,
  mail: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" /><path d="M3 8l9 6 9-6" /></svg>,
  flow: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="7" r="2.5" /><circle cx="6" cy="18" r="2.5" /><path d="M8.4 7.2h7.2M8.4 16.8l5.4-7.8M18 9.5v7" /></svg>,
  users: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="9" cy="7" r="3.2" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0" /><path d="M16.5 4.3a3.2 3.2 0 0 1 0 5.4M18 14.2a6.5 6.5 0 0 1 3.5 5.8" /></svg>,
  shield: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 3l7 3v6c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-3z" /><path d="M9 12l2 2 4-4" /></svg>,
  chart: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19V5M4 19h16M8 15l3-4 3 2 5-7" /></svg>,
  target: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3.2" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></svg>,
  clock: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M12 8v4l3 2" /></svg>,
  gear: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h0a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55h0a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v0a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1z" /></svg>,
};

export default function AppShell({ children, active }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState(null);
  // On a small screen the nav is a drawer. Without this it was simply unreachable.
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    try {
      const u = JSON.parse(window.localStorage.getItem("sw_user") || "null");
      setUser(u);
    } catch {
      setUser(null);
    }
  }, [router]);

  // Tapping a link should close the drawer, and so should Escape.
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") setNavOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    document.body.style.overflow = navOpen ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [navOpen]);

  const activeHref = active || pathname;

  return (
    <div className="app">
      <div className="mobile-bar">
        <div className="mb-brand">Send<span>wise</span></div>
        <button
          aria-label={navOpen ? "Close menu" : "Open menu"}
          aria-expanded={navOpen}
          onClick={() => setNavOpen((v) => !v)}
        >
          {navOpen ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M6 6l12 12M18 6L6 18" /></svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M3 6h18M3 12h18M3 18h18" /></svg>
          )}
        </button>
      </div>

      <div
        className={`scrim ${navOpen ? "show" : ""}`}
        onClick={() => setNavOpen(false)}
        aria-hidden="true"
      />

      <aside className={`sidebar ${navOpen ? "open" : ""}`}>
        <div className="side-brand">
          <Logo />
          <div style={{ flex: 1 }}>
            <div className="name">Send<span>wise</span></div>
            <div className="tag">AGENT OS</div>
          </div>
          <button
            className="side-close"
            aria-label="Close menu"
            onClick={() => setNavOpen(false)}
            style={{ background: "none", border: "none", color: "#7C86AC", cursor: "pointer", padding: 6 }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
        <nav className="nav">
          {NAV.map((group) => (
            <div key={group.section}>
              <div className="sec">{group.section}</div>
              {group.items.map((n) => (
                <Link key={n.href} href={n.href} className={activeHref === n.href ? "active" : ""}>
                  {ICONS[n.icon]}{n.label}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="side-foot">
          <div className="agent-pill">
            <span className="dot"></span>
            <div><b>Agent Online</b><span>Asia/Dhaka · 15-min dispatch</span></div>
          </div>
          <div className="user">
            <div className="avatar">{(user?.name || "O").charAt(0)}</div>
            <div>
              <b>{user?.name || "Operator"}</b>
              <span>{user?.email || "operator"}</span>
            </div>
          </div>
          <button className="nav" onClick={logout} style={{ background: "none", border: "none", cursor: "pointer", color: "#7C86AC", fontSize: 12, textAlign: "left", padding: "4px 10px" }}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
