# Mobile web end-to-end checks

The canonical runner is `run_web_e2e.py`. It creates a **fresh SQLite database**,
seeds synthetic accounts/plans/chats/weather, starts the real `assistant-api`
with a fixed clock, starts the production Next.js build, and runs
`web_flow_check.py` with Playwright. It never starts or writes the shared
backend and never calls a real model or sends a notification.

```bash
# 1. Browser test dependency (the Python that runs run_web_e2e.py)
python3 -m pip install playwright
python3 -m playwright install chromium

# 2. Isolated backend dependency (used only for the child API process)
python3 -m venv .tmp/backend-venv
.tmp/backend-venv/bin/pip install -e services/assistant-api

# 3. Build + seed + run (both browser suites)
ASSISTANT_PYTHON=.tmp/backend-venv/bin/python \
  pnpm --filter @superapp/web test:e2e
```

`ASSISTANT_PYTHON` is required. The runner builds `apps/web` unless
`--skip-build` is passed; `--web-port` (default `3300`) and `--api-port`
(default `8300`) are checked for conflicts before anything starts.

## What it proves

The flow stores its own fixture in `.tmp/e2e/fixture.json` and writes logs plus
screenshots to `.tmp/e2e/`. The fixed clock is tomorrow 08:00 Asia/Shanghai, and
all advisory/weather timestamps are relative to it, so the result does not
depend on an old advisory happening to still be valid.

- login failure/success, HttpOnly/Secure/SameSite cookies, renewal, logout,
  account switch, and a rejected cross-site write that keeps the session.
- chat history from the server, old local-cache verification via
  `POST /api/v1/asks/verify`, internal commute prompts removed, unknown records
  kept locally but hidden and non-resubmittable, verified chats readable while
  the history request fails, and records older than the server's newest 20 not
  being deleted.
- commute detail loads without creating a check; an explicit reanalysis creates
  exactly one check and reload restores the same id; a terminal check is read by
  its own `advisory_id`, not `latest`.
- latest weather and the weather stored with an old advisory are displayed
  separately, including “weather updated, advice not recomputed” copy; missing
  weather is honest; old-plan and expired advice are muted history.
- real check phases (`fetching_weather` etc.), unknown results,
  route-unconfigured copy, and a pending operation retired when the plan
  revision changes.
- `runtime_state_check.py` (run by the same runner) fulfills only
  `/api/pa/commute/runtime` with controlled responses while the plan/check stay
  on the isolated real backend. It asserts:
  - route availability, worker heartbeat and task phase stay separate;
  - `not_seen` says the worker has not responded and the task is still waiting;
  - a recent heartbeat says "最近有响应", not permanent online, and does not
    claim the queued task started;
  - an online state whose `last_seen_at` is older than `stale_after_seconds`
    becomes "近期未响应" without changing the task to failed;
  - a failed runtime request shows "暂时无法获取后台状态" and the last successful
    query time, not the previous online claim;
  - a long-open page ages a heartbeat into stale without a new request;
  - hidden pages do not poll and becoming visible refreshes once;
  - an account switch does not inherit the previous account's runtime state;
  - these reads create no checks and make no model calls.
- 360 px overflow, long Markdown/text, and dark mode.
- mock-backed advisories are asserted to carry the 开发/测试数据 label; the
  acceptance model flow no longer accepts a plain "服务端校验" badge.

## Fixed test accounts

These exist only in the temporary database created by the runner:

- `web-e2e-a@example.com` / `e2e-password-2026`
- `web-e2e-b@example.com` / `e2e-password-2026`

They are not real credentials and must not be reused.

## Legacy manual probes

`web_smoke_mobile_home*.py` and `phase4_assistant_*.py` were preserved from the
earlier scratch area. They expect older hand-made fixtures (a specific account
and plan) and are **not** part of `test:e2e`; use the runner above for repeatable
results.


## Extra model flow

`acceptance_model_flows.py` is the heavier "valid model / unknown model" flow. It starts
its own stub, API and web on 8411/3411/8421. The script forces `PYTHONPATH` to this
worktree's `services/assistant-api/src` and prints the actual Python/SQLAlchemy/aiosqlite/
SQLite versions and `assistant_api.__file__` before starting anything; do not point
`ASSISTANT_PYTHON` at a venv whose editable install belongs to another checkout.

As of 2026-10-25 this flow is **intermittent, not a stable pass**: the create-check step
sometimes returns 500 from `ensure_instance()` with
`sqlite3.OperationalError: no such savepoint: sa_savepoint_1`. It reproduced with both
SQLAlchemy 2.0.51 and 2.1.6 after the worktree import fix. Failure evidence and the exact
commands are in `tests/e2e/evidence/model-flow-failure-20261025/`. Do not claim this flow
passed from a single green run; wait for the backend fix and rerun both model outcomes.


## Real matters, drafts and today

`work_plan_check.py` (38 checks) runs against the isolated API built from
`E2E_BACKEND_SRC` (backend/dev `02a79ed2` for this round). It verifies real
`GET /matters`, grouped cards, detail fields, versioned title edits, todo
completion, deleting and reopening, the server-restored draft, conflict
options, apply-option, confirm, draft close-without-save warning, account
switch isolation, real `/today` schedules/reminders, and the lab-only preview.
The preview under `/me/lab/plan` is browser-local and never mixed into the
formal matters list.
