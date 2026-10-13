# Studionet (chain 61999) feasibility gate: Phase 1

**Gate status: PASSED (2026-10-13), on live evidence reported by your
independent Codex run.** This environment still has no live access; the items
below marked *Codex* are your reports, not observations made here.

- *Codex:* three Studionet transactions are NORMAL and FINALIZED:
  `0x0dbcc1e6…37db`, `0x8c0c2c72…05e7`, `0x0f9a62a4…e06f`.
  - `probe_studionet.py`'s binding and consensus checks passed for all three.
  - Each signed envelope targets the SDK consensus address
    `0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575`.
  - Votes: two have 5/5 AGREE; one has 3 AGREE and 2 IDLE, with electorate 5.
- *Codex:* the candidate port (sha256 `b6d7bb60…1d79`) passed
  `gen_getContractSchemaForCode` on live Studionet: 25 methods, constructor
  `flag_authority: string`. No deployment or transaction was submitted.
- **Not established:** that those transactions were signed with MetaMask.
  They have the shape genlayer-js produces; who signed them, and how, is
  unknown. Real MetaMask signing and the complete GFL flow remain live tests.

**Correction after the gate:** the probe now *requires* the envelope target
to be the SDK consensus address. Before, it only recorded the target in
`envelope_target_is_sdk_consensus`, and `fully_verifiable` did not depend on
it. A regression test covers a correctly signed envelope that targets the
zero address or another address. Codex reports that the three transactions
meet this requirement.

The Phase 2 kit is in `spike/studionet/`. `contracts/`, the 61997 recovery
files (`spike/run/`) and every other existing file are unchanged.

Run on 2026-10-13. There was no live RPC access from the investigating
environment (proxy 403 on studio.genlayer.com). Live facts come from your
Codex run. Everything else is from source, offline runs and tests.

## Contents

| path | what |
|---|---|
| `probe_studionet.py` | read-only live probe for items 3 and 4 (allowlist, ≤80 requests, 20 s timeout, 16 MB cap, no retries) |
| `test_probe_studionet.py` | 25 offline tests: safety rails, and every binding and consensus check against real-SDK envelopes |
| `sdk/` | genlayer-js **1.1.8** pinned by `package-lock.json` (viem 2.57.5). `external-wallet.test.mjs` has 8 real-SDK tests and writes `envelopes.json` |
| `test_v0121_parser.py` | 6 tests: Studio v0.121's real transaction parser decoding those envelopes |
| `test_verifier_on_sdk_envelopes.py` | 8 tests: the unchanged `spike_checks.py` on those envelopes, including its two gaps |
| `genvm/assess_port.py`, `genvm/harness_v0216.py` | item 6: original vs candidate on the real GenVM v0.2.16 Python std |

## Run (Windows PowerShell, your existing Python 3.12 venv)

```powershell
.\.venv\Scripts\Activate.ps1
# live, read-only
python spike\studionet-gate\probe_studionet.py
# optional: add NORMAL transactions you know of, or an address to list
python spike\studionet-gate\probe_studionet.py --tx 0x... --address 0x...
```

- Evidence goes to `spike\studionet-gate\run\probe_studionet.json`.
- The probe sends no keys, transactions, funding, admin, validator or
  snapshot calls, and never sends `sim_config`.
- It lists up to `--max-tx` (default 40, max 120) transactions:
  - the latest "block";
  - the seed transaction;
  - transactions of the seed contract (`0xBD64…2A65`), the seed sender, and
    any `--address` you give.

Offline checks (Linux/macOS shown; the Python ones also run in PowerShell):

```sh
cd spike/studionet-gate/sdk && npm ci && node --test        # writes envelopes.json
cd .. && python -m pytest -q test_probe_studionet.py test_verifier_on_sdk_envelopes.py
GFL_STUDIO_V0121=<studio checkout at v0.121> python -m pytest -q test_v0121_parser.py   # web3==7.14.1 eth-account==0.13.7
python genvm/assess_port.py --std <genvm v0.2.16>/runners/genlayer-py-std/src            # Python 3.12+
```

## Item 1: versions

| component | pin | basis |
|---|---|---|
| Studio backend | v0.121 line (`origin/v0.121` 2004fc4 inspected) | live facts match it, and not v0.123: `sim_getFeeConfig` absent; `sim_getConsensusContract` gives a zero address and the 5-input addTransaction. In v0.121 that is the built-in default (`DEFAULT_CONSENSUS_MAIN_ADDRESS` defaults to zero; hosted Studio ships no hardhat artifacts; the endpoint is marked deprecated in favour of the SDK chain config). **Zero is the expected convention, not a fault.** The deployed build is not v0.121 exactly: it stores `sim_config.signed_rollup_transaction`, which the v0.121 source does not |
| GenVM | v0.2.16 | v0.121 `Dockerfile.backend` `ARG GENVM_TAG=v0.2.16`. Offline, the original GFL fails on the v0.2.16 std library with exactly the live error, `No module named 'genlayer.storage'` |
| contract runner | `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` (candidate) | pinned by v0.121's examples. **Unconfirmed live:** the probe prints the seed contract's Depends line to compare |
| JS SDK | genlayer-js **1.1.8** (2026-05-06, last pre-v0.6 release) + viem 2.57.5 | its `studionet` chain is 61999, consensus `0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575`, v5 ABI, calldata key `method` |
| Python SDK | genlayer-py **0.18.0** (inspected only) | same studionet address and `method` key |
| **not usable** | genlayer-py 0.19.0rc2, genlayer-js 2.0.0-rc.1 | they encode the method under the key `""` (v0.6 convention). GenVM v0.2.16 dispatches on `"method"` (`genvm_contracts.py:46`), which explains the failed RC read of `get_price` |

## Item 2: external MetaMask signing (genlayer-js 1.1.8, real SDK, offline)

Verified by tests:

- A `writeContract` from an address-only account sends **exactly one**
  `eth_sendTransaction` to the EIP-1193 provider, and **no Snap method**
  (`wallet_getSnaps`, `wallet_requestSnaps`, `wallet_invokeSnap`).
- It does not broadcast itself or wait for a receipt. With `isStudio` it
  returns the EVM hash; MetaMask broadcasts.
- The wallet is asked to sign:
  - `to` = `0xb7278A61…E575`, `chainId` `0xf22f`, type `0x0`, `value` 0,
    `gasPrice` `0x0`;
  - v5 `addTransaction` (selector `0x27241a99`) with sender = wallet,
    recipient = contract, 5 validators, 3 rotations;
  - inner RLP `[calldata, 0x00]`, with calldata `{method, args}` exactly.
- `leaderOnly:true` shows up as `0x01`.
- **`connect()` requires the GenLayer Snap.** It calls `wallet_getSnaps`,
  then `wallet_requestSnaps`. So the kit must not call `connect()`. Its own
  `wallet_addEthereumChain` / `wallet_switchEthereumChain` path (already in
  `spike-flow.js`) needs no Snap. No Snap requirement is assumed away: the
  write path does not touch one, and the path that does is not used.
- **The SDK skips its chain check on Studio chains** (`assertChainMatch`
  returns early on `isStudio`). The kit's provider guard must keep checking
  `eth_chainId` = `0xf22f` before every send. It does today, for 61997.
- **One `writeContract` can ask for a second signature.** If the first send
  fails with an "ABI mismatch"-like error, the SDK re-sends with the v6
  encoding (`0xe71d5196`). The guarded provider must permit one
  `eth_sendTransaction` per armed action and refuse the second.
- Studio v0.121's real parser decodes these envelopes:
  - signature valid;
  - sender = signer, recipient = contract;
  - `0x00` → `leader_only` False → `NORMAL` (via its custom Boolean sedes);
  - `0x01` → `LEADER_ONLY`.

Not verified (needs a real MetaMask):

- MetaMask's handling of a dapp-supplied `gasPrice` of 0 on a custom
  network.
- MetaMask's own broadcast to `studio.genlayer.com/api`.

The v0.121 source returns synthetic blocks "because MetaMask needs valid
blocks", which suggests this path was meant to work. That is a suggestion,
not evidence.

## Item 3: recorded NORMAL transactions. Open; this is the gate

Known:

- The one sampled transaction carries `sim_config.signed_rollup_transaction`
  but is LEADER_ONLY.
- In the inspected v0.121 source:
  - `eth_sendRawTransaction` takes `sim_config` from the **caller**. MetaMask
    sends none.
  - The record's `r/s/v` columns are not populated.
  - Receipts are synthetic (one fabricated `NewTransaction` log, status from
    the DB).
- So **if a MetaMask-originated NORMAL call has no stored envelope, there is
  nothing to bind it to cryptographically.** The hash is keccak of a raw
  transaction we cannot see. That would be a **blocker**.
- The live build evidently stores envelopes somewhere in `sim_config`. Whether
  it does so for every transaction is what the probe measures.

For every listed record, the probe:

- runs every binding check: keccak = hash, chain 61999, envelope target =
  the SDK consensus address (mandatory), v5, signer = record
  sender = GenLayer sender, recipient, EVM value and user value 0, calldata
  key `method` (and no `""`), record calldata = envelope calldata, no unknown
  or override `sim_config` keys;
- runs every consensus check: FINALIZED, NORMAL, votes present, electorate
  present, strict majority AGREE over max(votes, electorate), leader
  receipt → `FINISHED_WITH_RETURN`, last round `Accepted`;
- prints `ITEM 3 EVIDENCE PRESENT`, `ITEM 3 BLOCKER` or `ITEM 3 NOT SETTLED`.

The gate passes only on `EVIDENCE PRESENT`. A call can only reach it if its
envelope targets `0xb7278A61…`, which is the SDK-shaped target. That shape does
not show which wallet signed the call.

## Item 4: funding and fees (source; probe confirms `eth_gasPrice`)

- v0.121:
  - `eth_gasPrice` = `0x0`;
  - `eth_estimateGas` = fixed `0x7a120`;
  - no fee config and no fee fields;
  - a value transfer is minted on demand (shortfall credited, then debited).
- GFL has no payable method. Funding is accounting arguments
  (`fund_protection_pool(amount)` etc.), so every GFL call has value 0.
- **No funding call is needed** for the wallet or the operators, if live
  `eth_gasPrice` is `0x0`. None of the v0.6 fee estimation and `fees` presets
  in the rc tests apply.

## Item 5: can the verification requirements be kept? Yes, conditional on item 3

The Studionet profile only changes constants or adds checks. It drops
nothing.

| requirement | on Studionet |
|---|---|
| keccak(envelope) = tx hash | unchanged (v0.121 `generate_transaction_hash` is keccak of the raw transaction) |
| EIP-155 chain | 61999. The **server does not check chain**: a 61997-signed envelope decodes and verifies there (test). Our check is the only chain binding, and stays |
| consensus target | pinned to the SDK address `0xb7278A61…`. The server ignores `to`, so this binds the signer's intent, not server routing. Still part of the signed bytes |
| signer = GenLayer sender = record sender, recipient, NORMAL inner mode | unchanged; decoder already handles v5 and `0x00` |
| value 0 | **gap 1, to close:** v5 has no GenLayer value field and v0.121 uses the EVM value. Today's verifier checks only the GenLayer value. The profile must also require EVM value 0 (a stricter check) |
| exact calldata | **gap 2, to close:** key `method` instead of `""`; reject `""` and unknown keys |
| outcome FINISHED_WITH_RETURN | v0.121 records have no `txExecutionResultName`. Derive it with Studio rc.7's own rule from the leader receipt. That rule is the same as the existing leader SUCCESS check plus a timeout guard |
| quorum | keep our rule (strict AGREE majority over max(votes, electorate)). It is stricter than v0.121's own rule (majority of cast votes; idle counts as disagree) |
| sim_config | unknown keys and overrides stay rejected |
| current state | `gen_call` with `{"method": …}` works on Studionet (your `get_price` read). `live_state_verified` still needs real reads; nothing substitutes for them |

## Item 6: contract changes (offline, real GenVM v0.2.16 std, stubbed host)

| GFL usage | v0.2.16 | change |
|---|---|---|
| `from genlayer.storage import allow as allow_storage` | no such module (`genlayer.py.storage`); `allow_storage` is exported by `from genlayer import *` | delete the line |
| `import genlayer as gl` | `from genlayer import *` rebinds `gl` to `genlayer.gl` anyway | delete (redundant) |
| `gl.contract.Contract` | `gl.Contract` | rename |
| Depends hash | v0.6-line runner | runner hash (to confirm live) |
| `gl.Address`, `gl.storage.TreeMap`, `gl.u256`, `@allow_storage @dataclass` | all present | none |
| `gl.public.write` / `view`, `gl.message.sender_address` | present | none |
| `gl.nondet.exec_prompt(prompt, response_format="json")` | present, returns a dict | none |
| `gl.vm.run_nondet(leader, validator)`, `gl.vm.Return(...).calldata` | present, eager; validator receives `Return`/`VMError`/`UserError` | none |

Candidate: three hunks; sha256
`b6d7bb600370c8b8ab91bc4339206283ffb46e0006cd92a96ae0942ad9631d79`
(original `2e93a95d…54d2`).

| check | original | candidate |
|---|---|---|
| load on v0.2.16 std | `ModuleNotFoundError: No module named 'genlayer.storage'` (= live) | loads |
| `get_schema` | — | 25 methods, 10 writes, ctor `(flag_authority: string)` |
| in-memory lifecycle | — | 37/37 |
| repo's 10 deterministic check scripts (run in a temp copy of the repo whose `contracts/good_faith_layer.py` is the candidate) | all pass | all pass, byte-identical output |

The in-memory lifecycle runs on the std library's storage manager:

- access control on every role;
- immutable policy;
- attest before acceptance;
- freshness;
- flag gate;
- scenario 01 PROTECTED with 1000 paid from the pool;
- scenario 05 REVIEW_REQUIRED with nothing paid.

`open_claim`'s `run_nondet` and `exec_prompt` were replaced by a local call
with a canned model answer, so this exercises the deterministic code around
the LLM step, not the LLM step or consensus. Not shown offline: WASM
execution, runner-hash resolution, the live LLM's JSON mode, and validator
agreement.

## What would make the gate pass

1. The probe prints `ITEM 3 EVIDENCE PRESENT` for at least one FINALIZED NORMAL
   call, preferably one whose envelope targets `0xb7278A61…`.
2. The probe shows `eth_gasPrice` `0x0`.
3. The probe shows a seed `Depends` line that matches the candidate hash, or
   the port takes the live one.

If item 3 shows NORMAL calls **without** envelopes, the gate fails. The
remedy is not a weaker check. It would need GenLayer to confirm that
envelopes are recorded for externally signed transactions, or a source of
the raw signed bytes that we can verify.
