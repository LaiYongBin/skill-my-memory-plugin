"""Core PostgreSQL memory operations."""

from typing import Any, Dict, List, Optional

from psycopg.types.json import Json

from service.db import get_conn, get_settings


def _resolve_user(user_code: Optional[str]) -> str:
    return user_code or str(get_settings()["memory_user"])


def get_memory(memory_id: int, user_code: Optional[str] = None) -> Optional[Dict[str, Any]]:
    resolved_user = _resolve_user(user_code)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_code, memory_type, title, content, summary, tags,
                   source_type, source_ref, confidence, importance, status,
                   is_explicit, supersedes_id, conflict_with_id,
                   valid_from, valid_to, created_at, updated_at, deleted_at
            FROM memory_item
            WHERE id = %s AND user_code = %s
            """,
            (memory_id, resolved_user),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def search_memories(
    *,
    query: str = "",
    user_code: Optional[str] = None,
    memory_type: Optional[str] = None,
    tags: Optional[List[str]] = None,
    include_archived: bool = False,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    resolved_user = _resolve_user(user_code)
    tags = tags or []
    conditions = ["user_code = %s", "deleted_at IS NULL"]
    where_params: List[Any] = [resolved_user]
    select_params: List[Any] = []
    if not include_archived:
        conditions.append("status = 'active'")
    if memory_type:
        conditions.append("memory_type = %s")
        where_params.append(memory_type)
    if tags:
        conditions.append("tags ?| %s")
        where_params.append(tags)

    rank_sql = "0::float AS rank_score"
    if query.strip():
        conditions.append(
            """
            (
                search_vector @@ websearch_to_tsquery('simple', %s)
                OR title ILIKE %s
                OR content ILIKE %s
                OR coalesce(summary, '') ILIKE %s
            )
            """
        )
        where_params.append(query.strip())
        like_query = "%" + query.strip() + "%"
        where_params.extend([like_query, like_query, like_query])
        rank_sql = (
            """
            CASE
                WHEN search_vector @@ websearch_to_tsquery('simple', %s)
                THEN ts_rank_cd(search_vector, websearch_to_tsquery('simple', %s))
                ELSE 0.0
            END AS rank_score
            """
        )
        select_params.extend([query.strip(), query.strip()])

    where_sql = " AND ".join(conditions)
    sql = f"""
        SELECT id, user_code, memory_type, title, content, summary, tags,
               source_type, source_ref, confidence, importance, status,
               is_explicit, created_at, updated_at,
               {rank_sql}
        FROM memory_item
        WHERE {where_sql}
        ORDER BY is_explicit DESC, rank_score DESC, importance DESC, confidence DESC, updated_at DESC
        LIMIT %s
    """
    params = select_params + where_params + [limit]

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [dict(row) for row in rows]


def upsert_memory(payload: Dict[str, Any]) -> Dict[str, Any]:
    resolved_user = _resolve_user(payload.get("user_code"))
    tags = payload.get("tags") or []
    values = {
        "user_code": resolved_user,
        "memory_type": payload["memory_type"],
        "title": payload["title"],
        "content": payload["content"],
        "summary": payload.get("summary"),
        "tags": Json(tags),
        "source_type": payload.get("source_type", "manual"),
        "source_ref": payload.get("source_ref"),
        "confidence": payload.get("confidence", 0.7),
        "importance": payload.get("importance", 5),
        "status": payload.get("status", "active"),
        "is_explicit": payload.get("is_explicit", False),
        "valid_from": payload.get("valid_from"),
        "valid_to": payload.get("valid_to"),
    }
    with get_conn() as conn, conn.cursor() as cur:
        if payload.get("id"):
            cur.execute(
                """
                UPDATE memory_item
                SET memory_type = %(memory_type)s,
                    title = %(title)s,
                    content = %(content)s,
                    summary = %(summary)s,
                    tags = %(tags)s,
                    source_type = %(source_type)s,
                    source_ref = %(source_ref)s,
                    confidence = %(confidence)s,
                    importance = %(importance)s,
                    status = %(status)s,
                    is_explicit = %(is_explicit)s,
                    valid_from = %(valid_from)s,
                    valid_to = %(valid_to)s,
                    updated_at = now()
                WHERE id = %(id)s AND user_code = %(user_code)s AND deleted_at IS NULL
                RETURNING id
                """,
                values | {"id": payload["id"]},
            )
        else:
            cur.execute(
                """
                INSERT INTO memory_item (
                    user_code, memory_type, title, content, summary, tags,
                    source_type, source_ref, confidence, importance, status,
                    is_explicit, valid_from, valid_to
                ) VALUES (
                    %(user_code)s, %(memory_type)s, %(title)s, %(content)s, %(summary)s, %(tags)s,
                    %(source_type)s, %(source_ref)s, %(confidence)s, %(importance)s, %(status)s,
                    %(is_explicit)s, %(valid_from)s, %(valid_to)s
                )
                RETURNING id
                """,
                values,
            )
        row = cur.fetchone()
        conn.commit()
    return get_memory(int(row["id"]), resolved_user) or {}


def promote_memory(payload: Dict[str, Any]) -> Dict[str, Any]:
    explicit = bool(payload.get("explicit"))
    title = payload.get("title") or payload["text"][:80]
    confidence = 0.95 if explicit else 0.6
    importance = 8 if explicit else 5
    return upsert_memory(
        {
            "user_code": payload.get("user_code"),
            "memory_type": payload.get("memory_type", "fact"),
            "title": title,
            "content": payload["text"],
            "summary": payload["text"][:240],
            "tags": payload.get("tags") or [],
            "source_type": payload.get("source_type", "conversation"),
            "source_ref": payload.get("source_ref"),
            "confidence": confidence,
            "importance": importance,
            "status": "active",
            "is_explicit": explicit,
        }
    )


def archive_memory(memory_id: int, user_code: Optional[str] = None) -> Optional[Dict[str, Any]]:
    resolved_user = _resolve_user(user_code)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE memory_item
            SET status = 'archived', updated_at = now()
            WHERE id = %s AND user_code = %s AND deleted_at IS NULL
            RETURNING id
            """,
            (memory_id, resolved_user),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return get_memory(int(row["id"]), resolved_user)


def delete_memory(memory_id: int, user_code: Optional[str] = None) -> Optional[Dict[str, Any]]:
    resolved_user = _resolve_user(user_code)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE memory_item
            SET status = 'deleted', deleted_at = now(), updated_at = now()
            WHERE id = %s AND user_code = %s AND deleted_at IS NULL
            RETURNING id
            """,
            (memory_id, resolved_user),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return get_memory(int(row["id"]), resolved_user)
