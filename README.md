# Hermes Cloud Tools Bundle (合集工具插件)

Hermes Agent 原生通用扩展插件包，集成了 Cloudflare KV 存储、日记系统以及乌龟卡短信提醒三大核心服务。

---

## 包含的 Agent 工具列表

### 1. Cloudflare KV 键值存储 (kvbox)
- `kv_get(key)`: 从 KV 存储中读取数据
- `kv_put(key, value, ttl)`: 写入键值数据，支持指定 TTL（秒）过期时间
- `kv_list(prefix, limit)`: 列出指定前缀的全部键名
- `kv_delete(key)`: 删除指定的键

### 2. 日记系统 (diary.benext.uk)
- `diary_write(content, title, mood, date)`: 记录新日记，支持设定心情和日期
- `diary_list(limit)`: 获取近期日记列表
- `diary_search(keyword, from_date, to_date)`: 关键词或日期区间检索
- `diary_delete(diary_id)`: 删除指定的日记条目

### 3. 乌龟卡短信提醒系统 (sms.benext.uk)
- `sms_send(message, phone)`: 即时发送短信
- `sms_create_reminder(title, message, run_at)`: 创建定时短信提醒（支持单次/周期）
- `sms_quota()`: 查询当月短信额度与使用情况

---

## 安装与使用

> **安装前请先阅读 [`skills/install-cloud-tools-bundle`](skills/install-cloud-tools-bundle/SKILL.md)。**
> 本插件自带 `plugin.yaml`，**任何含有该文件的目录都会被当作插件发现**。
> 旧版本残留的副本（包括 `plugins/tools/cloud_tools`、备份目录、
> `installs/*/workspace` 快照）会各自注册一套同名工具，且互相漂移——
> 实际加载哪一份无法从源码判断。该 skill 给出完整的清理、安装、启用、验证流程。

### 手动安装

`plugin.yaml` 位于**仓库根目录**，因此需要把插件负载复制到扁平路径下，
不能直接把整个仓库克隆进 `plugins/`：

```bash
SRC=/path/to/hermes-cloud-tools-bundle
DEST="$HERMES_HOME/plugins/cloud_tools"      # $HERMES_HOME 默认 ~/.hermes
rm -rf "$DEST" && mkdir -p "$DEST"
cp "$SRC/plugins/cloud_tools/tools.py" "$SRC/plugins/cloud_tools/__init__.py" "$DEST/"
cp "$SRC/plugin.yaml" "$DEST/plugin.yaml"
find "$HERMES_HOME/plugins" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
```

### 启用（opt-in，插件默认不启用）

用户侧插件必须显式启用才会被加载，未启用时工具不会进入会话、且**不会报任何错**：

```bash
hermes plugins enable cloud_tools
```

该操作写入 `config.yaml` 的 `plugins.enabled`，**下一个新会话**才生效。

### 验证

```bash
hermes plugins doctor cloud_tools     # 期望: registrations: 15 tool(s), 0 hook(s)
```

> `plugins doctor` 只校验 manifest 解析、import 与注册，**不校验 schema 结构**，
> 因此它通过并不等于模型能正确调用该工具。

### 凭据

本插件**不携带任何凭据**。读取顺序为环境变量 `KVBOX_TOKEN` →
`<HOME>/.kvbox_token`，两者都没有时报错并指明上述两种方式。

---

## 配套 Skills

| Skill | 用途 |
|---|---|
| [`skills/install-cloud-tools-bundle`](skills/install-cloud-tools-bundle/SKILL.md) | 安装/升级前先清理旧副本，含验证与排查步骤 |
| [`skills/kvbox-and-personal-services`](skills/kvbox-and-personal-services/SKILL.md) | 使用侧：kvbox 真实 key 布局、提醒中枢约定、工具报错时的正确反应 |
