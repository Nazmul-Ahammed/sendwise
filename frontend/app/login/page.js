"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Logo from "../../components/Logo";
import { api, setToken } from "../../lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = await api.login(email, password);
      setToken(res.token);
      window.localStorage.setItem("sw_user", JSON.stringify(res.user));
      router.push("/dashboard");
    } catch (err) {
      setError(err.message || "Login failed");
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-hero">
        <div className="brand-row">
          <Logo size={40} />
          <div>
            <div className="name">Send<span>wise</span></div>
            <div className="tag">AGENT OS</div>
          </div>
        </div>
        <div>
          <h2>Welcome back, operator.</h2>
          <p>Your autonomous email orchestrator is standing by. Review, approve, launch - everything from one control plane.</p>
        </div>
        <div className="status-line">Agent status: <b>Online</b> · Asia/Dhaka</div>
      </div>
      <div className="login-form">
        <h3>Sign in</h3>
        <form onSubmit={submit} style={{ display: "grid", gap: 14 }}>
          <div className="field">
            <label>Email</label>
            <input className="input" type="email" placeholder="you@ibos.io" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="field">
            <label>Password</label>
            <input className="input" type="password" placeholder="••••••••" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          {error && <div className="note n-warn" style={{ marginTop: 0 }}>⚠ {error}</div>}
          <button className="btn btn-primary btn-lg" disabled={busy} style={{ marginTop: 6 }}>
            {busy ? "Signing in…" : "Enter Control Plane"}
          </button>
          <div style={{ textAlign: "center", fontSize: 12.5, color: "var(--ink-3)" }}>
            Demo: admin@sendwise.io · admin123
          </div>
        </form>
      </div>
    </div>
  );
}
