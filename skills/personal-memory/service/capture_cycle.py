"""Conversation-end memory capture and consolidation."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from service.db import get_conn, get_settings
from service.extraction import extract_candidates, extract_review_candidates, should_auto_persist
from service.memory_ops import save_review_candidate, upsert_memory


TEMPORAL_HINTS = [
    "今天",
    "这周",
    "本周",
    "最近",
    "目前",
    "当前",
    "暂时",
    "先",
    "现在",
    "这两天",
    "今晚",
    "明天",
    "下周",
    "本月",
]

WORKING_HINTS = [
    "在做",
    "正在",
    "处理",
    "排查",
    "推进",
    "优先",
    "先做",
    "先看",
    "准备",
    "计划",
    "目标",
    "本次",
    "这次",
]


def _resolve_user(user_code: Optional[str]) -> str:
    return user_code or str(get_settings()["memory_user"])


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _memory_key(text: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", text.lower())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]


def _looks_short_term(text: str) -> bool:
    cleaned = _clean_text(text)
    return any(token in cleaned for token in TEMPORAL_HINTS + WORKING_HINTS)


def _working_importance(text: str) -> int:
    cleaned = _clean_text(text)
    if any(token in cleaned for token in ["优先", "目标", "排查", "正在", "推进"]):
        return 5
    return 4


def record_conversation_event(
    *,
    user_code: str,
    session_key: str,
    role: str,
    content: str,
    source_ref: Optional[str] = None,
    event_type: str = "turn",
) -> Optional[Dict[str, Any]]:
    cleaned = _clean_text(content)
    if not cleaned:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversation_event (
                user_code, session_key, event_type, role, content, source_ref
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, user_code, session_key, event_type, role, content, source_ref, created_at
            """,
            (user_code, session_key, event_type, role, cleaned, source_ref),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None


def upsert_working_memory(
    *,
    user_code: str,
    session_key: str,
    summary: str,
    source_text: str,
    importance: int,
    expires_in_days: int = 7,
) -> Dict[str, Any]:
    memory_key = _memory_key(summary)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM working_memory
            WHERE user_code = %s
              AND memory_key = %s
              AND status = 'active'
              AND (expires_at IS NULL OR expires_at > now())
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_code, memory_key),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """
                UPDATE working_memory
                SET session_key = %s,
                    summary = %s,
                    importance = GREATEST(importance, %s),
                    expires_at = now() + (%s || ' days')::interval,
                    source_text = %s,
                    updated_at = now(),
                    status = 'active'
                WHERE id = %s
                RETURNING id, user_code, session_key, memory_key, summary, importance,
                          expires_at, source_text, status, created_at, updated_at
                """,
                (session_key, summary, importance, expires_in_days, source_text, existing["id"]),
            )
        else:
            cur.execute(
                """
                INSERT INTO working_memory (
                    user_code, session_key, memory_key, summary, importance,
                    expires_at, source_text, status
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    now() + (%s || ' days')::interval, %s, 'active'
                )
                RETURNING id, user_code, session_key, memory_key, summary, importance,
                          expires_at, source_text, status, created_at, updated_at
                """,
                (user_code, session_key, memory_key, summary, importance, expires_in_days, source_text),
            )
        row = cur.fetchone()
        conn.commit()
        return dict(row)


def list_working_memories(
    *, user_code: Optional[str] = None, session_key: Optional[str] = None, limit: int = 20
) -> List[Dict[str, Any]]:
    resolved_user = _resolve_user(user_code)
    conditions = ["user_code = %s", "status = 'active'"]
    params: List[Any] = [resolved_user]
    if session_key:
        conditions.append("session_key = %s")
        params.append(session_key)
    where_sql = " AND ".join(conditions)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, user_code, session_key, memory_key, summary, importance,
                   expires_at, source_text, status, created_at, updated_at
            FROM working_memory
            WHERE {where_sql}
            ORDER BY importance DESC, updated_at DESC
            LIMIT %s
            """,
            params + [limit],
        )
        return [dict(row) for row in cur.fetchall()]


def build_working_memory_candidates(user_text: str, assistant_text: str = "") -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    cleaned_user = _clean_text(user_text)
    if cleaned_user and _looks_short_term(cleaned_user):
        candidates.append(
            {
                "summary": cleaned_user[:240],
                "source_text": cleaned_user,
                "importance": _working_importance(cleaned_user),
            }
        )
    cleaned_assistant = _clean_text(assistant_text)
    if cleaned_assistant and any(token in cleaned_assistant for token in ["下一步", "接下来", "先", "会继续"]):
        candidates.append(
            {
                "summary": cleaned_assistant[:240],
                "source_text": cleaned_assistant,
                "importance": 3,
            }
        )
    return candidates


def consolidate_working_memories(
    *, user_code: Optional[str] = None, session_key: Optional[str] = None
) -> Dict[str, Any]:
    resolved_user = _resolve_user(user_code)
    archived = 0
    promoted: List[Dict[str, Any]] = []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE working_memory
            SET status = 'archived', updated_at = now()
            WHERE user_code = %s
              AND status = 'active'
              AND expires_at IS NOT NULL
              AND expires_at <= now()
            RETURNING id
            """,
            (resolved_user,),
        )
        archived = len(cur.fetchall())

        conditions = ["user_code = %s", "status = 'active'"]
        params: List[Any] = [resolved_user]
        if session_key:
            conditions.append("session_key = %s")
            params.append(session_key)
        where_sql = " AND ".join(conditions)
        cur.execute(
            f"""
            SELECT memory_key,
                   max(summary) AS summary,
                   max(source_text) AS source_text,
                   max(importance) AS importance,
                   count(*) AS occurrence_count
            FROM working_memory
            WHERE {where_sql}
              AND memory_key IS NOT NULL
            GROUP BY memory_key
            HAVING count(*) >= 2
            ORDER BY count(*) DESC, max(updated_at) DESC
            """,
            params,
        )
        rows = [dict(row) for row in cur.fetchall()]
        conn.commit()

    for row in rows:
        summary = str(row["summary"] or "").strip()
        if not summary or _looks_short_term(summary):
            continue
        promoted.append(
            upsert_memory(
                {
                    "user_code": resolved_user,
                    "memory_type": "context",
                    "title": "沉淀上下文: " + summary[:60],
                    "content": summary,
                    "summary": summary[:240],
                    "tags": ["working-memory", "auto-consolidated"],
                    "source_type": "consolidation",
                    "confidence": 0.7,
                    "importance": int(row.get("importance") or 4),
                    "status": "active",
                    "is_explicit": False,
                }
            )
        )

    return {
        "archived_count": archived,
        "promoted": promoted,
        "promoted_count": len(promoted),
    }


def run_capture_cycle(
    *,
    user_text: str,
    assistant_text: str = "",
    user_code: Optional[str] = None,
    session_key: str = "default",
    source_ref: Optional[str] = None,
    consolidate: bool = True,
) -> Dict[str, Any]:
    resolved_user = _resolve_user(user_code)
    events = []
    user_event = record_conversation_event(
        user_code=resolved_user,
        session_key=session_key,
        role="user",
        content=user_text,
        source_ref=source_ref,
    )
    if user_event:
        events.append(user_event)
    if assistant_text.strip():
        assistant_event = record_conversation_event(
            user_code=resolved_user,
            session_key=session_key,
            role="assistant",
            content=assistant_text,
            source_ref=source_ref,
        )
        if assistant_event:
            events.append(assistant_event)

    persisted = []
    pending_candidates = []
    review_items = []
    for candidate in extract_candidates(user_text):
        payload = candidate.copy()
        payload["user_code"] = resolved_user
        if should_auto_persist(candidate):
            persisted.append(upsert_memory(payload))
        else:
            pending_candidates.append(candidate)

    for candidate in extract_review_candidates(user_text):
        review_items.append(
            save_review_candidate(user_code=resolved_user, source_text=user_text, candidate=candidate)
        )

    working_items = []
    for candidate in build_working_memory_candidates(user_text, assistant_text):
        working_items.append(
            upsert_working_memory(
                user_code=resolved_user,
                session_key=session_key,
                summary=candidate["summary"],
                source_text=candidate["source_text"],
                importance=int(candidate["importance"]),
            )
        )

    consolidation = None
    if consolidate:
        consolidation = consolidate_working_memories(user_code=resolved_user, session_key=session_key)

    return {
        "events": events,
        "event_count": len(events),
        "persisted": persisted,
        "persisted_count": len(persisted),
        "candidates": pending_candidates,
        "candidate_count": len(pending_candidates),
        "working_memory": working_items,
        "working_memory_count": len(working_items),
        "review_candidates": review_items,
        "review_candidate_count": len(review_items),
        "consolidation": consolidation,
    }
