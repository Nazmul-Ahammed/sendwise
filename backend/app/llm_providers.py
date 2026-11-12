"""Secure multi-provider LLM configuration and API clients for Sendwise.

API keys are encrypted at rest with a Fernet key derived from SECRET_KEY and
are never returned to the frontend. OpenAI, DeepSeek and Gemini use their
OpenAI-compatible chat endpoints; Anthropic uses its native Messages API.
"""
import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from . import models


PROVIDERS = {
    "openai": {
        "label": "ChatGPT / OpenAI",
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "kind": "openai",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-5", "gpt-5-mini"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        "kind": "openai",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "gemini": {
        "label": "Google Gemini",
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GEMINI_API_KEY",
        "kind": "openai",
        "models": ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "model": "claude-3-5-sonnet-latest",
        "base_url": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
        "kind": "anthropic",
        "models": ["claude-3-5-sonnet-latest", "claude-sonnet-4-5", "claude-opus-4-1"],
    },
}


def _fernet():
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY is required to encrypt provider API keys")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value):
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value):
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def _get(db, key, default=""):
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    return row.value if row and row.value is not None else default


def _set(db, key, value):
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    if row:
        row.value = str(value)
    else:
        db.add(models.Setting(key=key, value=str(value)))


def get_provider_config(db, provider, api_key_override="", model_override="", base_url_override=""):
    if provider not in PROVIDERS:
        return None
    meta = PROVIDERS[provider]
    stored = decrypt_secret(_get(db, f"llm_{provider}_api_key_enc", ""))
    env_key = os.getenv(meta["env_key"], "")
    if provider == "openai":
        env_key = env_key or os.getenv("LLM_API_KEY", "")
    return {
        "provider": provider,
        "label": meta["label"],
        "kind": meta["kind"],
        "api_key": (api_key_override or stored or env_key).strip(),
        "model": (model_override or _get(db, f"llm_{provider}_model", "") or
                  (os.getenv("LLM_MODEL", "") if provider == "openai" else "") or meta["model"]).strip(),
        "base_url": (base_url_override or _get(db, f"llm_{provider}_base_url", "") or
                     (os.getenv("LLM_BASE_URL", "") if provider == "openai" else "") or meta["base_url"]).rstrip("/"),
    }


def get_active_config(db):
    active = _get(db, "llm_active_provider", "")
    if not active:
        active = "openai" if (os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")) else "rule_based"
    if active == "rule_based":
        return None
    cfg = get_provider_config(db, active)
    return cfg if cfg and cfg["api_key"] else None


def public_config(db):
    active = _get(db, "llm_active_provider", "")
    if not active:
        active = "openai" if (os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")) else "rule_based"
    providers = {}
    for key, meta in PROVIDERS.items():
        cfg = get_provider_config(db, key)
        providers[key] = {
            "label": meta["label"],
            "configured": bool(cfg["api_key"]),
            "model": cfg["model"],
            "base_url": cfg["base_url"],
            "models": meta.get("models", []),
            "connection_status": (_get(db, f"llm_{key}_connection_status", "untested")
                                  if cfg["api_key"] else "not_configured"),
            "last_tested_at": _get(db, f"llm_{key}_last_tested_at", ""),
            "last_test_message": _get(db, f"llm_{key}_last_test_message", ""),
        }
    return {
        "active_provider": active,
        "fallback": "rule_based",
        "providers": providers,
        "security": "API keys are encrypted at rest and never returned",
    }


def save_provider(db, provider, api_key="", model="", base_url="", make_active=True):
    if provider == "rule_based":
        _set(db, "llm_active_provider", "rule_based")
        db.commit()
        return public_config(db)
    if provider not in PROVIDERS:
        raise ValueError("Unsupported LLM provider")
    if api_key and api_key.strip():
        _set(db, f"llm_{provider}_api_key_enc", encrypt_secret(api_key.strip()))
    if model and model.strip():
        _set(db, f"llm_{provider}_model", model.strip())
    if base_url and base_url.strip():
        _set(db, f"llm_{provider}_base_url", base_url.strip().rstrip("/"))
    if api_key or model or base_url:
        _set(db, f"llm_{provider}_connection_status", "untested")
    cfg = get_provider_config(db, provider)
    if make_active:
        if not cfg["api_key"]:
            raise ValueError("Add an API key before activating this provider")
        _set(db, "llm_active_provider", provider)
    db.commit()
    return public_config(db)


def record_test(db, provider, ok, message=""):
    if provider in PROVIDERS:
        _set(db, f"llm_{provider}_connection_status", "connected" if ok else "failed")
        _set(db, f"llm_{provider}_last_tested_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        _set(db, f"llm_{provider}_last_test_message", str(message or "")[:300])
        db.commit()


def _request_openai(cfg, messages, max_tokens=1600):
    request_data = {
        "model": cfg["model"],
        "messages": messages,
    }
    is_gpt5 = cfg.get("provider") == "openai" and str(cfg.get("model", "")).startswith("gpt-5")
    timeout = 180 if is_gpt5 else 60
    # GPT-5 is a reasoning model: use max_completion_tokens and low reasoning
    # effort so email copy stays fast enough for a serverless function. Older
    # OpenAI models and compatible providers still use max_tokens + temperature.
    if is_gpt5:
        request_data["max_completion_tokens"] = max_tokens
        request_data["reasoning_effort"] = "low"
    else:
        request_data["temperature"] = 0.65
        request_data["max_tokens"] = max_tokens
    payload = json.dumps(request_data).encode("utf-8")
    req = urllib.request.Request(cfg["base_url"] + "/chat/completions", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + cfg["api_key"])
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _request_anthropic(cfg, messages, max_tokens=1600):
    user_text = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
    system_text = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
    payload = json.dumps({
        "model": cfg["model"],
        "max_tokens": max_tokens,
        "temperature": 0.65,
        "system": system_text,
        "messages": [{"role": "user", "content": user_text}],
    }).encode("utf-8")
    req = urllib.request.Request(cfg["base_url"] + "/messages", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", cfg["api_key"])
    req.add_header("anthropic-version", "2023-06-01")
    with urllib.request.urlopen(req, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    return "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")


def request_text(cfg, messages, max_tokens=1600):
    if not cfg or not cfg.get("api_key"):
        raise ValueError("Provider API key is not configured")
    try:
        if cfg.get("kind") == "anthropic":
            return _request_anthropic(cfg, messages, max_tokens=max_tokens)
        return _request_openai(cfg, messages, max_tokens=max_tokens)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"{cfg['label']} API {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to {cfg['label']}: {exc.reason}") from exc


def test_connection(cfg):
    text = request_text(cfg, [
        {"role": "system", "content": "You are a connection test."},
        {"role": "user", "content": "Reply with exactly: OK"},
    ], max_tokens=200)
    if not text or not text.strip():
        raise RuntimeError(f"{cfg['label']} returned an empty response")
    return {"ok": True, "provider": cfg["provider"], "model": cfg["model"], "response": text.strip()[:100]}


def generate_email(cfg, prompt):
    content = request_text(cfg, [
        {"role": "system", "content": "You are Sendwise, a senior iBOS email writer. Return valid JSON only."},
        {"role": "user", "content": prompt},
    ])
    clean = content.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean
        clean = clean.rsplit("```", 1)[0].strip()
    return json.loads(clean)
