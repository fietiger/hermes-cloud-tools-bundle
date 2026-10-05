import functools
import json
import os
import urllib.error
import urllib.request
import urllib.parse
from typing import Any, Dict, Optional

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
# 0b. Schema-driven argument normalization
# ==============================================================================
# A model reliably emits three malformations for a scalar parameter, and each
# one used to reach the handler and break it from the inside:
#   kv_get(key={"key": "ai/junshi"})     -> TypeError: quote_from_bytes() expected bytes
#   sms_list_reminders(limit="50")       -> slice indices must be integers
#   sms_toggle_reminder(enabled="true")  -> a non-empty string is always truthy
# The declared type lives in the tool's OWN schema, so deriving the repair from
# that schema keeps it correct as schemas change and needs no per-handler code.
#
# Every repair here is best-effort and never raises: a value whose category still
# cannot satisfy the schema produces a named error instead of an opaque TypeError
# from deep inside a handler.

_JSON_SCALARS = (str, int, float, bool)


def _schema_type(spec: Any) -> Optional[str]:
    """The declared JSON Schema type, ignoring a null union member."""
    declared = (spec or {}).get("type")
    if isinstance(declared, list):
        declared = next((t for t in declared if t != "null"), None)
    return declared if isinstance(declared, str) else None


def _is_scalar(value: Any) -> Any:
    return isinstance(value, _JSON_SCALARS)


def _unwrap_scalar(value: Any) -> Any:
    """Recover a scalar from a single-entry wrapper dict: {"key": "x"} -> "x".

    Only unwraps when the wrapped value is itself a scalar, so an intentionally
    structured argument is never flattened.
    """
    if not isinstance(value, dict) or len(value) != 1:
        return value
    (inner,) = value.values()
    return inner if _is_scalar(inner) else value


def _to_integer(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return value
    if isinstance(value, int):
        return value
    # A float that is exactly integral is a model emitting "50.0" for limit:50;
    # a genuinely fractional value cannot satisfy an integer parameter.
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else value


def _to_boolean(value: Any) -> Any:
    """Only the unambiguous spellings; anything else is left for the handler."""
    if isinstance(value, str):
        folded = value.strip().lower()
        if folded in ("true", "yes", "1"):
            return True
        if folded in ("false", "no", "0"):
            return False
    return value


def _to_object(value: Any) -> Any:
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return parsed if isinstance(parsed, dict) else value
    return value


def _normalize_arg(value: Any, spec: Any) -> Any:
    """Best-effort repair of one argument toward its declared type."""
    expected = _schema_type(spec)
    if expected is None:
        return value
    if expected in ("string", "integer", "number", "boolean"):
        value = _unwrap_scalar(value)
    if expected == "string":
        return value if isinstance(value, str) else str(value) if _is_scalar(value) else value
    if expected in ("integer", "number"):
        return _to_integer(value)
    if expected == "boolean":
        return _to_boolean(value)
    if expected == "object":
        return _to_object(value)
    return value


def _arg_is_usable(value: Any, spec: Any) -> bool:
    """False when the value's category still cannot satisfy the declared type."""
    expected = _schema_type(spec)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        # A float reaching an integer parameter (a slice, an id) breaks the
        # handler even though its category looks close enough.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _arg_error(tool: str, param: str, spec: Any, received: Any) -> str:
    return json.dumps({
        "error": "ArgumentError",
        "tool": tool,
        "argument": param,
        "expected_type": _schema_type(spec),
        "received_type": type(received).__name__,
        "message": (
            f"{tool}: argument '{param}' must be {_schema_type(spec)}, got "
            f"{type(received).__name__}. Pass the value itself, not a dict that "
            f"describes it (e.g. key=\"ai/junshi\", not key={{\"key\": \"ai/junshi\"}})."
        ),
    }, ensure_ascii=False)


def _with_schema_coercion(name: str, schema: dict, handler):
    """Wrap *handler* so keyword arguments are normalized to the schema's types."""
    properties = ((schema or {}).get("parameters") or {}).get("properties") or {}

    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        if args:  # positional call: leave the calling convention alone
            return handler(*args, **kwargs)
        for param, spec in properties.items():
            if param not in kwargs:
                continue
            received = kwargs[param]
            value = _normalize_arg(received, spec)
            if not _arg_is_usable(value, spec):
                return _arg_error(name, param, spec, received)
            kwargs[param] = value
        return handler(**kwargs)

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
    'name': 'kv_get',
    'description': 'Read a value from Cloudflare KV by key. Use this for secrets and small config the user keeps in kvbox (e.g. openrouter/api_key, xiaoniu/ssh_password_b64, ai/junshi). Prefer it over terminal+curl: it needs no approval prompt and the key is URL-encoded for you. Returns {"found": false} for a missing key, never a false success.',
    'parameters': {
        'type': 'object',
        'properties': {
            'key': {
                'type': 'string',
                'description': 'Key name in Cloudflare KV',
            },
        },
        'required': ['key'],
    },
}

KV_PUT_SCHEMA = {
    'name': 'kv_put',
    'description': 'Store a value in Cloudflare KV, optionally with a TTL in seconds. Use to persist a credential or config fragment the user wants to retrieve later with kv_get. Values may be strings, numbers, or JSON objects.',
    'parameters': {
        'type': 'object',
        'properties': {
            'key': {
                'type': 'string',
                'description': 'Key name in Cloudflare KV',
            },
            'value': {
                'description': 'Value to store (string, number, dict, list, etc.)',
            },
            'ttl': {
                'type': 'integer',
                'description': 'Optional time-to-live in seconds',
            },
        },
        'required': ['key', 'value'],
    },
}

KV_LIST_SCHEMA = {
    'name': 'kv_list',
    'description': 'List Cloudflare KV keys matching a prefix. Use to discover what is stored before reading a specific key — kvbox is not enumerable any other way. Returns key names plus a cursor; page with a larger limit.',
    'parameters': {
        'type': 'object',
        'properties': {
            'prefix': {
                'type': 'string',
                'description': 'Key prefix filter',
                'default': '',
            },
            'limit': {
                'type': 'integer',
                'description': 'Maximum number of keys',
                'default': 100,
            },
        },
    },
}

KV_DELETE_SCHEMA = {
    'name': 'kv_delete',
    'description': 'Delete a key from Cloudflare KV. Destructive and irreversible — only call it when the user asked for that key to be removed.',
    'parameters': {
        'type': 'object',
        'properties': {
            'key': {
                'type': 'string',
                'description': 'Key to delete',
            },
        },
        'required': ['key'],
    },
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
    'name': 'diary_write',
    'description': 'Append a diary entry. Use when the user records something personal worth keeping (reflection, work log, mood). Content is required; title, mood and date default sensibly.',
    'parameters': {
        'type': 'object',
        'properties': {
            'content': {
                'type': 'string',
                'description': 'Diary body text content',
            },
            'title': {
                'type': 'string',
                'description': 'Optional title of the diary entry',
            },
            'mood': {
                'type': 'string',
                'description': 'Mood rating (e.g. happy, neutral, busy, sad)',
                'default': 'happy',
            },
            'date': {
                'type': 'string',
                'description': 'Date in YYYY-MM-DD (defaults to today)',
            },
        },
        'required': ['content'],
    },
}

DIARY_LIST_SCHEMA = {
    'name': 'diary_list',
    'description': 'List the most recent diary entries, newest first. Use to recall or summarize what the user has logged.',
    'parameters': {
        'type': 'object',
        'properties': {
            'limit': {
                'type': 'integer',
                'description': 'Number of recent diaries to list',
                'default': 10,
            },
        },
    },
}

DIARY_SEARCH_SCHEMA = {
    'name': 'diary_search',
    'description': 'Search diary entries by keyword and/or date range. The keyword is matched client-side against titles and content; the server filters by date only. Pass a keyword alone for a full-text sweep, or from_date/to_date for a period.',
    'parameters': {
        'type': 'object',
        'properties': {
            'keyword': {
                'type': 'string',
                'description': 'Search keyword in diary content/title',
            },
            'from_date': {
                'type': 'string',
                'description': 'Start date in YYYY-MM-DD',
            },
            'to_date': {
                'type': 'string',
                'description': 'End date in YYYY-MM-DD',
            },
        },
    },
}

DIARY_DELETE_SCHEMA = {
    'name': 'diary_delete',
    'description': 'Delete a diary entry by its numeric id. Destructive — only call it when the user asked for that entry to be removed. Use diary_list to find the id.',
    'parameters': {
        'type': 'object',
        'properties': {
            'diary_id': {
                'type': 'integer',
                'description': 'Diary record ID to delete',
            },
        },
        'required': ['diary_id'],
    },
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
    'name': 'sms_send',
    'description': "Send an immediate SMS to the user. For SCHEDULED or recurring alerts use sms_create_reminder instead — that is the user's standard channel for every reminder, bill, appointment and todo.",
    'parameters': {
        'type': 'object',
        'properties': {
            'message': {
                'type': 'string',
                'description': 'SMS content to deliver',
            },
            'phone': {
                'type': 'string',
                'description': 'Optional recipient phone number',
            },
        },
        'required': ['message'],
    },
}

SMS_REMINDER_SCHEMA = {
    'name': 'sms_create_reminder',
    'description': "Schedule an SMS reminder. This is the correct tool for ANY timed or recurring notification (bills, medication, appointments, todos) — the user wants all of them through this reminder hub, not local cron. One-shot: pass run_at as Beijing time YYYY-MM-DDTHH:MM, or a delay_* value to fire N minutes/hours/days from now. Recurring: frequency 'weekly' needs weekday, 'monthly' needs monthly_day (e.g. a bill due on the 8th -> frequency='monthly', monthly_day=7, run_at='...T09:00').",
    'parameters': {
        'type': 'object',
        'properties': {
            'title': {
                'type': 'string',
                'description': 'Reminder task title',
            },
            'message': {
                'type': 'string',
                'description': 'SMS reminder body',
            },
            'run_at': {
                'type': 'string',
                'description': 'Absolute Beijing time, format YYYY-MM-DDTHH:MM. Use for one-shot reminders.',
            },
            'frequency': {
                'type': 'string',
                'enum': ['once', 'weekly', 'monthly', 'yearly', 'interval'],
                'description': "Recurrence. Default 'once'. Use 'monthly' with monthly_day for repeating bills.",
            },
            'delay_minutes': {
                'type': 'integer',
                'description': 'Fire N minutes from now (alternative to run_at)',
            },
            'delay_hours': {
                'type': 'integer',
                'description': 'Fire N hours from now (alternative to run_at)',
            },
            'delay_days': {
                'type': 'integer',
                'description': 'Fire N days from now (alternative to run_at)',
            },
            'weekday': {
                'type': 'integer',
                'description': "0=Sunday .. 6=Saturday. Required when frequency='weekly'.",
            },
            'weekly_time': {
                'type': 'string',
                'description': 'HH:mm for weekly reminders, default 09:00',
            },
            'monthly_day': {
                'type': 'integer',
                'description': "Day of month 1-31 for frequency='monthly' (e.g. 14 = every month on the 14th)",
            },
            'auto_delete': {
                'type': 'boolean',
                'description': "Delete task after it completes. Default true; only meaningful for frequency='once'.",
            },
        },
        'required': ['title', 'message'],
    },
}

SMS_LIST_REMINDERS_SCHEMA = {
    'name': 'sms_list_reminders',
    'description': 'List scheduled SMS reminder tasks with their ids, recurrence and next fire time. Use before toggling or deleting a task, since both need the task_id.',
    'parameters': {
        'type': 'object',
        'properties': {
            'limit': {
                'type': 'integer',
                'description': 'Max tasks to return, default 50',
            },
        },
    },
}

SMS_TASK_ID_SCHEMA = {
    'name': 'sms_delete_reminder',
    'description': 'Delete a reminder task by id. Obtain the id from sms_list_reminders. Destructive — only call it when the user asked for that reminder to go.',
    'parameters': {
        'type': 'object',
        'properties': {
            'task_id': {
                'type': 'string',
                'description': 'Reminder task id (uuid) returned by sms_create_reminder',
            },
        },
        'required': ['task_id'],
    },
}

SMS_TOGGLE_SCHEMA = {
    'name': 'sms_toggle_reminder',
    'description': 'Pause or resume a reminder task without deleting it (enabled=false pauses, true resumes). Use this over delete when the user wants a reminder to stop for now but keep it.',
    'parameters': {
        'type': 'object',
        'properties': {
            'task_id': {
                'type': 'string',
                'description': 'Reminder task id',
            },
            'enabled': {
                'type': 'boolean',
                'description': 'true to enable, false to pause',
            },
        },
        'required': ['task_id'],
    },
}

SMS_LOGS_SCHEMA = {
    'name': 'sms_logs',
    'description': 'Fetch recent SMS delivery history — what was sent, when, and whether it went out. Use to check whether a reminder actually fired, or to debug a message the user says never arrived.',
    'parameters': {
        'type': 'object',
        'properties': {
            'limit': {
                'type': 'integer',
                'description': 'Max log entries, default 50',
            },
        },
    },
}

SMS_QUOTA_SCHEMA = {
    'name': 'sms_quota',
    'description': 'Check the remaining SMS quota and when the window resets. Use before scheduling a burst of reminders, or when a send fails and quota is the suspected cause.',
    'parameters': {
        'type': 'object',
        'properties': {},
    },
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
)

# Each tool is wrapped with the normalization its own schema declares, so the
# repair follows the schema instead of duplicating type knowledge per handler.
ALL_TOOLS = tuple(
    (name, schema, _with_schema_coercion(name, schema, handler), emoji)
    for name, schema, handler, emoji in ALL_TOOLS
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
