---
title: Personal Memory Skill Design
date: 2026-03-20
status: draft
---

# Personal Memory Skill Design

## Goal

Build a personal memory skill that can automatically read, write, maintain, and retrieve long-term knowledge from PostgreSQL, with a local service as the primary entry and Python scripts as a direct database fallback.

The system must support multi-user isolation through an environment variable. The initial user is `LYB`.

## Product Decisions

### 1. Service is an acceleration layer, not the only runtime path

The skill should:

1. Detect whether the local memory service is healthy.
2. Start it automatically on first use when it is not running.
3. Fall back to Python scripts that talk to PostgreSQL directly when the service cannot start.

This avoids hard dependency on a long-lived background process.

### 2. Long-term memory promotion must be explicit when correctness matters

Automatic memory extraction is useful, but risky.

Use a two-path model:

- implicit memory capture:
  capture candidate memories automatically from user dialogue and tool results
- explicit memory promotion:
  when the user says things like "记住 xxx" or "不要忘了 xxx", store as stronger long-term memory with higher confidence

This reduces false positives from model-side inference.

### 3. Storage tiers

Do not model the short-term layer as "token storage".

Use three tiers instead:

- structured memory in PostgreSQL:
  durable facts, preferences, rules, project notes, relationship notes
- semantic retrieval index:
  embeddings for recall across free-form notes and summaries
- working memory:
  recent session summaries and active task context, periodically compressed

### 4. Retrieval strategy

Version 1 should not depend on a full RAG stack.

Use:

1. structured filters
2. PostgreSQL full-text search
3. optional embedding search

Rerank can be added in version 2 after enough data volume exists.

### 5. Memory governance

Do not physically forget by default.

Instead use:

- active
- archived
- superseded
- deleted
- conflict flags

This keeps history auditable and allows correction.

## Skill Trigger Expectations

The skill should trigger for:

- "记住 xxx"
- "不要忘了 xxx"
- "我喜欢/我不喜欢 xxx"
- "帮我查一下我之前提过的 xxx"
- "我对象的 xxx"
- "更新一下我的偏好/状态/规则"

The skill should also support silent background use when another task needs personal context.

## Runtime Architecture

### Canonical location

Implementation root:

`/Users/lyb/Desktop/LinRun/code/my_skillproject`

### Proposed layout

```text
my_skillproject/
├── docs/
│   └── personal-memory-skill-design.md
├── service/
│   ├── app.py
│   ├── db.py
│   ├── models.py
│   ├── schemas.py
│   ├── memory_ops.py
│   └── startup.py
├── scripts/
│   ├── ensure_service.py
│   ├── memory_query.py
│   ├── memory_upsert.py
│   ├── memory_delete.py
│   └── embed_backfill.py
├── sql/
│   ├── 001_schema.sql
│   └── 002_indexes.sql
└── skill/
    ├── SKILL.md
    ├── agents/
    │   └── openai.yaml
    └── references/
        └── memory-usage.md
```

### Primary flow

1. skill triggers
2. `scripts/ensure_service.py` checks `/health`
3. if unavailable, it starts the service
4. if still unavailable, the skill falls back to direct Python scripts
5. query or mutation executes against PostgreSQL
6. response is formatted for the model

## API Design

Use a lightweight FastAPI service.

### Core endpoints

- `GET /health`
- `POST /memory/search`
- `POST /memory/upsert`
- `POST /memory/delete`
- `POST /memory/archive`
- `POST /memory/conflict`
- `GET /memory/{id}`

### Automatic promotion endpoint

- `POST /memory/promote`

Input:

- raw text
- source
- user code
- explicit flag

Behavior:

- if explicit flag is true, store as long-term memory candidate with higher confidence
- otherwise store as lower-confidence candidate or short summary depending on heuristics

## Data Model

### Important schema choice

Although the product requirement says "add a user field", using a column literally named `user` in PostgreSQL is a poor choice.

Use `user_code` instead.

It represents the logical memory owner, not the database account.

The environment variable should be:

- `LYB_SKILL_MEMORY_USER`

Initial value:

- `LYB`

### Table 1: memory_item

Stores durable memory records.

Suggested columns:

- `id` bigint primary key
- `user_code` varchar(64) not null
- `memory_type` varchar(32) not null
- `title` varchar(255) not null
- `content` text not null
- `summary` text null
- `tags` jsonb not null default '[]'
- `source_type` varchar(32) not null
- `source_ref` varchar(255) null
- `confidence` numeric(4,3) not null default 0.700
- `importance` int not null default 5
- `status` varchar(32) not null default 'active'
- `is_explicit` boolean not null default false
- `supersedes_id` bigint null
- `conflict_with_id` bigint null
- `valid_from` timestamptz null
- `valid_to` timestamptz null
- `created_at` timestamptz not null default now()
- `updated_at` timestamptz not null default now()
- `deleted_at` timestamptz null

### Table 2: memory_embedding

Stores vector search rows.

- `id` bigint primary key
- `memory_id` bigint not null references `memory_item(id)`
- `user_code` varchar(64) not null
- `chunk_index` int not null default 0
- `chunk_text` text not null
- `embedding` vector(...)
- `created_at` timestamptz not null default now()

### Table 3: working_memory

Stores recent compressed session context.

- `id` bigint primary key
- `user_code` varchar(64) not null
- `session_key` varchar(128) not null
- `summary` text not null
- `importance` int not null default 3
- `expires_at` timestamptz null
- `created_at` timestamptz not null default now()
- `updated_at` timestamptz not null default now()

## Memory Types

Suggested `memory_type` values:

- `fact`
- `preference`
- `rule`
- `profile`
- `relationship`
- `project`
- `experience`
- `context`

## Automatic Update Rules

### Safe auto-write

Auto-write is allowed for:

- explicit commands from the user
- clear first-person stable facts
- task context that is clearly scoped and non-sensitive

### Auto-write with lower confidence

Allowed for:

- inferred preference
- inferred intent
- soft observations from repeated behavior

These should be marked as:

- lower confidence
- non-explicit
- reviewable

### Never auto-promote directly

Avoid direct long-term promotion for:

- emotional interpretation
- relationship assumptions
- health judgments
- inferred personal identity
- contradictory statements

These should require explicit confirmation.

## Query Pipeline

### Version 1

1. filter by `user_code`
2. filter by `memory_type` or tags when available
3. apply PostgreSQL full-text search
4. sort by explicitness, confidence, importance, freshness

### Version 2

1. candidate generation with full-text + vector
2. merge and deduplicate
3. rerank
4. return top-k with explanations

## Deletion Strategy

Use logical deletion.

`POST /memory/delete` should:

- set `status='deleted'`
- set `deleted_at=now()`
- keep the row for audit

## Recommended Environment Variables

- `LYB_SKILL_PG_ADDRESS`
- `LYB_SKILL_PG_PORT`
- `LYB_SKILL_PG_USERNAME`
- `LYB_SKILL_PG_PASSWORD`
- `LYB_SKILL_PG_MY_PERSONAL_DATABASE`
- `LYB_SKILL_MEMORY_USER`

Optional future variables:

- `LYB_SKILL_MEMORY_SERVICE_PORT`
- `LYB_SKILL_MEMORY_EMBED_MODEL`
- `LYB_SKILL_MEMORY_RERANK_MODEL`
- `LYB_SKILL_MEMORY_TOP_K`

## Suggested MVP Scope

Build in this order:

1. PostgreSQL schema
2. Python direct scripts
3. FastAPI service with health check and CRUD/search
4. service auto-start helper
5. skill wrapper
6. embedding-based recall
7. rerank and conflict workflows

## Open Questions

Before implementation, confirm:

1. whether `pgvector` is available on your PostgreSQL instance
2. what embedding model should be used
3. whether the service should bind only to localhost
4. whether `LYB_SKILL_MEMORY_USER` should be the only tenant key or whether future couple usage needs household-level grouping too
