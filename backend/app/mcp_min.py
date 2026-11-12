"""Stateless MCP-over-HTTP endpoint for Sendwise.

Implements the Model Context Protocol (Streamable HTTP, JSON-response flavour) so
AI clients (Claude Desktop, Cursor, opencode ...) can call Sendwise tools with:

  POST https://sendwise.ibos.agency/mcp
  Authorization: Bearer <MCP_TOKEN>
  Content-Type: application/json
  Accept: application/json, text/event-stream

Body = JSON-RPC 2.0 (initialize | notifications/initialized | tools/list | tools/call).
The server is stateless: no session id required.
"""
import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

PROTOCOL = "2025-03-26"
SERVER = {"name": "sendwise", "version": "1.0.0"}

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _require_mcp(authorization: str = Header(default="")):
    token = os.getenv("MCP_TOKEN", "")
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized: bad or missing MCP_TOKEN")


def _ok(result, _id):
    return JSONResponse({"jsonrpc": "2.0", "id": _id, "result": result})


def _err(code, message, _id):
    return JSONResponse({"jsonrpc": "2.0", "id": _id, "error": {"code": code, "message": message}})


# ---------------- tool registry ----------------
TOOLS = []


def tool(name, description, properties=None, required=None):
    schema = {"type": "object"}
    if properties:
        schema["properties"] = properties
        schema["required"] = required or []
    def deco(fn):
        TOOLS.append({"name": name, "description": description, "inputSchema": schema, "_fn": fn})
        return fn
    return deco


def _str(desc=""):
    return {"type": "string", "description": desc}


def _run_call(name, args):
    for t in TOOLS:
        if t["name"] == name:
            return t["_fn"](**{k: v for k, v in (args or {}).items()})
    raise KeyError(name)


# --- tools ---
def _j(x):
    return json.dumps(x, default=str, ensure_ascii=False)


@tool("list_campaigns", "List campaigns: status, audience mode/segment, per-email send state.",
      properties={})
def t_list_campaigns():
    from .mcp_server import list_campaigns as _f
    return _f()


@tool("get_campaign", "Get one campaign: brief, audience options, emails, send state.",
      properties={"slug": _str("campaign slug")}, required=["slug"])
def t_get_campaign(slug: str = ""):
    from .mcp_server import get_campaign as _f
    return _f(slug)


@tool("list_sequences", "List lifecycle sequences with stages, delays, routing.",
      properties={})
def t_sequences():
    from .mcp_server import list_sequences as _f
    return _f()


@tool("duplicate_sequence", "Duplicate a sequence (emails, branches, delays).",
      properties={"slug": _str("sequence slug")}, required=["slug"])
def t_dup_seq(slug: str = ""):
    from .mcp_server import duplicate_sequence as _f
    return _f(slug)


@tool("generate_copy", "Draft with Agent: regenerate email copy+HTML from brand MD.",
      properties={"slug": _str("campaign slug"),
                  "llm_provider": _str("'active' (configured AI) or 'rule_based'")},
      required=["slug"])
def t_gen(slug: str = "", llm_provider: str = "active"):
    from .mcp_server import generate_copy as _f
    return _f(slug, llm_provider)


@tool("launch_campaign", "Send an approved send record through SendClean to its stored recipients.",
      properties={"slug": _str("campaign slug")}, required=["slug"])
def t_launch(slug: str = ""):
    from .mcp_server import launch_campaign as _f
    return _f(slug)


@tool("sendclean_status", "Active provider, configuration state and available SendClean capabilities.",
      properties={})
def t_sc_status():
    from .mcp_server import sendclean_status as _f
    return _f()


@tool("sendclean_diagnose",
      "Item-by-item SendClean connection diagnosis (missing key, undecryptable token, "
      "rejected credentials, network) plus a live account probe.",
      properties={})
def t_sc_diagnose():
    from .mcp_server import sendclean_diagnose as _f
    return _f()


@tool("delivery_ledger",
      "Delivery counts from Sendwise's own ledger - no SendClean call; works when the "
      "provider is down or its token is broken.",
      properties={"campaign_slug": _str("campaign slug (optional)"),
                  "batch": _str("launch batch id (optional)"),
                  "limit": {"type": "integer", "description": "recent rows to return"}})
def t_delivery_ledger(campaign_slug: str = "", batch: str = "", limit: int = 100):
    from .mcp_server import delivery_ledger as _f
    return _f(campaign_slug, batch, limit)


@tool("sendclean_overview", "Live SendClean account, SMTP users, domains and webhooks.",
      properties={})
def t_sc_overview():
    from .mcp_server import sendclean_overview as _f
    return _f()


@tool("sendclean_message_info", "SendClean delivery/open/click activity for one X-Unique-Id.",
      properties={"x_unique_id": _str("the X-Unique-Id used at send time"),
                  "skip_page": {"type": "integer", "description": "pagination offset (optional)"}},
      required=["x_unique_id"])
def t_sc_message(x_unique_id: str = "", skip_page: int = 0):
    from .mcp_server import sendclean_message_info as _f
    return _f(x_unique_id, skip_page)


@tool("sendclean_check_domain", "Check DKIM/SPF validity for a SendClean sending domain.",
      properties={"domain": _str("sending domain")}, required=["domain"])
def t_sc_domain(domain: str = ""):
    from .mcp_server import sendclean_check_domain as _f
    return _f(domain)


@tool("sendclean_send_message",
      "Send one transactional email via SendClean. Explicit recipients required; no fixed application limit.",
      properties={"subject": _str("subject line"),
                  "from_email": _str("verified sender address"),
                  "recipients": {"type": "array", "items": {"type": "string"},
                                 "description": "recipient email addresses"},
                  "html": _str("HTML body"),
                  "text": _str("plain-text body"),
                  "from_name": _str("sender display name"),
                  "reply_to": _str("reply-to address"),
                  "x_unique_id": _str("optional tracking id")},
      required=["subject", "from_email", "recipients"])
def t_sc_send(subject: str = "", from_email: str = "", recipients=None, html: str = "",
              text: str = "", from_name: str = "", reply_to: str = "", x_unique_id: str = ""):
    from .mcp_server import sendclean_send_message as _f
    return _f(subject, from_email, recipients or [], html, text, from_name, reply_to, x_unique_id)


@tool("server_time", "Current UTC server time.", properties={})
def t_time():
    from .mcp_server import server_time as _f
    return _f()


@tool("list_mcp_tools_help", "This Sendwise MCP tool catalogue (names + purpose).",
      properties={})
def t_help():
    return _j([{"name": t["name"], "description": t["description"]} for t in TOOLS])


# ---------------- JSON-RPC handlers ----------------
@router.post("")
@router.post("/")
def mcp_post(body: dict, _auth=Depends(_require_mcp)):
    method = body.get("method", "")
    _id = body.get("id")
    try:
        if method == "initialize":
            return _ok({
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER,
                "instructions": "Sendwise SendClean email orchestrator for iBOS. SendClean has no audience API - confirm the explicit recipient list before any real send.",
            }, _id)
        if method == "notifications/initialized":
            return JSONResponse({"jsonrpc": "2.0"})
        if method == "ping":
            return _ok({}, _id)
        if method == "tools/list":
            return _ok({"tools": [{"name": t["name"], "description": t["description"],
                                   "inputSchema": t["inputSchema"]} for t in TOOLS]}, _id)
        if method == "tools/call":
            name = (body.get("params") or {}).get("name", "")
            args = (body.get("params") or {}).get("arguments", {})
            out = _run_call(name, args)
            text = out if isinstance(out, str) else _j(out)
            return _ok({"content": [{"type": "text", "text": text}]}, _id)
        return _err(-32601, f"Method not found: {method}", _id)
    except KeyError:
        return _err(-32602, "Invalid params: tool not found", _id)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401)
    except TypeError as e:
        return _err(-32602, f"Invalid arguments: {e}", _id)
    except Exception as e:  # noqa: BLE001
        return _err(-32603, f"Tool error: {e}", _id)


@router.get("")
@router.get("/")
def mcp_get():
    return JSONResponse({"error": "Use POST (JSON-RPC) to talk to Sendwise MCP."})
