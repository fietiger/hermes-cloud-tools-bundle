# Hermes Cloud Tools Bundle (合集工具插件)

Hermes Agent 原生通用扩展插件包，集成了 Cloudflare KV 存储、日记系统、乌龟卡短信提醒以及工时日报系统四大核心服务。

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

### 4. 工时日报系统 (WHS)
- `whs_add_report(username, project, content, start_time, end_time, date)`: 提交工时日报
- `whs_list_reports(username, limit)`: 查询员工工时日报
- `whs_add_plan(username, project, start_date, end_date)`: 录入项目甘特图计划

---

## 安装与使用

将本仓库克隆至 Hermes 插件目录即可自动生效：
```bash
git clone https://github.com/fietiger/hermes-cloud-tools-bundle.git ~/.hermes/plugins/cloud-tools-bundle
```
无需修改任何底层核心代码，Hermes Gateway 启动时将自动识别并注册全部工具。
