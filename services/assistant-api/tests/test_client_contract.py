"""The client contract check must be strict, self-contained and honest.

It compares the backend OpenAPI with fields the committed Web/Swift clients read.
These tests prove the checker fails when a client-visible field disappears or a
route vanishes, and that it does not silently drop `server_time`.
"""

from __future__ import annotations

import copy

from scripts.check_contract import (
    CONTRACT_PATH,
    REPO_ROOT,
    build_spec,
    evaluate,
    load_contract,
)


def _result(contract=None, spec=None):
    return evaluate(
        spec or build_spec(),
        contract or load_contract(CONTRACT_PATH),
        REPO_ROOT,
    )


def test_current_clients_match_the_backend():
    result = _result()
    assert result["failures"] == []
    assert result["interface_count"] >= 14
    # Round-1 acceptance added the three client-read routes that used to be
    # reported as uncovered, so they must no longer appear there.
    uncovered = "\n".join(result["uncovered"])
    assert "/api/v1/asks/verify" not in uncovered
    assert "/api/v1/commute-plans/{plan_id}/weather" not in uncovered
    assert "/api/v1/commute/runtime" not in uncovered
    # Routes neither client calls are still listed instead of being treated as covered.
    assert "/health" in uncovered


def test_server_time_stays_in_the_login_contract():
    entry = load_contract(CONTRACT_PATH)["interfaces"]["LoginResponse"]
    assert "server_time" in entry["fields"]
    assert "server_time" in entry["required"]
    schema = build_spec()["components"]["schemas"]["LoginResponse"]
    assert "server_time" in schema["required"]


def test_a_renamed_field_is_a_failure():
    contract = copy.deepcopy(load_contract(CONTRACT_PATH))
    contract["interfaces"]["AskRead"]["fields"] = [
        "local_run_idd" if field == "local_run_id" else field
        for field in contract["interfaces"]["AskRead"]["fields"]
    ]
    contract["interfaces"]["AskRead"]["required"] = [
        "local_run_idd" if field == "local_run_id" else field
        for field in contract["interfaces"]["AskRead"]["required"]
    ]
    result = _result(contract)
    assert any("local_run_idd" in failure for failure in result["failures"])


def test_claiming_a_required_field_the_backend_does_not_require_fails():
    contract = copy.deepcopy(load_contract(CONTRACT_PATH))
    contract["interfaces"]["AskRead"]["required"].append("replayed")
    result = _result(contract)
    assert any("replayed" in failure for failure in result["failures"])


def test_a_missing_route_is_a_failure():
    contract = copy.deepcopy(load_contract(CONTRACT_PATH))
    contract["endpoints"].append(
        {
            "path": "/api/v1/does-not-exist",
            "methods": ["get"],
            "evidence": ["apps/web/lib/mobile/api.ts"],
        }
    )
    result = _result(contract)
    assert any("/api/v1/does-not-exist" in failure for failure in result["failures"])


def test_every_recorded_evidence_file_is_tracked_in_this_repository():
    contract = load_contract(CONTRACT_PATH)
    files = {
        path
        for entry in contract["interfaces"].values()
        for path in entry.get("evidence", [])
    } | {
        path
        for endpoint in contract["endpoints"]
        for path in endpoint.get("evidence", [])
    }
    assert files
    for relative in sorted(files):
        assert (REPO_ROOT / relative).is_file(), relative
