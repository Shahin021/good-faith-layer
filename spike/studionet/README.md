# GFL Studionet wallet kit (chain 61999): Phase 2

**Status:** built and tested offline only. Nothing has been deployed or
submitted. The only live calls this kit has made are the ones you ran.

It is the external-wallet sandbox for Studionet:

- Operators run setup, attest and flag from a script.
- Your MetaMask signs `accept_payment` and `open_claim` in a loopback page.
- The contract is a separate deployment of the **reviewed Studionet port**
  of GFL.

Everything is in `spike/studionet/`, plus the gate evidence in
`spike/studionet-gate/`. Unchanged:

- `contracts/good_faith_layer.py` (sha256 `2e93a95d…54d2`);
- the six proof deployments;
- the 61997 kit and its recovered case (`spike/run/`);
- the verifier, frontend, policies and scenarios.

The kit has its own run directory, `spike/studionet/run/`, and never reads or
writes `spike/run/`.

## What is established, and what is not

| | status |
|---|---|
| Port: three-hunk diff (`contract/port.diff`) | runner `Depends` line; drop `import genlayer as gl` and `from genlayer.storage import allow`; `gl.contract.Contract` → `gl.Contract` |
| Port hash | `b6d7bb600370c8b8ab91bc4339206283ffb46e0006cd92a96ae0942ad9631d79` |
| Repo's 10 deterministic check scripts against the port | pass, byte-identical output to the original (test) |
| Port on the real GenVM v0.2.16 Python std (stubbed host) | loads, schema 25 methods, 37/37 lifecycle checks (test, needs a genvm checkout) |
| Live schema (`gen_getContractSchemaForCode`) | 25 methods, constructor `flag_authority: string`. *Reported by your Codex run.* `operator_studionet.py schema` re-checks it read-only |
| NORMAL, FINALIZED Studionet calls pass every binding and consensus check | three transactions. *Reported by your Codex run* |
| Those transactions were signed with MetaMask | **not established.** They have the genlayer-js envelope shape; the signer's software is unknown |
| Real MetaMask signing on Studionet, incl. its handling of gas price 0 | **untested.** Live test |
| The complete GFL flow on Studionet (deploy → claim → verify) | **untested.** Live test |
| GenVM's real LLM execution of `open_claim` on Studionet | **untested.** Live test |

## Verification requirements: all kept

`studionet_checks.py` is the authoritative verifier. It reuses the neutral
helpers of `spike/spike_checks.py` (unchanged) and defines everything
deployment-specific.

| requirement | Studionet rule |
|---|---|
| exact call binding | keccak(signed envelope) = tx hash. Signer = GenLayer sender = record sender. Recipient = case contract. Calldata key `method` with exactly `{method, args}`; `""`, `kwargs` and other keys refused. Method and args equal the case. Record calldata **must** be present and byte-identical to the envelope |
| chain | EIP-155 chain id 61999 in the signed envelope. The server does not check it; this check is the only chain binding |
| consensus target | envelope `to` = `0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575` (mandatory) |
| encoding | v5 `addTransaction(address,address,uint256,uint256,bytes)` only |
| zero value | EVM value 0 **and** GenLayer value 0 (v5 carries value only in the EVM envelope) |
| NORMAL mode | inner flag empty or `0x00`; record `leader_only` false, `execution_mode` NORMAL |
| majority | strict AGREE majority over max(cast votes, electorate); electorate required (3 AGREE + 2 IDLE of 5 passes; 2 of 5 fails) |
| execution success | derived from the leader receipt with Studio rc.7's rule (Studionet records have no `txExecutionResultName`); a stated value must agree |
| finality | setup steps: decided. Accept and claim: **FINALIZED** at verify time (accept is stricter than in the 61997 kit) |
| last round | `Accepted` |
| source identity | signed deploy code, record code (if present) and `gen_getContractCode` all hash to the port. The constructor calldata is exactly `{args: [flag authority]}`. The live schema matches `contract/schema.expected.json` before deploying |
| live-state verification | only a complete set of successful contract reads that match the case sets `live_state_verified`; wallet writes and operator writes stay disabled until then; verify fails if the final reads fail |
| simulation overrides | any `sim_config` key other than the signed envelope is refused; the kit never sends `sim_config` |

## Three deliberate departures from SDK defaults

1. **Operator client config (genlayer-py 0.18.0).** Out of the box, 0.18.0
   cannot produce a transaction this verifier accepts (a test shows both
   cases):
   - Its static studionet config carries a **6-input** `addTransaction`.
     Studio v0.121's parser only decodes v5 and would treat it as a plain
     transfer.
   - Its runtime lookup replaces the address with Studionet's hosted **zero
     address**.

   The kit therefore uses the 0.18.0 client with the consensus contract
   pinned to genlayer-js 1.1.8's address and v5 ABI
   (`consensus_main_v5_abi.json`). It also disables the runtime lookup. Every
   signed transaction is checked against these rules before it is sent.
2. **No `connect()` from the JS SDK.** genlayer-js 1.1.8's `connect()`
   installs the GenLayer MetaMask Snap. The page adds or switches the network
   itself with `wallet_addEthereumChain` and `wallet_switchEthereumChain`, and
   needs no Snap.
3. **At most one wallet prompt per action.** After some errors, genlayer-js
   1.1.8 re-sends a write with another encoding. The page's guard refuses
   that second request, and so does the operator's transport boundary.

## Files

| path | what |
|---|---|
| `contract/good_faith_layer.studionet.py` | the port (hash above) |
| `contract/port.diff` | exact diff from `contracts/good_faith_layer.py` |
| `contract/schema.expected.json` | schema generated offline by GenVM v0.2.16's std from the port |
| `studionet_checks.py` | verifier profile |
| `operator_studionet.py` | operator CLI: `schema`, `preflight`, `setup`, `check-state`, `attest`, `flag`, `verify`, `recover` |
| `consensus_main_v5_abi.json` | v5 `addTransaction` + `NewTransaction` (from genlayer-js 1.1.8) + `CreatedTransaction` (from genlayer-py 0.18.0) |
| `requirements-studionet.txt` | fully pinned Python 3.12 environment (genlayer-py 0.18.0) |
| `serve_studionet.py` | loopback server, 7 allowlisted files, port 8766 |
| `public/wallet.html`, `sn-core.js`, `sn-flow.js`, `sn-page.js` | the page |
| `public/vendor/genlayer-js-1.1.8.js` | pinned bundle, sha256 `a7af4598e4aa5ef3108c5721b03860aeef6f1ba77e2ea1a69cb37486d113bef6` |
| `sdk/` | `package.json` + `package-lock.json` (genlayer-js 1.1.8, viem 2.57.5, esbuild 0.25.10) to rebuild the bundle and run JS tests |
| `tests/` | 70 Python tests (+1 that needs a genvm checkout) and 51 JS tests |

### Operator transport boundary

Every request from genlayer-py goes through one `Transport` object:

- It refuses any JSON-RPC method not on a read allowlist, and any `sim_config`.
- It refuses `eth_sendRawTransaction` unless one write is armed.
- Before a raw transaction leaves the machine, it decodes it and applies the
  verifier's rules: chain, target, v5, signer, zero value, NORMAL, exact
  method and args, or the exact port code and constructor.
- It writes keccak(raw) to `run/progress.json` **before** sending.
- It sends once and refuses a second send for the same step.
- If the RPC returns a hash other than keccak(raw), it stops.

Nothing is retried. Faucet, admin, validator and snapshot methods are not
reachable. Gas price must be `0x0`, or `preflight` and `setup` stop.

**Latency and TTL.** Each step's submission-to-decided time runs from the
transport's own timestamp to the moment `wait_record` sees a decided record.
The timestamp is taken immediately before `eth_sendRawTransaction` leaves the
machine. So it includes the time the SDK spends waiting for the EVM receipt
inside `write_contract`/`deploy_contract`, and excludes pre-send work. The TTL
uses these values. The full SDK call time is logged separately as
`sdk_call_seconds`.

**Line endings.** Every source-identity check, for the original and the port,
compares bytes after CRLF→LF only. A CRLF Windows checkout of the unchanged
files passes; any other difference is refused, including a BOM, a lone CR or
an extra newline. The whole Python suite also passes on a CRLF copy of the
repository.

## Windows instructions

Use PowerShell from the repository root. You need Python 3.12 (the `py`
launcher) and Node.js 20 or newer. The commands do not touch
`GFL-Wallet-Spike-Env`.

### 1. Create the separate environment (once)

```powershell
py -3.12 -m venv ..\GFL-Studionet-Env
$py = (Resolve-Path ..\GFL-Studionet-Env\Scripts\python.exe).Path
& $py -m pip install --upgrade pip
& $py -m pip install -r spike\studionet\requirements-studionet.txt
& $py -c "import importlib.metadata as m; print(m.version('genlayer-py'))"     # must print 0.18.0
```

In every new PowerShell window, set `$py` again (the second line). Do not
activate or modify `GFL-Wallet-Spike-Env`.

### 2. Offline tests (no network, no keys)

```powershell
& $py -m pytest spike\studionet\tests -q
& $py -m pytest spike\studionet-gate\test_probe_studionet.py spike\studionet-gate\test_verifier_on_sdk_envelopes.py -q

Push-Location spike\studionet\sdk; npm ci; Pop-Location
node --test spike/studionet/tests/sn-core.test.mjs spike/studionet/tests/sn-flow.test.mjs spike/studionet/tests/sn-page.test.mjs
```

Expected:

- `70 passed, 1 skipped` (the skip is the GenVM test below);
- `33 passed`;
- `# pass 51`, `# fail 0`.

Optional: run the port on the real GenVM v0.2.16 Python std. This is what
removes the skip:

```powershell
git clone --depth 1 --branch v0.2.16 https://github.com/genlayerlabs/genvm ..\genvm-v0.2.16
$env:GFL_GENVM_STD = (Resolve-Path ..\genvm-v0.2.16\runners\genlayer-py-std\src).Path
& $py -m pytest spike\studionet\tests -q -k genvm
& $py spike\studionet-gate\genvm\assess_port.py --std $env:GFL_GENVM_STD
Remove-Item Env:GFL_GENVM_STD
```

Optional: check that the bundle is reproducible. This writes to `%TEMP%` and
leaves the vendored file alone:

```powershell
Push-Location spike\studionet\sdk
npx esbuild entry.js --bundle --format=esm --platform=browser --legal-comments=none --outfile=$env:TEMP\gjs-1.1.8.js
Pop-Location
(Get-FileHash $env:TEMP\gjs-1.1.8.js -Algorithm SHA256).Hash
(Get-FileHash spike\studionet\public\vendor\genlayer-js-1.1.8.js -Algorithm SHA256).Hash
```

Both hashes should be `A7AF4598E4AA5EF3108C5721B03860AEEF6F1BA77E2EA1A69CB37486D113BEF6`.

### 3. Read-only live check (allowed now: no keys, no transactions)

```powershell
& $py spike\studionet\operator_studionet.py schema
```

- Exit 0 means Studionet's schema generator matches the reviewed port:
  - the same 25 methods;
  - the same parameters, `readonly` and `payable`;
  - constructor `flag_authority: string`.
- The result is written to `spike\studionet\run\schema.json`.
- It sends `eth_chainId` and `gen_getContractSchemaForCode` only.

### 4. Live run: NOT YET, only after your review and explicit go-ahead

This section is for later. Each step stops on any failed check. Exit codes:
0 passed, 1 verification failed, 2 blocked.

**Load the operator keys** into the current window only. The variables are
separate from the 61997 ones, and the keys are never printed or written:

```powershell
function Set-SecretEnv([string]$Name) {
  $s = Read-Host -AsSecureString -Prompt $Name
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { [Environment]::SetEnvironmentVariable($Name, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b), 'Process') }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
Set-SecretEnv GFL_SN_OWNER_KEY; Set-SecretEnv GFL_SN_ATTESTER_KEY; Set-SecretEnv GFL_SN_FLAG_KEY
```

When you are finished:
`Remove-Item Env:GFL_SN_OWNER_KEY, Env:GFL_SN_ATTESTER_KEY, Env:GFL_SN_FLAG_KEY`.

1. **Serve the page** in a second window: `& $py spike\studionet\serve_studionet.py`.
   Open `http://127.0.0.1:8766/wallet.html` and click **Connect wallet**:
   - MetaMask is asked to add or switch to **GenLayer Studionet** (61999,
     `https://studio.genlayer.com/api`).
   - No Snap is requested.
   - Copy the connected address.
2. **Preflight** (read-only):
   `& $py spike\studionet\operator_studionet.py preflight --wallet 0xYOUR_WALLET`.
   It checks:
   - the chain;
   - gas price 0;
   - three distinct operators, none of them the wallet;
   - port identity;
   - the live schema.
3. **Setup** (deploys and sends 4 setup writes):
   `& $py spike\studionet\operator_studionet.py setup --wallet 0xYOUR_WALLET --confirm-allowance 120`.
   - It ends with live reads. Only a full matching read set marks the case
     usable.
   - If reads fail, run
     `& $py spike\studionet\operator_studionet.py check-state --attempts 5`.
   - If setup is interrupted, run `recover --wallet 0xYOUR_WALLET`. It
     rebuilds the case from `run\progress.json` without sending anything.
4. In the page: **Load case** → **Arm accept**. Then run
   `& $py spike\studionet\operator_studionet.py attest` and press Enter when
   the page shows *armed*. Confirm the MetaMask prompt promptly; there is
   exactly one per action.
5. Run
   `& $py spike\studionet\operator_studionet.py flag --accept-hash 0xACCEPT_HASH_FROM_THE_PAGE`.
   It verifies the accept transaction and its read-back before flagging.
6. In the page, click **Arm claim** and confirm the prompt.
7. Run
   `& $py spike\studionet\operator_studionet.py verify --claim-hash 0xCLAIM_HASH_FROM_THE_PAGE`.
   - It waits, bounded, for both wallet transactions to be FINALIZED, then
     re-reads everything.
   - The full report goes to `spike\studionet\run\report.json`.

**Logs to keep for review:**

- `spike\studionet\run\` (`preflight`, `progress`, `operator_log`, `state`,
  `report` JSON);
- the page's **Download log**.

They contain only public addresses, hashes, timings, decoded checks and
read-backs.
