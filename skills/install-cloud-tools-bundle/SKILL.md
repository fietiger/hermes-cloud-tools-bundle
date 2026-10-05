---
name: install-cloud-tools-bundle
description: "Use when installing or upgrading the cloud_tools bundle."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, plugins, install, upgrade, cloud_tools, kvbox]
    category: software-development
    related_skills: [hermes-plugin-tool-usability, kvbox-and-personal-services, safe-config-edit]
---

# Installing or Upgrading the cloud_tools Bundle

## When to Use

- The user asks to install, reinstall, upgrade, update, repair, or "fix" the
  `cloud_tools` plugin (kvbox / diary / SMS tools).
- Any symptom where the tools seem missing, duplicated, or half-working
  (`plugins list` shows no rows, duplicate rows, or an old version).
- Before changing anything, an agent that has been asked to "make these tools
  work better" should read this first — **the cause is usually a stale copy left
  by a previous install, not a defect in the code.**

## Step 0: uninstall / clean BEFORE installing (do not skip)

**Rationale: this plugin ships a `plugin.yaml`, and every directory containing one
is discovered as a plugin.** So any leftover copy — from an earlier install, a
manual `cp`, a backup, or a snapshot in an environment workspace — registers its
own set of tools under the same names. The duplicates drift apart, and which copy
a given session actually loads becomes a race that cannot be read off the source.
Two of the worst hours of this plugin's history were spent debugging a file that
was already correct while a stale sibling was the one running.

So: enumerate, remove the stale ones, install exactly one, verify one.

### 0a. Find every copy (never install on top of a guess)

```bash
find / -name tools.py -path '*cloud_tools*' -not -path '*/node_modules/*' 2>/dev/null \
  | while read f; do printf '%-95s %s\n' "$f" "$(grep -c '_with_schema_coercion' "$f")"; done
```

A `0` in the second column means that copy is **stale** — it predates the
argument-normalization fix. Grep for a symbol the fix introduced, not for a
version string.

### 0b. Confirm what the loader will actually pick

```bash
cd /usr/local/src/hermes-agent && HERMES_HOME=<HOME> \
  ./.venv/bin/python -c "
import sys; sys.path.insert(0,'/usr/local/src/hermes-agent')
from hermes_cli.plugins_discovery import collect_directory_manifests
for m in collect_directory_manifests():
    if 'cloud_tools' in str(m.path): print(m.source, m.version, m.path)
"
```

One row is the goal. Two or more = duplicates still installed.
This lists only what the real discovery sweep sees, so it also catches copies
outside `$HERMES_HOME`.

### 0c. Remove the stale copies

Do NOT delete anything inside the Hermes source tree without checking whether it
is bundled — some bundled dirs can be replaced safely, others are part of the
install. Safe cleanups in practice:

```bash
# A duplicate discovered under $HERMES_HOME (usual case)
rm -rf "$HERMES_HOME/plugins/tools/cloud_tools"     # old 'tools/' category layout
rm -rf "$HERMES_HOME/plugins/cloud_tools.bak."*     # backups must NOT sit in the scan path

# Environment snapshots — these are what single-query / CLI runs actually load
find "$HERMES_HOME/installs" -type d -path '*/workspace/plugins/cloud_tools' \
  -exec rm -rf {} + 2>/dev/null

# Stale bundled copy, if present (user copy takes precedence anyway)
rm -rf /usr/local/src/hermes-agent/plugins/cloud_tools
```

Keep a backup **outside** every scan path (e.g. `~/.hermes/backups/…`), and strip
any credential from it before it is left on disk.

### 0d. Disable it while you work

```bash
cd /usr/local/src/hermes-agent && HERMES_HOME=<HOME> \
  ./.venv/bin/python -m hermes_cli.main plugins disable cloud_tools
```

Re-enable in Step 3. This stops a half-installed state from registering duplicate
tools in a live gateway.

## Step 1: install exactly one copy

The manifest must end up directly under `$HERMES_HOME/plugins/cloud_tools/` (flat
layout). Note the repo ships its `plugin.yaml` at the **repository root**, so
after cloning you copy the plugin payload into the flat path — do not clone the
whole repo into `$HERMES_HOME/plugins/` and expect discovery to find it, and do
not leave a second `plugin.yaml` inside `plugins/cloud_tools/` if one already
exists at the path you are using.

```bash
SRC=/path/to/hermes-cloud-tools-bundle
DEST="$HERMES_HOME/plugins/cloud_tools"
rm -rf "$DEST" && mkdir -p "$DEST"
cp "$SRC/plugins/cloud_tools/tools.py" "$SRC/plugins/cloud_tools/__init__.py" "$DEST/"
cp "$SRC/plugin.yaml" "$DEST/plugin.yaml"     # manifest is at repo ROOT, not inside plugins/
```

Then clear bytecode so the new source is what loads:

```bash
find "$HERMES_HOME/plugins" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
```

**Credentials:** this plugin ships no credential. It reads `KVBOX_TOKEN` from the
environment or `<HOME>/.kvbox_token`. If a token file exists, keep it at mode 600.
Never write a token into `tools.py`.

## Step 2: verify registration

```bash
cd /usr/local/src/hermes-agent && HERMES_HOME=<HOME> \
  ./.venv/bin/python -m hermes_cli.main plugins doctor cloud_tools
```

Expect: `manifest: cloud_tools 1.2.0 (backend)` and `registrations: 15 tool(s), 0 hook(s)`.

- `ERROR: Plugin registration failed: name 'X_SCHEMA' is not defined` -> a schema
  constant was deleted or renamed; the manifest names tools whose schemas are gone.
- `WARN: registration adds tool 'X' not listed in provides_tools` -> the manifest
  and `ALL_TOOLS` disagree. `plugin.yaml` lists the tools; `plugins/cloud_tools/tools.py`
  defines them. Update both in the same commit — `plugins doctor` compares them.
- A count other than 15 -> you are running a stale copy. Back to Step 0a.

**`plugins doctor` does not validate schema nesting and does not prove the model
can call a tool.** It checks manifest parsing, import and registration only.

## Step 3: enable it (opt-in!)

This plugin is **opt-in**: installing is not enabling. A user-sourced plugin absent
from `plugins.enabled` is silently skipped and its tools never enter the session —
with nothing logged as an error, which is exactly why "the tools never get called"
can look like a code defect.

```bash
cd /usr/local/src/hermes-agent && HERMES_HOME=<HOME> \
  ./.venv/bin/python -m hermes_cli.main plugins enable cloud_tools
```

This writes `plugins.enabled` into `config.yaml` and reports that it takes effect
on the **next session** — say so explicitly to the user. A hot reload leaves the
running gateway holding the old toolset.

Confirm it is no longer "not enabled":

```bash
cd /usr/local/src/hermes-agent && HERMES_HOME=<HOME> \
  ./.venv/bin/python -m hermes_cli.main plugins list | grep -A1 cloud_tools
```

## Step 4: prove the tool works end to end

Never verify by calling the handler directly — the dispatcher calls plugin handlers
as `handler(args_dict, **context)`, so a direct call passes even when the tool is
broken in a real session. Use the registry:

```bash
cd /usr/local/src/hermes-agent && HERMES_HOME=<HOME> ./.venv/bin/python -c "
import importlib.util, sys; sys.path.insert(0,'.')
spec = importlib.util.spec_from_file_location('ct', '<HOME>/plugins/cloud_tools/tools.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from tools.registry import registry
for n,s,h,e in m.ALL_TOOLS:
    registry.register(name=n, toolset='cloud_tools', schema=s, handler=h, override=True)
print(registry.dispatch('kv_get', {'key': 'github/token_meta'})[:160])
print(registry.dispatch('sms_quota', {})[:160])
"
```

Then one real model turn is the final check — the only test that exercises the whole
chain (tool discovery -> tool_search -> dispatch -> handler):

```bash
cd /usr/local/src/hermes-agent && HERMES_HOME=<HOME> \
  ./.venv/bin/python -m hermes_cli.main chat -q "读一下 kvbox 的 github/token_meta,只回答 owner。"
```

A good result: the value comes back in seconds, **no `terminal` call**, and no
`tool_call takes exactly one entry` or `quote_from_bytes` errors.

## Step 5: report honestly

State plainly:
- how many copies you found and which ones you deleted
- that `plugins doctor` reports 15 and that this does **not** prove schema shape
- that a config write needs a **new session** to take effect
- if you also synced an `installs/.../workspace` snapshot, that you did so because
  single-query runs load it — and that it can drift again, so re-check Step 0a
  after any future `hermes update` or reinstall

If any copy was skipped because you were unsure whether deleting it was safe, say
that too rather than reporting a clean install.

## Also install the usage skill

The plugin fixes what the tools do; a usage skill is what makes a *new* session
reach for them instead of `terminal` + `curl`. If the agent sees the user ask for
"the openrouter key" / "set a reminder" / "what did I log" and reaches for a shell,
that is a missing skill, not a broken tool.