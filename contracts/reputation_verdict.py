# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *

import json
from dataclasses import dataclass


def _addr_str(a: Address) -> str:
    try:
        return a.as_hex
    except Exception:
        return str(a)


def _to_address(val) -> Address:
    if isinstance(val, Address):
        return val
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str.startswith("0x"):
            val_str = "0x" + val_str
        return Address(val_str)
    return Address(val)


@allow_storage
@dataclass
class ReputationReport:
    requester: Address
    subject_id: str
    subject_label: str
    evidence_urls: DynArray[str]
    status: str
    score: bigint
    tier: str
    sources_read: bigint
    reasoning: str
    scored_at: str


class Contract(gl.Contract):
    reports: TreeMap[str, ReputationReport]
    next_id: bigint
    latest_report: TreeMap[str, str]
    owner: Address

    def __init__(self):
        self.owner = _to_address(gl.message.sender_address)
        self.next_id = bigint(1)

    def _require_report(self, report_id: str) -> ReputationReport:
        if report_id not in self.reports:
            raise gl.vm.UserError("Report not found")
        return self.reports[report_id]

    @gl.public.write
    def submit_request(
        self,
        subject_id: str,
        subject_label: str,
        evidence_urls: DynArray[str],
        scored_at: str
    ) -> str:
        if not subject_id or len(subject_id.strip()) == 0:
            raise gl.vm.UserError("subject_id cannot be empty")
        if len(subject_id) > 100:
            raise gl.vm.UserError("subject_id cannot exceed 100 characters")

        if len(subject_label) > 100:
            raise gl.vm.UserError("subject_label cannot exceed 100 characters")

        if not scored_at or len(scored_at.strip()) == 0:
            raise gl.vm.UserError("scored_at cannot be empty")
        if len(scored_at) > 30:
            raise gl.vm.UserError("scored_at cannot exceed 30 characters")

        num_urls = len(evidence_urls)
        if num_urls < 1:
            raise gl.vm.UserError("At least 1 evidence URL required")
        if num_urls > 5:
            raise gl.vm.UserError("Maximum 5 evidence URLs")

        urls_copy = []
        seen = []
        for i in range(num_urls):
            url = evidence_urls[i]
            if not (url.startswith("http://") or url.startswith("https://")):
                raise gl.vm.UserError("Invalid URL")
            if url in seen:
                raise gl.vm.UserError("Duplicate URL")
            seen.append(url)
            urls_copy.append(url)

        report_id = str(self.next_id)
        self.next_id += bigint(1)

        new_report = ReputationReport(
            requester=_to_address(gl.message.sender_address),
            subject_id=subject_id,
            subject_label=subject_label,
            evidence_urls=urls_copy,
            status="PENDING",
            score=bigint(0),
            tier="",
            sources_read=bigint(0),
            reasoning="",
            scored_at=scored_at
        )
        self.reports[report_id] = new_report
        self.latest_report[subject_id] = report_id
        return report_id

    @gl.public.write
    def evaluate_reputation(self, report_id: str) -> None:
        report = self._require_report(report_id)
        if report.status != "PENDING":
            raise gl.vm.UserError("Report is not PENDING")

        evidence_urls_list = [report.evidence_urls[i] for i in range(len(report.evidence_urls))]
        subject_id_cap = report.subject_id
        subject_label_cap = report.subject_label

        def leader_fn():
            evidence_results = []
            readable_count = 0
            for url in evidence_urls_list:
                try:
                    res = gl.nondet.web.render(url)
                    body = res.body if hasattr(res, "body") else str(res)
                    if body and len(body.strip()) > 30:
                        evidence_results.append(
                            "Evidence [" + url + "]:\n" + body[:3000]
                        )
                        readable_count += 1
                    else:
                        evidence_results.append(
                            "Evidence [" + url + "]: (empty or too short)"
                        )
                except Exception as e:
                    evidence_results.append(
                        "Evidence [" + url + "]: (fetch failed: " + str(e) + ")"
                    )

            prompt = f"""You are an expert AI agent reputation analyst on GenLayer.
Your job is to assess the trustworthiness and reliability of an AI agent
or blockchain address based on publicly available evidence.

Subject Identifier: {subject_id_cap}
Subject Description: {subject_label_cap if subject_label_cap else "Not provided"}

Public evidence fetched on-chain:
{chr(10).join(evidence_results)}

Evaluate the subject's reputation based on the following signals:
- Activity history: volume, consistency, and age of on-chain or platform activity
- Quality indicators: code quality, successful deliveries, positive reviews
- Risk signals: disputes, scam reports, irregular patterns, very new account
- Independence: are sources genuinely independent or all from the same platform
- Transparency: is the subject's identity and work publicly verifiable

Scoring guide:
- 80-100 (TRUSTED): Strong positive track record, multiple independent sources confirm reliability
- 50-79 (NEUTRAL): Mixed or limited evidence, no major red flags but not enough to fully trust
- 0-49 (RISKY): Red flags present, negative history, suspicious patterns, or insufficient verifiable history

If fewer than 1 source returned readable content, return INCONCLUSIVE.

Return ONLY raw JSON, no markdown, no backticks:
{{"score": <0-100>,
  "tier": "TRUSTED"|"NEUTRAL"|"RISKY"|"INCONCLUSIVE",
  "confidence": <0-100>,
  "sources_read": <integer>,
  "reasoning": "<2-3 sentences citing specific evidence signals>"}}"""

            raw = gl.nondet.exec_prompt(prompt, response_format="json")

            try:
                if isinstance(raw, dict):
                    parsed = raw
                else:
                    cleaned = str(raw).strip()
                    if cleaned.startswith("```json"):
                        cleaned = cleaned[7:]
                    if cleaned.startswith("```"):
                        cleaned = cleaned[3:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    parsed = json.loads(cleaned.strip())

                tier = parsed.get("tier", "INCONCLUSIVE")
                if tier not in ["TRUSTED", "NEUTRAL", "RISKY", "INCONCLUSIVE"]:
                    tier = "INCONCLUSIVE"

                try:
                    score = max(0, min(100, int(parsed.get("score", 0))))
                except Exception:
                    score = 0

                if tier == "TRUSTED" and score < 80:
                    score = 80
                if tier == "NEUTRAL" and (score < 50 or score > 79):
                    score = 65
                if tier == "RISKY" and score > 49:
                    score = 30
                if tier == "INCONCLUSIVE":
                    score = 0

                try:
                    conf = max(0, min(100, int(parsed.get("confidence", 0))))
                except Exception:
                    conf = 0

                try:
                    src_read = max(0, min(len(evidence_urls_list),
                                    int(parsed.get("sources_read", readable_count))))
                except Exception:
                    src_read = readable_count

                return {
                    "score": score,
                    "tier": tier,
                    "confidence": conf,
                    "sources_read": src_read,
                    "reasoning": str(parsed.get("reasoning", ""))
                }
            except Exception as e:
                return {
                    "score": 0,
                    "tier": "INCONCLUSIVE",
                    "confidence": 0,
                    "sources_read": readable_count,
                    "reasoning": "Parse error: " + str(e)
                }

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            lp = leader_res.calldata
            if not isinstance(lp, dict):
                return False

            leader_tier = lp.get("tier")
            leader_score = lp.get("score")
            leader_conf = lp.get("confidence")

            if leader_tier not in ["TRUSTED", "NEUTRAL", "RISKY", "INCONCLUSIVE"]:
                return False
            try:
                ls = int(leader_score)
                lc = int(leader_conf)
                if not (0 <= ls <= 100):
                    return False
                if not (0 <= lc <= 100):
                    return False
            except Exception:
                return False

            try:
                my_result = leader_fn()
            except Exception:
                return False

            if my_result.get("tier") != leader_tier:
                return False

            try:
                ms = int(my_result.get("score", 0))
                if abs(ms - ls) > 5:
                    return False
            except Exception:
                return False

            try:
                mc = int(my_result.get("confidence", 0))
                if not (0 <= mc <= 100):
                    return False
            except Exception:
                return False

            def _band(c):
                if c < 35:
                    return 1
                elif c < 80:
                    return 2
                else:
                    return 3

            return _band(mc) == _band(lc)

        ruling = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        result_data = ruling.calldata if hasattr(ruling, "calldata") else ruling

        result_tier = result_data.get("tier", "INCONCLUSIVE")
        result_score = int(result_data.get("score", 0))

        report.score = bigint(result_score)
        report.tier = result_tier
        report.sources_read = bigint(int(result_data.get("sources_read", 0)))
        report.reasoning = str(result_data.get("reasoning", ""))

        if result_tier == "INCONCLUSIVE" or report.sources_read == bigint(0):
            report.status = "INCONCLUSIVE"
        else:
            report.status = "SCORED"

        self.reports[report_id] = report

    @gl.public.write
    def invalidate_report(self, report_id: str) -> None:
        report = self._require_report(report_id)

        sender = _to_address(gl.message.sender_address)
        if sender != self.owner:
            raise gl.vm.UserError("Only owner can invalidate reports")

        if report.status != "PENDING":
            raise gl.vm.UserError("Report is not PENDING")

        report.status = "INCONCLUSIVE"
        report.reasoning = "Invalidated by admin"
        self.reports[report_id] = report

    @gl.public.view
    def get_report(self, report_id: str) -> str:
        report = self._require_report(report_id)
        urls = [report.evidence_urls[i] for i in range(len(report.evidence_urls))]
        res = {
            "requester": _addr_str(report.requester),
            "subject_id": report.subject_id,
            "subject_label": report.subject_label,
            "evidence_urls": urls,
            "status": report.status,
            "score": int(report.score),
            "tier": report.tier,
            "sources_read": int(report.sources_read),
            "reasoning": report.reasoning,
            "scored_at": report.scored_at
        }
        return json.dumps(res)

    @gl.public.view
    def get_latest_report(self, subject_id: str) -> str:
        if subject_id not in self.latest_report:
            raise gl.vm.UserError("No report found for this subject")
        report_id = self.latest_report[subject_id]
        report = self._require_report(report_id)
        urls = [report.evidence_urls[i] for i in range(len(report.evidence_urls))]
        res = {
            "requester": _addr_str(report.requester),
            "subject_id": report.subject_id,
            "subject_label": report.subject_label,
            "evidence_urls": urls,
            "status": report.status,
            "score": int(report.score),
            "tier": report.tier,
            "sources_read": int(report.sources_read),
            "reasoning": report.reasoning,
            "scored_at": report.scored_at
        }
        return json.dumps(res)

    @gl.public.view
    def get_score(self, report_id: str) -> str:
        report = self._require_report(report_id)
        if report.status == "PENDING":
            raise gl.vm.UserError("Not yet evaluated")
        res = {
            "subject_id": report.subject_id,
            "score": int(report.score),
            "tier": report.tier,
            "sources_read": int(report.sources_read),
            "reasoning": report.reasoning,
            "status": report.status
        }
        return json.dumps(res)

    @gl.public.view
    def list_reports(self, status_filter: str) -> str:
        if status_filter not in ["", "PENDING", "SCORED", "INCONCLUSIVE"]:
            raise gl.vm.UserError("Invalid status filter")

        results = []
        limit = int(self.next_id)
        for i in range(1, limit):
            rid = str(i)
            if rid in self.reports:
                report = self.reports[rid]
                if status_filter == "" or report.status == status_filter:
                    results.append({
                        "report_id": rid,
                        "subject_id": report.subject_id,
                        "subject_label": report.subject_label,
                        "status": report.status,
                        "score": int(report.score),
                        "tier": report.tier
                    })
        return json.dumps(results)

    @gl.public.view
    def get_count(self) -> int:
        return int(self.next_id) - 1
