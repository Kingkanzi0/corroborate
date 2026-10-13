# Corroborate

**Project: Corroborate** · a reusable GenLayer Intelligent Contract for multi-source claim verification.

Many apps need one answer to "is this statement true right now?": prediction markets settling an outcome, quiz and education apps, insurance triggers, content moderation, grant milestones. A single LLM call is not enough, and a single web page can be wrong. Corroborate turns a claim plus independent sources into a verdict that other contracts can read on-chain.

## How it decides

1. A claim comes with **2 or 3 sources on different domains** (checked deterministically, `www.` ignored).
2. Under consensus, each source gets a **stance**: `SUPPORTS`, `CONTRADICTS` or `UNCLEAR`, judged from that page's text only.
3. The **verdict is fixed code**, not AI:

| Stances | Verdict |
| --- | --- |
| at least 2 SUPPORTS and no CONTRADICTS | `TRUE` |
| at least 2 CONTRADICTS and no SUPPORTS | `FALSE` |
| SUPPORTS and CONTRADICTS both present | `DISPUTED` |
| anything else | `UNVERIFIED` |

## Consensus design

| Step | Evidence | Pattern | Validators agree on | Allowed to differ |
| --- | --- | --- | --- | --- |
| `submit_claim`, `refresh` | every source via `gl.nondet.web.render(mode="text")` | `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` | the **final verdict** from `aggregate()` | per-source wording, the note |
| `challenge` | the new source only | same | the final verdict computed from the stored stances plus the new one | same |

- **Equivalence on the decision, not the wording.** Each validator re-reads the sources and computes its own stances, then accepts the leader's result only if both lead to the same verdict. Two honest models that phrase one source differently still agree; a leader that hides a contradicting source to turn `DISPUTED` into `TRUE` is rejected (see `test_leader_hiding_a_contradiction_is_rejected`).
- **A source that fails to load counts as UNCLEAR** instead of failing the whole check, so one slow site cannot block a claim. If no source loads, the error is `[TRANSIENT]` and validators agree on it.
- Errors are classified `[EXPECTED]`, `[TRANSIENT]`, `[LLM_ERROR]`; LLM errors always disagree, forcing leader rotation.
- Source text is wrapped in markers and the prompt treats it as data only (prompt-injection hardening). Stances are normalised against a fixed set.
- Flat `TreeMap` storage, no dataclass storage objects, no `web.get`, no `strict_eq`.

## Incentives and freshness

- **Challenge** (`challenge`, payable, 0.1 GEN bond): add one new source on a new domain. If the verdict changes, the bond is refunded to the challenger. If it does not, the bond goes to the claim's author, which rewards submitters who cite good sources and makes spam challenges costly.
- **Refresh** (`refresh`, free): facts change. After 6 hours anyone can re-check every source.
- **Staleness for consumers**: `get_verdict(id, max_age)` returns `STALE` when the last check is older than `max_age` seconds, so a consumer decides how fresh evidence must be.

## API

| Method | Type | Notes |
| --- | --- | --- |
| `submit_claim(claim, sources)` | write | claim 12 to 280 characters; 2 or 3 sources, one per line or comma separated; returns id, verdict, stances |
| `challenge(claim_id, new_source)` | write, payable | 0.1 GEN bond; up to 6 sources per claim |
| `refresh(claim_id)` | write | allowed 6 hours after the last check |
| `get_verdict(claim_id, max_age)` | view | `TRUE`, `FALSE`, `DISPUTED`, `UNVERIFIED` or `STALE`; `max_age = 0` means any age |
| `get_claim(claim_id)` | view | JSON with sources, stances, note, checked_at, author, challenges, flips |
| `get_claims(offset, limit)` | view | JSON page, up to 50 |
| `get_claim_count()` | view | int |

## Using it from another Intelligent Contract

```python
CORROBORATE = Address("0x...")   # deployed Corroborate address

verdict = gl.get_contract_at(CORROBORATE).view().get_verdict(claim_id, 86400)
if verdict == "TRUE":
    ...  # settle the market, release the grant, unlock the reward
elif verdict in ("STALE", "UNVERIFIED", "DISPUTED"):
    ...  # wait, refresh, or route to a human
```

From the CLI:

```
genlayer write <address> submit_claim --args "Abuja is the capital of Nigeria" "https://site-one/...,https://site-two/..."
genlayer call  <address> get_verdict --args 0 0
```

## Console (frontend)

`frontend/index.html` is a single static page (genlayer-js from a CDN, OKX or MetaMask on Bradbury). It lists every claim with its verdict and per-source stances, submits new claims, posts challenges with the 0.1 GEN bond, and calls `refresh`. Its transaction tracker counts a call as successful only when it is ACCEPTED and GenVM returned `FINISHED_WITH_RETURN`, then keeps watching until FINALIZED.

Live: https://kingkanzi0.github.io/corroborate/frontend/

## Tests

```
python3 tests/test_corroborate.py
```

16 offline tests cover: the aggregation table; distinct-domain and input rules; validators accepting different wording with the same verdict; a leader hiding a contradiction being rejected; unloadable sources counting as UNCLEAR; transient errors; wrong stance counts; challenges that refund or pay the author; challenge limits; refresh timing; `STALE` reporting; render settings. `tests/mock_genlayer.py` is a minimal SDK stand-in, so behaviour must also be confirmed on Bradbury.

## Limits

- Sources on different domains are not guaranteed to be independent (one site may copy another). Consumers that need more can require claims with three sources or a `DISPUTED`-free history.
- Validators read the first 4,000 characters of each page; cite pages where the fact appears early. Light pages (plain text, docs files) are faster and less likely to time out.

## Live proof (Bradbury)

The challenge demo uses `demo/old-staking-note.txt`, a deliberately outdated test fixture in this repo (served by GitHub Pages). A claim backed by two copies of the official staking docs is TRUE; a challenge that adds the outdated note as a new source contradicts it, the verdict becomes DISPUTED, and the bond is refunded to the challenger.

| Proof | Transaction |
| --- | --- |
| Claim checked against 2 sources | _fill in_ |
| Challenge changed the verdict, 0.1 GEN refunded | _fill in_ |

## Deployment record

| Item | Value |
| --- | --- |
| Network | Testnet Bradbury (chain 4221) |
| Contract | `0x94DE6E49047518008Fb97Ec6D01a49782b4a8C3E` |
| Deploy tx | `0xec2bdd0f7010dd9f5775ed97ca4221a409f6338ef819aa10f4a15a89058236e5` |
| Deploy result | ACCEPTED, validators AGREE, FINISHED_WITH_RETURN (13 Oct 2026) |
| Explorer | https://explorer-bradbury.genlayer.com/address/0x94DE6E49047518008Fb97Ec6D01a49782b4a8C3E |
