# ReputationVerdict -- On-Chain AI Agent Reputation Scoring on GenLayer

**One-line pitch:** ReputationVerdict dies without GenLayer: no EVM contract can read a GitHub profile and an on-chain explorer page simultaneously, reason about activity patterns, and produce a multi-node consensus reputation score -- only GenLayer's AI validators can fetch public evidence on-chain and deliver a tamper-proof attestation.

---

## Problem & Solution

In the emerging AI agent economy (A2A commerce), Agent A wants to hire Agent B but has no neutral way to verify B's trustworthiness. Self-reported claims and single-platform reviews can be gamed.

ReputationVerdict lets anyone request a reputation assessment of any address or agent identifier by providing 1-5 public evidence URLs (GitHub profile, explorer page, platform profile, portfolio, community reviews). The contract reads those sources on-chain and AI validators independently compute a Reputation Score (0-100) plus a tier (`TRUSTED` / `NEUTRAL` / `RISKY`) stored permanently on-chain.

Remove `web.render` + the LLM and this contract is just a registry that stores whatever score the requester types in. The AI aggregation **is** the product.

---

## How it works

```
submit_request  ->  PENDING
       |
       +--> evaluate_reputation (permissionless, nondet consensus)
       |         -> SCORED or INCONCLUSIVE
       |
       +--> invalidate_report (owner only)  ->  INCONCLUSIVE
```

1. **Submit** -- `submit_request(subject_id, subject_label, evidence_urls, scored_at)` stores a `PENDING` report and returns a `report_id` (`"1"`, `"2"`, ...). Also updates `latest_report[subject_id]`.
2. **Evaluate** -- anyone may call `evaluate_reputation(report_id)`. Validators fetch each evidence URL, prompt the LLM reputation analyst, and agree on tier + score within tolerance.
3. **Read** -- `get_score`, `get_report`, `get_latest_report`, and `list_reports` expose the attestation.

Owner-only `invalidate_report` marks spam or clearly invalid `PENDING` submissions as `INCONCLUSIVE`.

---

## Consensus design: meaning, not format

`evaluate_reputation` uses `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)`. This is **semantic consensus**, not a JSON-shape check.

1. **Independent execution (`leader_fn`)**
   - Each node fetches every evidence URL with `gl.nondet.web.render`.
   - Fetch failures are captured as text; the leader never throws.
   - An LLM reputation analyst returns score, tier, confidence, sources_read, and reasoning.
   - Tier/score consistency is enforced (e.g. `TRUSTED` forces score >= 80).

2. **Equivalence on the decision (`validator_fn`)**
   - The validator re-runs `leader_fn` on its own web fetches and LLM call.
   - **Tier match (exact):** `TRUSTED` / `NEUTRAL` / `RISKY` / `INCONCLUSIVE` must match. Tier drives the trust decision.
   - **Score tolerance:** +/- 5 within the same tier.
   - **Confidence banding:** scores are grouped into three bands (0-34, 35-79, 80-100). Same band is enough.
   - Reasoning text is not compared.

---

## Public API

### Write methods

- `submit_request(subject_id, subject_label, evidence_urls, scored_at) -> str`
- `evaluate_reputation(report_id) -> None` (permissionless)
- `invalidate_report(report_id) -> None` (owner only, `PENDING` only)

### View methods (JSON strings except `get_count`)

- `get_report(report_id) -> str`
- `get_latest_report(subject_id) -> str`
- `get_score(report_id) -> str`
- `list_reports(status_filter) -> str` -- `""` / `PENDING` / `SCORED` / `INCONCLUSIVE`
- `get_count() -> int`

---

## Tiers

| Tier | Score range | Meaning |
| --- | --- | --- |
| `TRUSTED` | 80-100 | Strong positive track record from independent sources |
| `NEUTRAL` | 50-79 | Mixed or limited evidence, no major red flags |
| `RISKY` | 0-49 | Red flags, negative history, or suspicious patterns |
| `INCONCLUSIVE` | 0 | No readable sources, parse failure, or admin invalidate |

---

## Edge cases handled

- **Web fetch failure:** every `web.render` is wrapped in try/except; `leader_fn` never throws.
- **All sources fail:** still produces an `INCONCLUSIVE` attestation.
- **Partial source failure:** one dead URL and one readable page still yield a verdict.
- **LLM parse failure / unknown tier:** normalize to `INCONCLUSIVE` / score 0.
- **Tier/score mismatch:** leader corrects score to the tier band before consensus.
- **Submit guards:** non-empty `subject_id`, 1-5 unique `http(s)` URLs, non-empty `scored_at`.
- **Already evaluated:** `evaluate_reputation` only runs while status is `PENDING`.
- **Pending score read:** `get_score` reverts with `Not yet evaluated`.

---

## Use cases

- **A2A hiring** -- Agent A checks Agent B's on-chain reputation before paying for a task
- **Escrow / marketplace gates** -- require `TRUSTED` (or minimum score) before unlocking funds
- **Agent directories** -- cache the latest report per `subject_id` for discovery UIs

---

## Local tests

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
gltest tests/
```

Latest recorded run: **20 passed**.

---

## Deployment

- **CONTRACT_ADDRESS:** `0xb5129E7FfD77cA96177a37A7efFbE65923cC8a74`
- **NETWORK:** `studionet`
- **Explorer:** https://explorer-studio.genlayer.com/address/0xb5129E7FfD77cA96177a37A7efFbE65923cC8a74
- **Studio:** https://studio.genlayer.com
- **Suggested public repo name:** `reputation-verdict`
- **Contract file:** `contracts/reputation_verdict.py` (`class Contract`)

### Live read (real result, 2026-09-22)

A read-only call against the studionet address returned:

- `get_count()` -> `0`
- `list_reports("")` -> `[]`
- schema methods: `submit_request`, `evaluate_reputation`, `invalidate_report`, `get_report`, `get_latest_report`, `get_score`, `list_reports`, `get_count`

Deploy tx on explorer: constructor `FINALIZED` / `Accepted` (created Sep 22, 2026). No reputation reports have been submitted on this deployment yet, so there is no on-chain `evaluate_reputation` receipt to quote. The call sequence below is an **illustrative expected example** taken from the passing unit tests (same public API the live schema exposes).

### Worked example (illustrative expected output)

```
submit_request(
  subject_id     = "0xABC123...agent",
  subject_label  = "AI trading agent",
  evidence_urls  = [
    "https://github.com/agent-abc",
    "https://etherscan.io/address/0xABC"
  ],
  scored_at      = "2026-09-01T10:00:00Z"
)
-> report_id "1"

evaluate_reputation("1")   # validators fetch both pages and agree on meaning

get_score("1") expected:
{
  "subject_id": "0xABC123...agent",
  "score": 88,
  "tier": "TRUSTED",
  "sources_read": 2,
  "reasoning": "Strong GitHub history and clean on-chain record confirm reliability.",
  "status": "SCORED"
}
```

When those two sources show long GitHub activity and a clean explorer history, validators should store `TRUSTED` with score in 80-100.

### How to redeploy

1. Open [GenLayer Studio](https://studio.genlayer.com) and connect a wallet.
2. Paste `contracts/reputation_verdict.py`.
3. Deploy to **studionet**.
4. Update this README with the new address if it changes.

---

## File layout

```
contracts/reputation_verdict.py
tests/test_reputation_verdict.py
tests/conftest.py
requirements-dev.txt
README.md
```
