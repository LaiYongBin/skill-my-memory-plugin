#!/usr/bin/env python3
"""Extract and optionally persist candidate memories from user text."""

from __future__ import annotations

import argparse
import json

from common import request_json, start_service
from service.extraction import extract_candidates
from service.memory_ops import upsert_memory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--user-code")
    parser.add_argument("--auto-persist", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = {
        "text": args.text,
        "user_code": args.user_code,
        "auto_persist": args.auto_persist,
    }
    if start_service():
        response = request_json("POST", "/memory/capture", payload)
        print(json.dumps(response, ensure_ascii=False, default=str))
        return 0

    candidates = extract_candidates(args.text)
    if args.auto_persist:
        persisted = []
        for candidate in candidates:
            candidate["user_code"] = args.user_code
            persisted.append(upsert_memory(candidate))
        print(json.dumps({"ok": True, "data": {"candidates": persisted, "count": len(persisted)}}, ensure_ascii=False, default=str))
        return 0

    print(json.dumps({"ok": True, "data": {"candidates": candidates, "count": len(candidates)}}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
