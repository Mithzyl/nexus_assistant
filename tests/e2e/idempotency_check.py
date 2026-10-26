"""Distinguish replayed submissions from concurrent different submissions.

Two separate properties, reported separately:

1. REPLAY: the same operation (same Idempotency-Key) sent twice returns the
   same check id and does not create a second check or model run.
2. CONCURRENCY: N requests with different keys create N checks. The backend's
   "8 requests -> 8 checks" evidence is this property, not replay safety.

Runs against an isolated API on the given base URL with a synthetic account.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request


def call(method, url, *, token=None, payload=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return response.status, dict(response.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as caught:
        raw = caught.read()
        try:
            parsed = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            parsed = {"raw": raw.decode(errors="replace")[:200]}
        return caught.code, dict(caught.headers), parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--plan-name", default="幂等验收通勤")
    args = parser.parse_args()

    status, _, login = call("POST", f"{args.api}/api/v1/auth/login",
                            payload={"email": args.email, "password": args.password})
    assert status == 200, (status, login)
    token = login["token"]

    status, _, plan = call(
        "POST", f"{args.api}/api/v1/commute-plans", token=token,
        payload={
            "name": args.plan_name, "enabled": True, "timezone": "Asia/Shanghai",
            "weekdays": [1, 2, 3, 4, 5, 6, 7], "arrival_local_time": "23:50",
            "check_local_time": "07:30", "transport_mode": "driving",
            "buffer_minutes": 10, "notify_enabled": False,
            "origin": {"label": "起点", "latitude": 31.2304, "longitude": 121.4737},
            "destination": {"label": "终点", "latitude": 31.2019, "longitude": 121.606},
        },
    )
    assert status == 201, (status, plan)
    plan_id = plan["id"]

    key = "idem-replay-key-0001"
    first_status, first_headers, first = call(
        "POST", f"{args.api}/api/v1/commute-plans/{plan_id}/checks",
        token=token, headers={"Idempotency-Key": key},
    )
    second_status, second_headers, second = call(
        "POST", f"{args.api}/api/v1/commute-plans/{plan_id}/checks",
        token=token, headers={"Idempotency-Key": key},
    )
    def header(headers: dict, name: str):
        return next((value for key, value in headers.items() if key.lower() == name.lower()), None)

    replay = {
        "first_status": first_status,
        "second_status": second_status,
        "same_check_id": first.get("check_id") == second.get("check_id"),
        "second_replay_header": header(second_headers, "X-Idempotent-Replay"),
        "first_replay_header": header(first_headers, "X-Idempotent-Replay"),
        "check_id": first.get("check_id"),
    }

    different = []
    for index in range(3):
        status, _, created = call(
            "POST", f"{args.api}/api/v1/commute-plans/{plan_id}/checks",
            token=token, headers={"Idempotency-Key": f"idem-distinct-key-{index:04d}"},
        )
        different.append({"status": status, "check_id": created.get("check_id")})

    connection = sqlite3.connect(args.db)
    total = connection.execute("select count(*) from commute_checks").fetchone()[0]
    model_runs = connection.execute(
        "select count(*) from nexus_runs where purpose='commute_internal'"
    ).fetchone()[0]
    connection.close()

    result = {
        "replay": replay,
        "distinct": different,
        "checks_in_db": total,
        "commute_model_runs": model_runs,
        "replay_safe": replay["same_check_id"]
        and replay["second_replay_header"] == "true"
        and total == 4,
        "distinct_created": len({item["check_id"] for item in different}) == 3,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["replay_safe"] and result["distinct_created"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
