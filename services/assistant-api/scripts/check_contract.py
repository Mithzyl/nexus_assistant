"""Verify the backend against the fields committed clients actually read.

History: the previous version parsed `v3-kit/source/src/api/contract.ts`. That
directory is ignored by `.gitignore` and absent from a clean checkout, so the
check failed for the wrong reason (missing file at best, a stale copy at worst).
The authority is now `docs/backend-integration/client-contract.json`, whose
entries name the tracked files they are derived from:

* `apps/web/lib/mobile/api.ts`             web response types
* `apps/web/app/api/auth/login/route.ts`   web login response (includes server_time)
* `apps/web/lib/mobile/session.ts`         web renewal response
* `ios/.../PA/PAModels.swift`              Swift auth and ask models
* `ios/.../Commute/CommuteModels.swift`    Swift commute models

Rules:

1. every declared interface exists in the backend OpenAPI;
2. every declared field exists in that schema (the client must be able to decode it);
3. every field declared `required` is required by the backend schema;
4. every declared route + method exists;
5. every evidence file exists, and each declared field name appears in at least
   one of its interface's evidence files (a cheap drift alarm, not a parser).

Backend-only fields and routes are reported as coverage notes, never as failures:
additive backend growth must not break a client. Run it from the service dir:

    .venv/bin/python scripts/check_contract.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent.parent
CONTRACT_PATH = REPO_ROOT / "docs" / "backend-integration" / "client-contract.json"


def normalize(path: str) -> str:
    """Collapse path parameters so both sides can be compared."""

    return re.sub(r"\{[^}]*\}|\$\{[^}]*\}", "{}", path)


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_spec() -> dict[str, Any]:
    """Build OpenAPI with explicit, environment-independent settings."""

    from assistant_api.core.config import Settings
    from assistant_api.main import create_app

    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        allow_schema_create=False,
    )
    return create_app(settings).openapi()


def evaluate(
    spec: dict[str, Any],
    contract: dict[str, Any],
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Return rows, failures, uncovered routes and backend-only field notes."""

    schemas = spec.get("components", {}).get("schemas", {})
    paths = {normalize(path): item for path, item in spec.get("paths", {}).items()}
    failures: list[str] = []
    rows: list[tuple[str, str]] = []
    backend_only: dict[str, list[str]] = {}
    evidence_cache: dict[str, str] = {}

    def evidence_text(relative: str) -> str | None:
        if relative not in evidence_cache:
            candidate = repo_root / relative
            evidence_cache[relative] = (
                candidate.read_text(encoding="utf-8") if candidate.is_file() else ""
            )
        return evidence_cache[relative] or None

    for name in sorted(contract.get("interfaces", {})):
        entry = contract["interfaces"][name]
        fields = list(dict.fromkeys(entry.get("fields", [])))
        required = list(dict.fromkeys(entry.get("required", [])))
        evidence = list(entry.get("evidence", []))
        problems: list[str] = []

        schema = schemas.get(name)
        if schema is None:
            failures.append(f"{name}: interface is not published by the backend")
            rows.append((name, "MISSING in backend"))
            continue
        properties = set(schema.get("properties", {}))
        backend_required = set(schema.get("required", []))

        missing = sorted(set(fields) - properties)
        if missing:
            problems.append(f"not in backend: {missing}")
            failures.append(f"{name}: fields missing in backend: {missing}")
        unknown_required = sorted(set(required) - backend_required)
        if unknown_required:
            problems.append(f"not required by backend: {unknown_required}")
            failures.append(f"{name}: declared required but optional in backend: {unknown_required}")
        if not set(required) <= set(fields):
            extra = sorted(set(required) - set(fields))
            problems.append(f"required not declared as fields: {extra}")
            failures.append(f"{name}: required fields not listed in fields: {extra}")

        if not evidence:
            problems.append("no evidence file")
            failures.append(f"{name}: no client evidence file recorded")
        for relative in evidence:
            if evidence_text(relative) is None:
                problems.append(f"missing evidence: {relative}")
                failures.append(f"{name}: evidence file not found: {relative}")
        available = [evidence_text(relative) or "" for relative in evidence]
        for field in fields:
            if not any(field in text for text in available):
                problems.append(f"field not found in client evidence: {field}")
                failures.append(f"{name}: {field} is not visible in any evidence file")

        extra_fields = sorted(properties - set(fields))
        if extra_fields:
            backend_only[name] = extra_fields
        rows.append(
            (name, "; ".join(problems) if problems else f"ok ({len(fields)} fields)")
        )

    covered_paths: set[str] = set()
    for endpoint in contract.get("endpoints", []):
        raw_path = endpoint.get("path", "")
        key = normalize(raw_path)
        covered_paths.add(key)
        methods = [method.lower() for method in endpoint.get("methods", [])]
        label = f"{','.join(m.upper() for m in methods)} {raw_path}"
        problems = []
        operation = paths.get(key)
        if operation is None:
            problems.append("route not registered")
            failures.append(f"endpoint {label}: route not registered")
        else:
            missing_methods = [m for m in methods if m not in operation]
            if missing_methods:
                problems.append(f"method(s) missing: {missing_methods}")
                failures.append(f"endpoint {label}: missing method(s) {missing_methods}")
        for relative in endpoint.get("evidence", []):
            if evidence_text(relative) is None:
                problems.append(f"missing evidence: {relative}")
                failures.append(f"endpoint {label}: evidence file not found: {relative}")
        rows.append((f"endpoint {label}", "; ".join(problems) if problems else "ok"))

    uncovered = []
    for raw_path in sorted(spec.get("paths", {})):
        if normalize(raw_path) in covered_paths:
            continue
        methods = sorted(
            m for m in spec["paths"][raw_path] if m.isalpha()
        )
        uncovered.append(
            f"{raw_path} [{', '.join(m.upper() for m in methods)}]"
        )

    return {
        "rows": rows,
        "failures": failures,
        "uncovered": uncovered,
        "backend_only": backend_only,
        "interface_count": len(contract.get("interfaces", {})),
        "endpoint_count": len(contract.get("endpoints", [])),
    }


def main() -> int:
    contract = load_contract()
    result = evaluate(build_spec(), contract)

    width = max(len(name) for name, _ in result["rows"]) if result["rows"] else 0
    print(f"contract: {CONTRACT_PATH.relative_to(REPO_ROOT)}")
    for name, detail in result["rows"]:
        print(f"  {name:<{width}}  {detail}")

    if result["backend_only"]:
        print()
        print("backend-only fields (additive, clients may ignore):")
        for name, fields in sorted(result["backend_only"].items()):
            print(f"  {name}: {', '.join(fields)}")

    if result["uncovered"]:
        print()
        print("backend routes not covered by the client contract:")
        for item in result["uncovered"]:
            print(f"  - {item}")

    print()
    if result["failures"]:
        print(f"{len(result['failures'])} mismatch(es):")
        for failure in result["failures"]:
            print(f"  - {failure}")
        return 1
    print(
        f"contract ok: {result['interface_count']} interfaces, "
        f"{result['endpoint_count']} routes, "
        f"{len(result['backend_only'])} interfaces with additive fields"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
