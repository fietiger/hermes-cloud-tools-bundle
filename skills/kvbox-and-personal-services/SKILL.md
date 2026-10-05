---
name: kvbox-and-personal-services
description: "Use when a secret, reminder, or diary entry is needed."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [kvbox, cloudflare-kv, secrets, sms, reminders, diary, tools]
    category: productivity
    related_skills: [sms-reminder-system, self-hosted-personal-services, git-repo-publishing]
---

# kvbox,Reminders and Diary: Use the Tools

## When to Use

Reaching this skill means the answer isprobably in kvbox, the reminder hub, or the
diary. **Call the tool. Do not shell out.**

- Any credential, config fragment, address, or note stored in kvbox -> `kv_get` /
  `kv_list`
- Any timed or recurring alert (bill, medication, appointment, todo) ->
  `sms_create_reminder`
- What the user logged or wants to recall -> `diary_list` / `diary_search`

These 15 tools ship in the `cloud_tools` plugin. They exist because the user asked
for exactly this: **no approval prompt for routine reads and writes.** Hand-rolling
`terminal` + `python3 -c` + `curl` to do what `kv_get` does in one call defeats the
point and triggers an approval the user then has to answer.

**If a tool call returns an error, do not silently switch to `curl`.** Report the
error and stop; the tool is the path of least resistance for a reason, and a
shell fallback both hides the defect and reintroduces the prompt. (If you already
fell back once, say so plainly rather than presenting the result as if the tool
had worked.)

Verified 2026-10-05: a session asking for `github/token_meta` completed in 19s
using the tool alone, no terminal.

## Rule

**Tool first, shell only for what no tool covers.** If `cloud_tools` exposes it,
call it. Reach for `terminal` only when the operation is genuinely outside these
15 tools — and then say which tool was missing.

Do not interleave the two: reading a value with `curl` and then writing it with
`kv_put` is worse than either, because the read bypassed the schema that would
have told you what you actually had.

## kvbox keys, as they really exist

`kv_list` is the only way to discover these; the layout is not obvious and
guessing wastes a round trip. Verified inventory (28 keys, `list_complete: true`):

| Prefix / key | Holds |
|---|---|
| `github/token`, `github/token_meta` | the GitHub PAT (`meta` carries owner, scopes, notes) |
| `openrouter/api_key` | OpenRouter key |
| `antigravity/token` | antigravity2api caller token |
| `cloudflare/api_token` | Cloudflare API token |
| `cbproxy/api_key` | cbproxy key |
| `qoder/api_key`, `qoder/pat`, `qoder/access`, `qoder/dashboard` | Qoder CLI credentials |
| `xiaoniu/ssh`, `xiaoniu/ssh_password_b64`, `xiaoniu/ssh_password` | VPS SSH host/port/user + password (base64 variant is the one to read) |
| `xiaoniu/or_key_*` | per-task OpenRouter keys |
| `ai/junshi` | 军师AI (Antigravity2Api) endpoint + model config |
| `company/chuangkai/wifi`, `wifi/chuangkai` | office wifi |
| `company/shenzhen_address` | address |
| `admin-ck@192.168.66.12` | a host credential |
| `test`, `test2`, `test-*`, `article:*` | **junk — leave them alone** |

Keys contain `/` routinely. That is normal and the tool URL-encodes for you; never
hand-build the path yourself.

**Scan before you fetch.** `kv_list(prefix="github/")` is one cheap call and tells
you the exact key names. The `kvbox` 404 is indistinguishable from "no such
credential", so a guessed name reads as "auth is broken" and sends you diagnosing
the wrong thing. (Memory records `openrouter/api_key` and
`xiaoniu/ssh_password_b64`; the inventory above is the authority when they
disagree.)

**`github/token` returns a dict**, not a bare string — `{"key":..., "value":...}`.
A value may also be a **masked preview** (`ghp_Vc...FcfB`); `github/token_meta`
names the owner and scopes and is the pointer to the full value. Unwrap defensively
and assert a plausible length (`ghp_` + 36 chars) before use.

## Reminders go through the hub, always

`user preference: every timed alert — bill, appointment, medication, todo, follow-up — is created
with sms_create_reminder. Never a local cronjob or a WeChat nudge.`

One-shot: `run_at="2026-10-08T09:00"` (Beijing time, `YYYY-MM-DDTHH:MM`) or a
`delay_minutes` / `delay_hours` / `delay_days`.
Recurring: `frequency="monthly", monthly_day=7, run_at="...T09:00"` for a bill due
on the 8th; `frequency="weekly"` needs `weekday` (0=Sunday).
The API returns `run_at` as UTC — 09:00 CST comes back `T01:00:00.000Z`. That is
correct, not an off-by-8h bug.

`enabled="false"` on `sms_toggle_reminder` is the one call worth double-checking:
before the hardening layer existed, a non-empty string was truthy, so `"false"`
silently **enabled** a paused reminder. It is fixed and now coerced from the
schema — but a wrong recurrence is a real SMS to the user, so read back the return.

## Diary

`diary_search` filters `keyword` client-side (the server filters by date only), so
a keyword search over a wide range fetches the range first. `diary_delete` takes
the numeric `diary_id` from `diary_list` — it is destructive, so only call it when
the user actually asked for that entry to go.

## When a tool returns an error, read it before retrying

Every handler returns a JSON envelope, and the errors are specific on purpose:

- `{"found": false, ...}` — the key does not exist. That is an answer, not a bug;
  do not retry it as if transient.
- `{"error": "ArgumentError", "expected_type": ..., "received_type": ...}` — the
  model sent the wrong shape. Fix the call; do not shell out around the tool.
- `{"error": "RuntimeError", "message": "KV token not found: ..."}` — the token
  file/env is missing. That is an operator action, not something to work around.

If a handler still seems broken, the plugin's own triage ladder is in
`hermes-plugin-tool-usability` (opt-in enablement, duplicate copies, schema
shape, which copy the runtime loaded).

## Do not hand-roll kvbox HTTP

If you genuinely must script it (a bulk migration, a server-side cron), the two
things that make it work:

```bash
# 1. custom User-Agent, always — the Cloudflare WAF 403s default python UAs.
#    A 403 from kv.benext.uk is a UA block, not a credential failure.
curl -H "Authorization: Bearer $(cat /opt/data/.kvbox_token)" \
     -A 'hermes-agent-tool/1.0' "https://kv.benext.uk/keys/<url-encoded-key>"
```

`X-KVBOX-Token` alone returns 401; `Authorization: Bearer` alone works. Reads are
`GET /keys/<name>`, lists are `GET /list?prefix=&limit=`, writes are
`PUT /keys/<name>` with `{"value": ..., "ttl": ...}`. Secrets belong in a
`chmod 600` file or the environment, never in argv or a URL.
