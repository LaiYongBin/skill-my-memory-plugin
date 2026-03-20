#!/usr/bin/env python3
"""List structured memory-analysis results."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError

from common import request_json, start_service
from service.analyzer import list_analysis_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-code")
    parser.add_argument("--session-key")
    parser.add_argument("--limit", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = {
        "user_code": args.user_code,
        "session_key": args.session_key,
        "limit": args.limit,
    }
    if start_service():
        try:
            response = request_json("POST", "/memory/analysis/list", payload)
            print(json.dumps(response, ensure_ascii=False, default=str))
            return 0
        except HTTPError:
            pass
    rows = list_analysis_results(user_code=args.user_code, session_key=args.session_key, limit=args.limit)
    print(json.dumps({"ok": True, "data": {"items": rows, "count": len(rows)}}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
