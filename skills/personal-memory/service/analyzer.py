"""Structured memory analysis for conversation turns."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from psycopg.types.json import Json

from service.db import get_conn, get_settings


CODE_KEYWORDS = [
    "代码",
    "编程",
    "程序",
    "接口",
    "数据库",
    "sql",
    "python",
    "java",
    "javascript",
    "typescript",
    "bug",
    "debug",
    "服务",
    "部署",
    "前端",
    "后端",
]

COOKING_KEYWORDS = [
    "做饭",
    "做菜",
    "菜谱",
    "下厨",
    "炖",
    "炒",
    "煮",
    "烤",
]

SHORT_TERM_HINTS = [
    "这周",
    "本周",
    "今天",
    "明天",
    "最近",
    "当前",
    "目前",
    "先",
    "暂时",
    "这次",
]

SENSITIVE_HINTS = [
    "抑郁",
    "焦虑",
    "生病",
    "怀孕",
    "爱不爱",
    "对象是不是",
]


def _resolve_user(user_code: Optional[str]) -> str:
    return user_code or str(get_settings()["memory_user"])


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _contains_any(text: str, keywords: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _count_recent_keyword_turns(user_code: str, keywords: Sequence[str], limit: int = 30) -> int:
    patterns = ["%" + keyword + "%" for keyword in keywords]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS hit_count
            FROM (
                SELECT content
                FROM conversation_event
                WHERE user_code = %s
                  AND role = 'user'
                ORDER BY created_at DESC
                LIMIT %s
            ) recent
            WHERE content ILIKE ANY(%s)
            """,
            (user_code, limit, patterns),
        )
        row = cur.fetchone()
    return int(row["hit_count"] or 0) if row else 0


def build_analysis_item(
    *,
    category: str,
    subject: str,
    claim: str,
    rationale: str,
    evidence_type: str,
    time_scope: str,
    action: str,
    confidence: float,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "category": category,
        "subject": subject,
        "claim": claim,
        "rationale": rationale,
        "evidence_type": evidence_type,
        "time_scope": time_scope,
        "action": action,
        "confidence": confidence,
        "tags": tags or [],
    }


def analyze_turn(
    *,
    user_text: str,
    assistant_text: str = "",
    user_code: Optional[str] = None,
    session_key: str = "default",
) -> List[Dict[str, Any]]:
    resolved_user = _resolve_user(user_code)
    cleaned = _clean(user_text)
    if not cleaned:
        return []

    items: List[Dict[str, Any]] = []

    if _contains_any(cleaned, SENSITIVE_HINTS):
        items.append(
            build_analysis_item(
                category="sensitive_state",
                subject="user_state",
                claim=cleaned,
                rationale="这条消息包含敏感或模糊的个人状态线索。",
                evidence_type="explicit",
                time_scope="short_term",
                action="review",
                confidence=0.45,
                tags=["sensitive"],
            )
        )
        return items

    self_match = re.search(r"我是(?:一个|个)?(?P<content>.+)", cleaned)
    if self_match:
        content = self_match.group("content").strip("。！？!?,， ")
        items.append(
            build_analysis_item(
                category="self_description",
                subject="user_profile",
                claim=content,
                rationale="用户明确描述了自己的特征或身份倾向。",
                evidence_type="explicit",
                time_scope="long_term",
                action="long_term",
                confidence=0.86,
                tags=["self-description"],
            )
        )

    if _contains_any(cleaned, CODE_KEYWORDS):
        hit_count = _count_recent_keyword_turns(resolved_user, CODE_KEYWORDS)
        items.append(
            build_analysis_item(
                category="domain_interest",
                subject="technical_topics",
                claim="用户高频关注软件开发和工程技术话题。",
                rationale="这一轮包含明显技术关键词，并且与历史对话模式一致。",
                evidence_type="observed",
                time_scope="long_term" if hit_count >= 3 else "mid_term",
                action="long_term" if hit_count >= 3 else "working_memory",
                confidence=0.74 if hit_count >= 3 else 0.58,
                tags=["coding", "engineering"],
            )
        )
        if hit_count >= 5:
            items.append(
                build_analysis_item(
                    category="role_hypothesis",
                    subject="possible_role",
                    claim="用户可能是程序员或技术从业者。",
                    rationale="多轮重复的技术讨论表明其可能具有稳定的技术角色。",
                    evidence_type="inferred",
                    time_scope="long_term",
                    action="review",
                    confidence=0.62,
                    tags=["role-hypothesis", "coding"],
                )
            )

    if _contains_any(cleaned, COOKING_KEYWORDS):
        action = "working_memory" if _contains_any(cleaned, SHORT_TERM_HINTS) else "long_term"
        time_scope = "short_term" if action == "working_memory" else "mid_term"
        items.append(
            build_analysis_item(
                category="cooking_interest",
                subject="food_or_cooking",
                claim="用户当前对做饭或烹饪存在需求或兴趣。",
                rationale="这一轮出现了做饭相关内容，但单次提及不足以推断职业。",
                evidence_type="observed",
                time_scope=time_scope,
                action=action,
                confidence=0.6 if action == "working_memory" else 0.66,
                tags=["cooking"],
            )
        )

    if _contains_any(cleaned, SHORT_TERM_HINTS):
        items.append(
            build_analysis_item(
                category="current_goal",
                subject="active_context",
                claim=cleaned,
                rationale="这一轮描述了具有明显时间范围的当前目标或近期关注点。",
                evidence_type="explicit",
                time_scope="short_term",
                action="working_memory",
                confidence=0.8,
                tags=["short-term"],
            )
        )

    if not items and len(cleaned) <= 12:
        items.append(
            build_analysis_item(
                category="lightweight_context",
                subject="ephemeral_context",
                claim=cleaned,
                rationale="这一轮过短或过于模糊，不适合作为长期记忆保存。",
                evidence_type="observed",
                time_scope="ephemeral",
                action="ignore",
                confidence=0.4,
                tags=["ephemeral"],
            )
        )

    return items


def save_analysis_results(
    *,
    user_code: str,
    session_key: str,
    source_event_id: Optional[int],
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    saved: List[Dict[str, Any]] = []
    if not items:
        return saved
    with get_conn() as conn, conn.cursor() as cur:
        for item in items:
            cur.execute(
                """
                INSERT INTO memory_analysis_result (
                    user_code, session_key, source_event_id, category, subject, claim,
                    rationale, evidence_type, time_scope, action, confidence, status, tags
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, 'active', %s
                )
                RETURNING id, user_code, session_key, source_event_id, category, subject, claim,
                          rationale, evidence_type, time_scope, action, confidence, status, tags,
                          created_at, updated_at
                """,
                (
                    user_code,
                    session_key,
                    source_event_id,
                    item["category"],
                    item["subject"],
                    item["claim"],
                    item["rationale"],
                    item["evidence_type"],
                    item["time_scope"],
                    item["action"],
                    item["confidence"],
                    Json(item.get("tags") or []),
                ),
            )
            row = cur.fetchone()
            saved.append(dict(row))
        conn.commit()
    return saved


def list_analysis_results(
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
            SELECT id, user_code, session_key, source_event_id, category, subject, claim,
                   rationale, evidence_type, time_scope, action, confidence, status, tags,
                   created_at, updated_at
            FROM memory_analysis_result
            WHERE {where_sql}
            ORDER BY updated_at DESC, id DESC
            LIMIT %s
            """,
            params + [limit],
        )
        return [dict(row) for row in cur.fetchall()]


def mark_event_analyzed(event_ids: Sequence[int]) -> None:
    ids = [int(event_id) for event_id in event_ids if event_id]
    if not ids:
        return
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE conversation_event
            SET analyzed_status = 'done',
                analyzed_at = now()
            WHERE id = ANY(%s)
            """,
            (ids,),
        )
        conn.commit()
