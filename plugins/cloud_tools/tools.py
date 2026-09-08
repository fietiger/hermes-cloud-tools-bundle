import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# ==============================================================================
# 1. Cloudflare KV Tools (kvbox)
# ==============================================================================
KV_DEFAULT_URL = "https://kv.benext.uk"

def _get_kv_token() -> str:
    token = os.getenv("KVBOX_TOKEN")
    if not token:
        for p in ["/opt/data/.kvbox_token", "/root/.hermes/.kvbox_token"]:
            if os.path.exists(p):
                return open(p).read().strip()
    return token or "CpxaDyRfzW2fKOQqPC8kDRufUCT5MpK6np9rIHgk7zg"

def _kv_req(method: str, path: str, body: Any = None) -> Dict[str, Any]:
    url = f"{os.getenv('KVBOX_URL', KV_DEFAULT_URL).rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_get_kv_token()}")
    req.add_header("User-Agent", "hermes-agent-tool/1.0")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}

def handle_kv_get(key: str, **kwargs) -> Dict[str, Any]:
    """Get value by key from Cloudflare KV."""
    res = _kv_req("GET", f"/keys/{key}")
    if res.get("error") == "not found":
        return {"found": False, "key": key, "value": None}
    return {"found": True, "key": key, "value": res.get("value", res)}

def handle_kv_put(key: str, value: Any, ttl: Optional[int] = None, **kwargs) -> Dict[str, Any]:
    """Put key-value pair into Cloudflare KV."""
    body: Dict[str, Any] = {"value": value}
    if ttl:
        body["ttl"] = int(ttl)
    return _kv_req("PUT", f"/keys/{key}", body)

def handle_kv_list(prefix: str = "", limit: int = 100, **kwargs) -> Dict[str, Any]:
    """List keys from Cloudflare KV with prefix."""
    qs = urllib.parse.urlencode({"prefix": prefix, "limit": limit})
    return _kv_req("GET", f"/list?{qs}")

def handle_kv_delete(key: str, **kwargs) -> Dict[str, Any]:
    """Delete a key from Cloudflare KV."""
    return _kv_req("DELETE", f"/keys/{key}")

KV_GET_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "Key name in Cloudflare KV"}
    },
    "required": ["key"]
}

KV_PUT_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "Key name in Cloudflare KV"},
        "value": {"description": "Value to store (string, number, dict, list, etc.)"},
        "ttl": {"type": "integer", "description": "Optional time-to-live in seconds"}
    },
    "required": ["key", "value"]
}

KV_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "prefix": {"type": "string", "description": "Key prefix filter", "default": ""},
        "limit": {"type": "integer", "description": "Maximum number of keys", "default": 100}
    }
}

KV_DELETE_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "Key to delete"}
    },
    "required": ["key"]
}


# ==============================================================================
# 2. Diary Tools (diary.benext.uk)
# ==============================================================================
DIARY_DEFAULT_URL = "https://diary.benext.uk"

def _get_diary_client():
    try:
        from clients.diary_client import DiaryClient
        return DiaryClient()
    except Exception:
        # 尝试直接按路径载入
        import importlib.util
        for p in ["/opt/data/clients/diary_client.py", "/root/.hermes/diary_client.py"]:
            if os.path.exists(p):
                spec = importlib.util.spec_from_file_location("diary_client", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.DiaryClient()
    raise RuntimeError("无法初始化 DiaryClient，缺少凭据文件")

def handle_diary_write(content: str, title: str = "", mood: str = "happy", date: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Write a new diary entry."""
    client = _get_diary_client()
    return client.write(content=content, title=title, mood=mood, date=date)

def handle_diary_list(limit: int = 10, **kwargs) -> Any:
    """List recent diary entries."""
    client = _get_diary_client()
    return client.list_diaries(limit=limit)

def handle_diary_search(keyword: str = "", from_date: str = "", to_date: str = "", **kwargs) -> Any:
    """Search diaries by keyword and/or date range."""
    client = _get_diary_client()
    return client.search(keyword=keyword, from_date=from_date, to_date=to_date)

def handle_diary_delete(diary_id: int, **kwargs) -> Dict[str, Any]:
    """Delete a diary entry by ID."""
    client = _get_diary_client()
    return client.delete(entry_id=diary_id)

DIARY_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string", "description": "Diary body text content"},
        "title": {"type": "string", "description": "Optional title of the diary entry"},
        "mood": {"type": "string", "description": "Mood rating (e.g. happy, neutral, busy, sad)", "default": "happy"},
        "date": {"type": "string", "description": "Date in YYYY-MM-DD (defaults to today)"}
    },
    "required": ["content"]
}

DIARY_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "description": "Number of recent diaries to list", "default": 10}
    }
}

DIARY_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string", "description": "Search keyword in diary content/title"},
        "from_date": {"type": "string", "description": "Start date in YYYY-MM-DD"},
        "to_date": {"type": "string", "description": "End date in YYYY-MM-DD"}
    }
}

DIARY_DELETE_SCHEMA = {
    "type": "object",
    "properties": {
        "diary_id": {"type": "integer", "description": "Diary record ID to delete"}
    },
    "required": ["diary_id"]
}


# ==============================================================================
# 3. Turtle SMS Tools (sms.benext.uk)
# ==============================================================================
SMS_DEFAULT_URL = "https://sms.benext.uk"

def _get_sms_client():
    try:
        from clients.sms_client import SMSClient
        return SMSClient()
    except Exception:
        import importlib.util
        for p in ["/opt/data/clients/sms_client.py", "/root/.hermes/sms_client.py"]:
            if os.path.exists(p):
                spec = importlib.util.spec_from_file_location("sms_client", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.SMSClient()
    raise RuntimeError("无法初始化 SMSClient")

def handle_sms_send(message: str, phone: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Send an instant SMS message."""
    client = _get_sms_client()
    return client.send_sms(message=message, phone=phone)

def handle_sms_create_reminder(title: str, message: str, run_at: str, **kwargs) -> Dict[str, Any]:
    """Create a scheduled SMS reminder."""
    client = _get_sms_client()
    return client.create_task(message=message, run_at=run_at, title=title, frequency="once")

def handle_sms_quota(**kwargs) -> Dict[str, Any]:
    """Check remaining SMS quota."""
    client = _get_sms_client()
    return client.get_quota()

SMS_SEND_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "description": "SMS content to deliver"},
        "phone": {"type": "string", "description": "Optional recipient phone number"}
    },
    "required": ["message"]
}

SMS_REMINDER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Reminder task title"},
        "message": {"type": "string", "description": "SMS reminder body"},
        "run_at": {"type": "string", "description": "ISO timestamp (e.g. 2026-09-15T09:00)"}
    },
    "required": ["title", "message", "run_at"]
}

SMS_QUOTA_SCHEMA = {
    "type": "object",
    "properties": {}
}


# ==============================================================================
# 4. Work Hour System Tools (WHS)
# ==============================================================================
WHS_DEFAULT_URL = "https://work-hour-system.pages.dev"

def _whs_req(method: str, path: str, body: Any = None) -> Dict[str, Any]:
    url = f"{os.getenv('WHS_URL', WHS_DEFAULT_URL).rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "hermes-agent-tool/1.0")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}

def handle_whs_add_report(username: str, project: str, content: str, start_time: str = "08:30", end_time: str = "17:30", date: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Submit a daily work report."""
    tz = timezone(timedelta(hours=8))
    date_str = date or datetime.now(tz).strftime("%Y-%m-%d")
    body = {
        "username": username,
        "date": date_str,
        "project": project,
        "start_time": start_time,
        "end_time": end_time,
        "content": content
    }
    return _whs_req("POST", "/api/reports", body)

def handle_whs_list_reports(username: Optional[str] = None, limit: int = 20, **kwargs) -> Dict[str, Any]:
    """List work reports."""
    qs = urllib.parse.urlencode({"username": username or "", "limit": limit})
    return _whs_req("GET", f"/api/reports?{qs}")

def handle_whs_add_plan(username: str, project: str, start_date: str, end_date: str, **kwargs) -> Dict[str, Any]:
    """Add a project Gantt chart plan."""
    body = {
        "username": username,
        "project": project,
        "start_date": start_date,
        "end_date": end_date
    }
    return _whs_req("POST", "/api/plans", body)

WHS_ADD_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "username": {"type": "string", "description": "Worker username (e.g. nick)"},
        "project": {"type": "string", "description": "Project name"},
        "content": {"type": "string", "description": "Detailed task report content"},
        "start_time": {"type": "string", "description": "Start time (HH:MM)", "default": "08:30"},
        "end_time": {"type": "string", "description": "End time (HH:MM)", "default": "17:30"},
        "date": {"type": "string", "description": "Date (YYYY-MM-DD, defaults to today)"}
    },
    "required": ["username", "project", "content"]
}

WHS_LIST_REPORTS_SCHEMA = {
    "type": "object",
    "properties": {
        "username": {"type": "string", "description": "Optional username filter"},
        "limit": {"type": "integer", "description": "Maximum items", "default": 20}
    }
}

WHS_ADD_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "username": {"type": "string", "description": "Owner username"},
        "project": {"type": "string", "description": "Project title"},
        "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
        "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"}
    },
    "required": ["username", "project", "start_date", "end_date"]
}


# ==============================================================================
# Master Tools Registry List & Hermes Entrypoint
# ==============================================================================
ALL_TOOLS = (
    # KV Tools
    ("kv_get", KV_GET_SCHEMA, handle_kv_get, "🗄️"),
    ("kv_put", KV_PUT_SCHEMA, handle_kv_put, "💾"),
    ("kv_list", KV_LIST_SCHEMA, handle_kv_list, "📋"),
    ("kv_delete", KV_DELETE_SCHEMA, handle_kv_delete, "🗑️"),

    # Diary Tools
    ("diary_write", DIARY_WRITE_SCHEMA, handle_diary_write, "📔"),
    ("diary_list", DIARY_LIST_SCHEMA, handle_diary_list, "📖"),
    ("diary_search", DIARY_SEARCH_SCHEMA, handle_diary_search, "🔍"),
    ("diary_delete", DIARY_DELETE_SCHEMA, handle_diary_delete, "❌"),

    # SMS Tools
    ("sms_send", SMS_SEND_SCHEMA, handle_sms_send, "📱"),
    ("sms_create_reminder", SMS_REMINDER_SCHEMA, handle_sms_create_reminder, "⏰"),
    ("sms_quota", SMS_QUOTA_SCHEMA, handle_sms_quota, "📊"),

    # WHS Tools
    ("whs_add_report", WHS_ADD_REPORT_SCHEMA, handle_whs_add_report, "⏱️"),
    ("whs_list_reports", WHS_LIST_REPORTS_SCHEMA, handle_whs_list_reports, "📑"),
    ("whs_add_plan", WHS_ADD_PLAN_SCHEMA, handle_whs_add_plan, "📅"),
)

def register_tools(ctx) -> None:
    """Standard Hermes v0.21 entrypoint for tool discovery."""
    for name, schema, handler, emoji in ALL_TOOLS:
        try:
            ctx.register_tool(
                name=name,
                toolset="cloud_tools",
                schema=schema,
                handler=handler,
                emoji=emoji,
            )
        except Exception:
            pass
