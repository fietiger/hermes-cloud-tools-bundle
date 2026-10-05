import functools
import json
import os
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# ==============================================================================
# 0. Shared helper: uniform tool response envelope
# ==============================================================================
def _safe(fn):
    """Wrap a handler so it always returns a serialized JSON string.

    - a returned str passes through unchanged
    - any other return value is json.dumps(..., ensure_ascii=False, default=str)
    - an escaping exception becomes {"error": <type>, "message": <str>}
      instead of a traceback reaching the model.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            res = fn(*args, **kwargs)
        except Exception as e:
            return json.dumps(
                {"error": type(e).__name__, "message": str(e)},
                ensure_ascii=False, default=str,
            )
        if isinstance(res, str):
            return res
        return json.dumps(res, ensure_ascii=False, default=str)
    return wrapper

# ==============================================================================
# 1. Cloudflare KV Tools (kvbox)
# ==============================================================================
KV_DEFAULT_URL = "https://kv.benext.uk"

def _get_kv_token() -> str:
    token = os.getenv("KVBOX_TOKEN")
    if token:
        return token
    for p in ["/opt/data/.kvbox_token", "/root/.hermes/.kvbox_token"]:
        if os.path.exists(p):
            return open(p).read().strip()
    raise RuntimeError(
        "KV token not found: set the KVBOX_TOKEN environment variable, or provide "
        "a token file at /opt/data/.kvbox_token or /root/.hermes/.kvbox_token"
    )

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
    except urllib.error.HTTPError as e:
        # A real HTTP status response; caller can branch on "_status" (e.g. 404).
        return {"_status": e.code, "error": str(e)}
    except Exception as e:
        # Transport / decode failure (network down, DNS, bad JSON, missing token).
        return {"_transport_error": f"{type(e).__name__}: {e}"}

@_safe
def handle_kv_list(prefix: str = "", limit: int = 100, **kwargs) -> str:
    """List keys from Cloudflare KV with prefix."""
    qs = urllib.parse.urlencode({"prefix": prefix, "limit": limit})
    res = _kv_req("GET", f"/list?{qs}")
    return json.dumps(res, ensure_ascii=False)

@_safe
def handle_kv_get(key: str, **kwargs) -> str:
    """Get value by key from Cloudflare KV."""
    res = _kv_req("GET", f"/keys/{urllib.parse.quote(key, safe='')}")
    if res.get("_status") == 404:
        return json.dumps({"found": False, "key": key, "value": None}, ensure_ascii=False)
    if res.get("_status") or res.get("_transport_error"):
        return json.dumps({"found": False, "key": key, "error": res}, ensure_ascii=False)
    return json.dumps({"found": True, "key": key, "value": res.get("value", res)}, ensure_ascii=False)

@_safe
def handle_kv_put(key: str, value: Any, ttl: Optional[int] = None, **kwargs) -> str:
    """Put key-value pair into Cloudflare KV."""
    body: Dict[str, Any] = {"value": value}
    if ttl:
        body["ttl"] = int(ttl)
    res = _kv_req("PUT", f"/keys/{urllib.parse.quote(key, safe='')}", body)
    return json.dumps(res, ensure_ascii=False)

@_safe
def handle_kv_delete(key: str, **kwargs) -> str:
    """Delete a key from Cloudflare KV."""
    res = _kv_req("DELETE", f"/keys/{urllib.parse.quote(key, safe='')}")
    return json.dumps(res, ensure_ascii=False)

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

@_safe
def handle_diary_write(content: str, title: str = "", mood: str = "happy", date: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Write a new diary entry."""
    client = _get_diary_client()
    return client.write(content=content, title=title, mood=mood, date=date)

@_safe
def handle_diary_list(limit: int = 10, **kwargs) -> Any:
    """List recent diary entries."""
    client = _get_diary_client()
    return client.list_diaries(limit=limit)

@_safe
def handle_diary_search(keyword: str = "", from_date: str = "", to_date: str = "", **kwargs) -> Any:
    """Search diaries by keyword (client-side) and/or date range.

    DiaryClient.search only accepts from_date/to_date — the server cannot filter
    by keyword. The schema advertises `keyword` to the model, so instead of
    dropping the capability (which would require editing the schema and the
    tool description) we fetch the date-range results and filter them here on a
    case-insensitive substring match against each entry's title/content. This
    keeps schema, handler signature and client all in agreement.
    """
    client = _get_diary_client()
    entries = client.search(from_date=from_date or None, to_date=to_date or None)
    if keyword and isinstance(entries, list):
        kw = keyword.lower()
        entries = [
            e for e in entries
            if kw in str(e.get("title", "")).lower()
            or kw in str(e.get("content", "")).lower()
        ]
    return entries

@_safe
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

@_safe
def handle_sms_send(message: str, phone: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Send an instant SMS message."""
    client = _get_sms_client()
    return client.send_sms(message=message, phone=phone)

@_safe
def handle_sms_create_reminder(
    title: str,
    message: str,
    run_at: Optional[str] = None,
    frequency: str = "once",
    delay_minutes: Optional[int] = None,
    delay_hours: Optional[int] = None,
    delay_days: Optional[int] = None,
    weekday: Optional[int] = None,
    weekly_time: Optional[str] = None,
    monthly_day: Optional[int] = None,
    auto_delete: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    """Create a scheduled SMS reminder (one-shot, delayed, weekly, monthly, ...)."""
    client = _get_sms_client()
    return client.create_task(
        message=message,
        title=title,
        run_at=run_at,
        frequency=frequency,
        delay_minutes=delay_minutes,
        delay_hours=delay_hours,
        delay_days=delay_days,
        weekday=weekday,
        weekly_time=weekly_time,
        monthly_day=monthly_day,
        auto_delete=auto_delete,
    )

@_safe
def handle_sms_list_reminders(limit: int = 50, **kwargs) -> Dict[str, Any]:
    """List scheduled SMS reminder tasks."""
    client = _get_sms_client()
    tasks = client.get_tasks()
    if isinstance(tasks, list):
        tasks = tasks[: int(limit)]
    return {"count": len(tasks) if isinstance(tasks, list) else 0, "tasks": tasks}

@_safe
def handle_sms_delete_reminder(task_id: str, **kwargs) -> Dict[str, Any]:
    """Delete a scheduled SMS reminder task by id."""
    client = _get_sms_client()
    return client.delete_task(task_id)

@_safe
def handle_sms_toggle_reminder(task_id: str, enabled: bool = True, **kwargs) -> Dict[str, Any]:
    """Enable or disable a scheduled SMS reminder task."""
    client = _get_sms_client()
    return client.toggle_task(task_id, enabled)

@_safe
def handle_sms_logs(limit: int = 50, **kwargs) -> Dict[str, Any]:
    """Fetch recent SMS send history / delivery logs."""
    client = _get_sms_client()
    return {"logs": client.get_logs(limit=int(limit))}

@_safe
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
        "run_at": {
            "type": "string",
            "description": "Absolute Beijing time, format YYYY-MM-DDTHH:MM. Use for one-shot reminders.",
        },
        "frequency": {
            "type": "string",
            "enum": ["once", "weekly", "monthly", "yearly", "interval"],
            "description": "Recurrence. Default 'once'. Use 'monthly' with monthly_day for repeating bills.",
        },
        "delay_minutes": {"type": "integer", "description": "Fire N minutes from now (alternative to run_at)"},
        "delay_hours": {"type": "integer", "description": "Fire N hours from now (alternative to run_at)"},
        "delay_days": {"type": "integer", "description": "Fire N days from now (alternative to run_at)"},
        "weekday": {
            "type": "integer",
            "description": "0=Sunday .. 6=Saturday. Required when frequency='weekly'.",
        },
        "weekly_time": {"type": "string", "description": "HH:mm for weekly reminders, default 09:00"},
        "monthly_day": {
            "type": "integer",
            "description": "Day of month 1-31 for frequency='monthly' (e.g. 14 = every month on the 14th)",
        },
        "auto_delete": {
            "type": "boolean",
            "description": "Delete task after it completes. Default true; only meaningful for frequency='once'.",
        },
    },
    "required": ["title", "message"],
}

SMS_LIST_REMINDERS_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "description": "Max tasks to return, default 50"}
    },
}

SMS_TASK_ID_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "Reminder task id (uuid) returned by sms_create_reminder"}
    },
    "required": ["task_id"],
}

SMS_TOGGLE_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "Reminder task id"},
        "enabled": {"type": "boolean", "description": "true to enable, false to pause"},
    },
    "required": ["task_id"],
}

SMS_LOGS_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "description": "Max log entries, default 50"}
    },
}

SMS_QUOTA_SCHEMA = {
    "type": "object",
    "properties": {}
}


# ==============================================================================
# 4. Work Hour System Tools (WHS)
# ==============================================================================
WHS_DEFAULT_URL = "https://work-hour-system.fietiger.workers.dev"

def _whs_creds() -> tuple:
    """WHS_USERNAME / WHS_PASSWORD, or a named error naming both."""
    user = os.getenv("WHS_USERNAME")
    pwd = os.getenv("WHS_PASSWORD")
    if not user or not pwd:
        raise RuntimeError(
            "WHS credentials missing: set both the WHS_USERNAME and WHS_PASSWORD "
            "environment variables"
        )
    return user, pwd

def _whs_token() -> str:
    """Log in once per process and cache the Bearer token.

    The server issues a token from POST /api/login and expects it back as
    ``Authorization: Bearer <token>`` on every later call (see
    /opt/data/clients/whs_client.py). It does NOT accept HTTP Basic.
    """
    global _WHS_TOKEN
    if _WHS_TOKEN:
        return _WHS_TOKEN
    user, pwd = _whs_creds()
    url = f"{os.getenv('WHS_URL', WHS_DEFAULT_URL).rstrip('/')}/api/login"
    req = urllib.request.Request(
        url,
        data=json.dumps({"username": user, "password": pwd}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "hermes-agent-tool/1.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"WHS login failed: HTTP {e.code} (check WHS_USERNAME/WHS_PASSWORD)")
    except Exception as e:
        raise RuntimeError(f"WHS login failed: {type(e).__name__}: {e}")
    token = body.get("token") if isinstance(body, dict) else None
    if not token:
        raise RuntimeError("WHS login returned no token — server auth scheme may have changed")
    _WHS_TOKEN = token
    return token

_WHS_TOKEN: Optional[str] = None

def _whs_req(method: str, path: str, body: Any = None) -> Dict[str, Any]:
    url = f"{os.getenv('WHS_URL', WHS_DEFAULT_URL).rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_whs_token()}")
    req.add_header("User-Agent", "hermes-agent-tool/1.0")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "error": str(e)}
    except Exception as e:
        return {"_transport_error": f"{type(e).__name__}: {e}"}

@_safe
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

@_safe
def handle_whs_list_reports(username: Optional[str] = None, limit: int = 20, **kwargs) -> Dict[str, Any]:
    """List work reports."""
    qs = urllib.parse.urlencode({"username": username or "", "limit": limit})
    return _whs_req("GET", f"/api/reports?{qs}")

@_safe
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
    ("sms_list_reminders", SMS_LIST_REMINDERS_SCHEMA, handle_sms_list_reminders, "📋"),
    ("sms_delete_reminder", SMS_TASK_ID_SCHEMA, handle_sms_delete_reminder, "🗑️"),
    ("sms_toggle_reminder", SMS_TOGGLE_SCHEMA, handle_sms_toggle_reminder, "🔀"),
    ("sms_logs", SMS_LOGS_SCHEMA, handle_sms_logs, "🧾"),
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
