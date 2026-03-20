---
name: personal-memory
description: 管理个人长期记忆与上下文记忆。当用户要求记住某件事、不要忘记某件事、查询过去提过的信息、更新个人偏好或维护多用户隔离的记忆数据时使用。
---

# Personal Memory

## Overview

Use this skill to manage durable personal memory in PostgreSQL.

The primary path is a local FastAPI service under `/Users/lyb/Desktop/LinRun/code/my_skillproject`. If the service is not running, start it automatically. If it still cannot run, fall back to the Python scripts that talk to PostgreSQL directly.

## Trigger Cases

- "记住 xxx"
- "不要忘了 xxx"
- "我之前提过的 xxx 是什么"
- "更新我的偏好/状态/规则"
- "查一下我对象的 xxx"

## Runtime Order

1. Run `scripts/ensure_service.py`.
2. If healthy, use the service endpoints.
3. If startup fails, use the direct scripts in `scripts/`.

## Automatic Capture

- For explicit phrases such as `记住` and `不要忘了`, persist directly as stronger long-term memory.
- For softer statements such as `我喜欢...`, `我不喜欢...`, `以后请...`, first extract candidate memories.
- Use `scripts/memory_capture.py` to extract or auto-persist candidates.

## Safety Rules

- Explicit phrases such as "记住" and "不要忘了" should promote content into stronger long-term memory.
- Automatic memory capture is allowed for clear low-risk facts and task context, but inferred personal facts should use lower confidence.
- Do not physically delete by default. Use archive or logical delete.
- Always scope reads and writes by `LYB_SKILL_MEMORY_USER`.

## Core Commands

```bash
python3 scripts/ensure_service.py
python3 scripts/memory_query.py --query "最近喜欢什么"
python3 scripts/memory_upsert.py --promote --explicit --memory-type preference --content "我喜欢黑咖啡"
python3 scripts/memory_capture.py --text "我喜欢黑咖啡"
python3 scripts/memory_capture.py --text "记住我对象喜欢花" --auto-persist
python3 scripts/memory_delete.py --id 123 --archive
```

## Environment Variables

- `LYB_SKILL_PG_ADDRESS`
- `LYB_SKILL_PG_PORT`
- `LYB_SKILL_PG_USERNAME`
- `LYB_SKILL_PG_PASSWORD`
- `LYB_SKILL_PG_MY_PERSONAL_DATABASE`
- `LYB_SKILL_MEMORY_USER`

## References

Read `references/memory-usage.md` for the data model and trigger policy.
