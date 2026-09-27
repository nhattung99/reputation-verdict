import json


def _web(vm, pattern, body):
    """Register a web mock in the flat dict format gltest expects."""
    if callable(body):
        vm.mock_web(pattern, body)
    else:
        vm.mock_web(pattern, {"method": "GET", "status": 200, "body": str(body)})


def _submit(
    contract,
    direct_vm,
    caller,
    subject_id="agent-x",
    subject_label="",
    urls=None,
    scored_at="2026-01-01",
):
    if urls is None:
        urls = ["https://a.com"]
    with direct_vm.prank(caller):
        return contract.submit_request(subject_id, subject_label, urls, scored_at)


def _mock_trusted_sources(direct_vm):
    _web(direct_vm, r".*github.*", "500+ commits, 3 years active, 120 stars on public repositories")
    _web(direct_vm, r".*etherscan.*", "2,400 transactions, 0 failed, active since 2023 with clean history")


def _mock_llm(direct_vm, payload):
    direct_vm.mock_llm(r".*reputation analyst.*", json.dumps(payload))


def test_trusted_score_happy_path(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")

    with direct_vm.prank(direct_alice):
        report_id = contract.submit_request(
            "0xABC123...agent",
            "AI trading agent",
            ["https://github.com/agent-abc", "https://etherscan.io/address/0xABC"],
            "2026-09-01T10:00:00Z"
        )
    assert report_id == "1"
    assert contract.get_count() == 1

    _web(direct_vm, r".*github.*", "500+ commits, 3 years active, 120 stars on public repositories")
    _web(direct_vm, r".*etherscan.*", "2,400 transactions, 0 failed, active since 2023 with clean history")

    direct_vm.mock_llm(r".*reputation analyst.*", json.dumps({
        "score": 88,
        "tier": "TRUSTED",
        "confidence": 91,
        "sources_read": 2,
        "reasoning": "Strong GitHub history and clean on-chain record confirm reliability."
    }))

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["tier"] == "TRUSTED"
    assert result["score"] == 88
    assert result["sources_read"] == 2
    assert result["status"] == "SCORED"


def test_neutral_score(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        subject_id="agent-neutral",
        subject_label="new agent",
        urls=["https://github.com/agent-neutral", "https://etherscan.io/address/0xNEUT"],
        scored_at="2026-03-01T00:00:00Z",
    )
    _mock_trusted_sources(direct_vm)
    _mock_llm(direct_vm, {
        "score": 65,
        "tier": "NEUTRAL",
        "confidence": 55,
        "sources_read": 2,
        "reasoning": "Limited history with mixed signals and no major red flags."
    })

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["tier"] == "NEUTRAL"
    assert result["score"] == 65
    assert result["status"] == "SCORED"


def test_risky_score(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        subject_id="agent-risky",
        subject_label="disputed agent",
        urls=["https://github.com/agent-risky", "https://etherscan.io/address/0xRISK"],
        scored_at="2026-04-01T00:00:00Z",
    )
    _web(direct_vm, r".*github.*", "Brand new account with almost no commits and deleted repos")
    _web(direct_vm, r".*etherscan.*", "Multiple failed txs and community scam reports linked to this address")
    _mock_llm(direct_vm, {
        "score": 25,
        "tier": "RISKY",
        "confidence": 78,
        "sources_read": 2,
        "reasoning": "Red flags include scam reports and irregular on-chain patterns."
    })

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["tier"] == "RISKY"
    assert result["score"] == 25
    assert result["status"] == "SCORED"
    assert "red" in result["reasoning"].lower() or "scam" in result["reasoning"].lower()


def test_all_sources_fail_inconclusive(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        urls=["https://down-a.example.com/x", "https://down-b.example.com/y"],
    )

    # Unmocked URLs raise MockNotFoundError inside web.render (caught as fetch fail).
    # Intentionally no successful web mocks for down-a / down-b.
    _mock_llm(direct_vm, {
        "score": 99,
        "tier": "TRUSTED",
        "confidence": 99,
        "sources_read": 5,
        "reasoning": "Should be ignored when no evidence is readable."
    })

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["status"] == "INCONCLUSIVE"
    assert result["tier"] == "INCONCLUSIVE"
    assert result["score"] == 0
    assert result["sources_read"] == 0


def test_sources_read_derived_from_fetch(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        urls=["https://github.com/agent-src", "https://etherscan.io/address/0xSRC"],
    )
    _mock_trusted_sources(direct_vm)
    # LLM lies about sources_read; contract must use actual fetch count (2).
    _mock_llm(direct_vm, {
        "score": 82,
        "tier": "TRUSTED",
        "confidence": 88,
        "sources_read": 99,
        "reasoning": "Two independent readable sources support a trusted score."
    })

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["status"] == "SCORED"
    assert result["tier"] == "TRUSTED"
    assert result["sources_read"] == 2


def test_empty_bodies_zero_source_inconclusive(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        urls=["https://empty-a.example.com/x", "https://empty-b.example.com/y"],
    )
    _web(direct_vm, r".*empty-a.*", "short")
    _web(direct_vm, r".*empty-b.*", "  ")
    _mock_llm(direct_vm, {
        "score": 70,
        "tier": "NEUTRAL",
        "confidence": 60,
        "sources_read": 2,
        "reasoning": "Should not apply when bodies are too short to count as readable."
    })

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["status"] == "INCONCLUSIVE"
    assert result["tier"] == "INCONCLUSIVE"
    assert result["score"] == 0
    assert result["sources_read"] == 0


def test_partial_source_failure(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        urls=["https://github.com/agent-partial", "https://down.example.com/fail"],
    )

    _web(direct_vm, r".*github.*", "Consistent open-source activity over two years with reviews")
    # down.example is intentionally unmocked so web.render fails for that URL.
    _mock_llm(direct_vm, {
        "score": 72,
        "tier": "NEUTRAL",
        "confidence": 60,
        "sources_read": 1,
        "reasoning": "One source failed but the readable GitHub history supports a cautious neutral score."
    })

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["status"] == "SCORED"
    assert result["tier"] == "NEUTRAL"
    assert result["sources_read"] == 1


def test_single_url_allowed(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        urls=["https://github.com/single-agent"],
        scored_at="2026-05-01T12:00:00Z",
    )
    _web(direct_vm, r".*github.*", "Moderate contribution history with a few public repositories")
    _mock_llm(direct_vm, {
        "score": 58,
        "tier": "NEUTRAL",
        "confidence": 48,
        "sources_read": 1,
        "reasoning": "Single independent source shows limited but clean activity."
    })

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["status"] == "SCORED"
    assert result["sources_read"] == 1


def test_empty_urls_rejected(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("At least 1 evidence URL required"):
            contract.submit_request("agent-x", "", [], "2026-01-01")


def test_too_many_urls_rejected(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Maximum 5 evidence URLs"):
            contract.submit_request("agent-x", "", [
                "https://a.com", "https://b.com", "https://c.com",
                "https://d.com", "https://e.com", "https://f.com"
            ], "2026-01-01")


def test_invalid_url_rejected(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Invalid URL"):
            contract.submit_request("agent-x", "",
                ["not-a-url", "https://b.com"], "2026-01-01")


def test_duplicate_urls_rejected(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Duplicate"):
            contract.submit_request("agent-x", "",
                ["https://a.com", "https://a.com"], "2026-01-01")


def test_evaluate_already_scored(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        urls=["https://github.com/agent-scored"],
    )
    _web(direct_vm, r".*github.*", "Solid contribution history across multiple repositories")
    _mock_llm(direct_vm, {
        "score": 84,
        "tier": "TRUSTED",
        "confidence": 88,
        "sources_read": 1,
        "reasoning": "Public history supports a trusted reputation."
    })
    contract.evaluate_reputation(report_id)

    with direct_vm.expect_revert("not PENDING"):
        contract.evaluate_reputation(report_id)


def test_get_score_pending(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    with direct_vm.prank(direct_alice):
        report_id = contract.submit_request(
            "agent-x", "", ["https://a.com"], "2026-01-01")
    with direct_vm.expect_revert("Not yet evaluated"):
        contract.get_score(report_id)


def test_latest_report_updated(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy("contracts/reputation_verdict.py")

    with direct_vm.prank(direct_alice):
        id1 = contract.submit_request(
            "agent-x", "first", ["https://a.com"], "2026-01-01")
    assert id1 == "1"

    with direct_vm.prank(direct_bob):
        id2 = contract.submit_request(
            "agent-x", "second", ["https://b.com"], "2026-02-01")
    assert id2 == "2"

    latest = json.loads(contract.get_latest_report("agent-x"))
    assert latest["subject_label"] == "second"
    assert latest["scored_at"] == "2026-02-01"


def test_latest_report_unknown(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    with direct_vm.expect_revert("No report found"):
        contract.get_latest_report("nonexistent-agent")


def test_malformed_llm_json_fallback(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        urls=["https://github.com/agent-badjson"],
    )
    _web(direct_vm, r".*github.*", "Readable profile content with contribution history present")
    direct_vm.mock_llm(r".*reputation analyst.*", "not valid json!!")

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["status"] == "INCONCLUSIVE"
    assert result["tier"] == "INCONCLUSIVE"
    assert result["score"] == 0


def test_invalid_tier_normalized(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        urls=["https://github.com/agent-tier"],
    )
    _web(direct_vm, r".*github.*", "Readable profile content with contribution history present")
    _mock_llm(direct_vm, {
        "tier": "EXCELLENT",
        "score": 95,
        "confidence": 90,
        "sources_read": 1,
        "reasoning": "Model returned an unknown tier label."
    })

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["tier"] == "INCONCLUSIVE"
    assert result["status"] == "INCONCLUSIVE"
    assert result["score"] == 0


def test_tier_score_consistency(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        urls=["https://github.com/agent-consistency"],
    )
    _web(direct_vm, r".*github.*", "Long track record with strong reviews and deliveries")
    _mock_llm(direct_vm, {
        "tier": "TRUSTED",
        "score": 40,
        "confidence": 85,
        "sources_read": 1,
        "reasoning": "Tier says trusted but raw score was inconsistent."
    })

    contract.evaluate_reputation(report_id)

    result = json.loads(contract.get_score(report_id))
    assert result["tier"] == "TRUSTED"
    assert result["score"] >= 80
    assert result["status"] == "SCORED"


def test_list_reports_filter(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy("contracts/reputation_verdict.py")

    with direct_vm.prank(direct_alice):
        id1 = contract.submit_request(
            "agent-one", "one", ["https://github.com/one"], "2026-01-01")
    with direct_vm.prank(direct_bob):
        id2 = contract.submit_request(
            "agent-two", "two", ["https://github.com/two"], "2026-02-01")

    _web(direct_vm, r".*github.*", "Readable contribution history for filtering test")
    _mock_llm(direct_vm, {
        "score": 70,
        "tier": "NEUTRAL",
        "confidence": 60,
        "sources_read": 1,
        "reasoning": "Enough evidence for a neutral score."
    })
    contract.evaluate_reputation(id1)

    pending = json.loads(contract.list_reports("PENDING"))
    assert len(pending) == 1
    assert pending[0]["report_id"] == id2

    scored = json.loads(contract.list_reports("SCORED"))
    assert len(scored) == 1
    assert scored[0]["report_id"] == id1

    all_rows = json.loads(contract.list_reports(""))
    assert len(all_rows) == 2


def test_invalidate_owner_only(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy("contracts/reputation_verdict.py")
    report_id = _submit(contract, direct_vm, direct_alice)

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Only owner"):
            contract.invalidate_report(report_id)

    contract.invalidate_report(report_id)

    report = json.loads(contract.get_report(report_id))
    assert report["status"] == "INCONCLUSIVE"
    assert report["reasoning"] == "Invalidated by admin"


def test_empty_subject_id(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/reputation_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("subject_id cannot be empty"):
            contract.submit_request("", "", ["https://a.com"], "2026-01-01")
