// The Studionet wallet page's behaviour: connect, guarded writes, arm/resume flows.
// wallet.html only wires DOM events to this module; the tests drive this same
// module with the real vendored genlayer-js 1.1.8 bundle and a fake wallet.
// No Snap: the SDK's connect() is never called (it installs the GenLayer Snap);
// the page adds/switches the network itself and the SDK only sees the guard below.

import * as core from "./sn-core.js";

const lower = (x) => String(x ?? "").toLowerCase();

// ------------------------------------------------------------------------
// Guard at the wallet boundary. The SDK reaches the wallet only through this
// object. eth_sendTransaction is forwarded only while a write is authorized,
// the action is live, the wallet is on the right chain and account, the
// transaction the SDK built is exactly the expected call, and nothing has been
// forwarded yet for this authorization. genlayer-js 1.1.8 re-sends with a
// different encoding after some errors; that second request is refused here.
// ------------------------------------------------------------------------
export function createGuardedProvider({ provider, session, sdk, onHash, log = () => {} }) {
  let slot = null;

  const live = (s) => {
    if (slot !== s) throw new core.Invalidated("write authorization was revoked");
    core.ensureLive(session, s.act);
  };

  async function guardSend(args) {
    const s = slot;
    if (!s) throw new core.Invalidated("no authorized write in progress; transaction not sent");
    if (s.forwarded) throw new core.EnvelopeRejected("a transaction request was already forwarded to the wallet for this action; a second one is refused");
    live(s);
    const chain = parseInt(await provider.request({ method: "eth_chainId" }), 16);
    live(s);
    if (chain !== core.CHAIN_ID) throw new core.Invalidated(`wallet is on chain ${chain}, expected ${core.CHAIN_ID}`);
    const accounts = await provider.request({ method: "eth_accounts" });
    live(s);
    const exposed = lower(Array.isArray(accounts) ? accounts[0] : null);
    if (!exposed || exposed !== lower(session.account) || exposed !== lower(s.wallet))
      throw new core.Invalidated("wallet account is not the connected case recipient");

    const tx = args?.params?.[0];
    if (!tx || typeof tx !== "object") throw new core.EnvelopeRejected("missing transaction parameters");
    if (lower(tx.from) !== exposed) throw new core.EnvelopeRejected(`from ${tx.from} is not ${exposed}`);
    if (lower(tx.to) !== lower(core.CONSENSUS_ADDRESS)) throw new core.EnvelopeRejected(`to ${tx.to} is not the consensus contract ${core.CONSENSUS_ADDRESS}`);
    if (tx.chainId === undefined || parseInt(tx.chainId, 16) !== core.CHAIN_ID)
      throw new core.EnvelopeRejected(`chainId ${tx.chainId} is not ${core.CHAIN_ID}`);
    if (BigInt(tx.value ?? "0x0") !== 0n) throw new core.EnvelopeRejected(`value ${tx.value} is not zero`);
    const at = core.decodeAddTransaction(tx.data);
    if (at.glSender !== exposed) throw new core.EnvelopeRejected(`GenLayer sender ${at.glSender} is not ${exposed}`);
    if (at.glRecipient !== lower(s.contract)) throw new core.EnvelopeRejected(`GenLayer recipient ${at.glRecipient} is not the case contract`);
    const call = core.decodeInnerCall(sdk, at.txCalldata);
    if (call.method !== s.method || !core.sameArgs(call.args, s.args))
      throw new core.EnvelopeRejected(`call ${call.method}(${call.args.length} args) is not the expected ${s.method}`);
    live(s);                                             // last check before the wallet prompt
    s.forwarded = true;
    log("wallet_prompt", { method: s.method });
    const hash = await provider.request(args);           // cannot be recalled once the prompt is open
    if (typeof hash === "string") { s.hash = hash; onHash(hash, s); }   // kept even if cancelled meanwhile
    return hash;
  }

  return {
    authorize(s) { if (slot) throw new Error("another write is already authorized"); slot = s; },
    revoke(s) { if (!s || slot === s) slot = null; },
    async request(args) {
      const m = args?.method;
      if (m === "eth_sendTransaction") {
        const s = slot;
        try { return await guardSend(args); }
        catch (e) { if (s) { if (!s.hash) s.rejection = e; else s.lateRejection = e; } throw e; }   // SDK rewraps errors; keep the real reason
      }
      if (m === "eth_accounts" || m === "eth_chainId") return provider.request(args);
      throw new core.Invalidated(`wallet method ${m} is not permitted by this page`);
    },
  };
}

// ------------------------------------------------------------------------
// Pending hashes: bound to chain + contract + payment + method + args + wallet
// ------------------------------------------------------------------------
export function bindingFor(state, method, args, wallet) {
  return { chainId: core.CHAIN_ID, contract: lower(state.contract), paymentId: state.payment_id,
           policyId: state.policy_id, method, args, wallet: lower(wallet) };
}
const bindingKey = (b) => `gfl-sn:${b.chainId}|${b.contract}|${b.paymentId}|${b.method}|${b.wallet}`;

// ------------------------------------------------------------------------
export function createFlow(deps) {
  const {
    provider, sdk, chain, rpc, readContract, loadAssertions,
    ui = { stage: () => {}, log: () => {} }, storage = null,
    timing = {}, sleep = core.defaultSleep, now = Date.now,
  } = deps;
  if (lower(chain?.consensusMainContract?.address) !== lower(core.CONSENSUS_ADDRESS) || chain?.id !== core.CHAIN_ID)
    throw new Error("chain config is not the pinned genlayer-js 1.1.8 studionet config");
  const T = { pollMs: core.POLL_MS, waitAttestMs: 20 * 60e3, waitFlagMs: 30 * 60e3, waitDecidedMs: 20 * 60e3,
              readDelaysMs: [600, 1500], ...timing };
  const session = core.createSession();
  const memory = new Map();
  let state = null, writeClient = null, guard = null;

  const read = (fn, args, signal) => core.withReadRetry(() => readContract(state, fn, args),
    { signal, sleep, delaysMs: T.readDelaysMs });

  // ---- pending -----------------------------------------------------------
  function savePending(binding, hash) {
    const rec = { ...binding, hash, savedAt: new Date(now()).toISOString() };
    memory.set(bindingKey(binding), rec);
    try { storage?.setItem(bindingKey(binding), JSON.stringify(rec)); } catch { /* storage optional */ }
    ui.log("pending_saved", rec);
  }
  function pendingFor(method, args) {
    if (!state || !session.account) return null;
    const want = bindingFor(state, method, args, session.account);
    let rec = memory.get(bindingKey(want));
    if (!rec) { try { const s = storage?.getItem(bindingKey(want)); rec = s ? JSON.parse(s) : null; } catch { rec = null; } }
    if (!rec) return null;
    const same = rec.chainId === want.chainId && rec.contract === want.contract && rec.paymentId === want.paymentId &&
      rec.policyId === want.policyId && rec.method === want.method && rec.wallet === want.wallet &&
      core.sameArgs(rec.args, want.args) && /^0x[0-9a-f]{64}$/.test(lower(rec.hash));
    if (!same) { ui.log("pending_ignored", { reason: "binding mismatch", stored: rec, want }); return null; }
    return rec.hash;
  }

  // ---- wallet ------------------------------------------------------------
  function onWalletChange(reason) {
    core.invalidate(session, reason);
    writeClient = null; guard = null;
    ui.log("invalidated", { reason });
  }
  if (provider?.on) {
    provider.on("accountsChanged", (a) => onWalletChange(`accounts changed to ${JSON.stringify(a)}`));
    provider.on("chainChanged", (c) => onWalletChange(`chain changed to ${parseInt(c, 16)}`));
  }

  // silent: restore an existing authorization after reload via eth_accounts.
  // It never opens a wallet prompt and never switches networks; returns null
  // when there is nothing to restore.
  async function connect({ addChain, silent = false } = {}) {
    let addr;
    if (silent) {
      const accounts = await provider.request({ method: "eth_accounts" });
      if (!Array.isArray(accounts) || !accounts.length) { ui.log("silent_restore_skipped", { reason: "no authorized account" }); return null; }
      const c = parseInt(await provider.request({ method: "eth_chainId" }), 16);
      if (c !== core.CHAIN_ID) { ui.log("silent_restore_skipped", { reason: `wallet is on chain ${c}` }); return null; }
      addr = accounts[0];
    } else {
      [addr] = await provider.request({ method: "eth_requestAccounts" });
      if (addChain) await addChain();
      const c = parseInt(await provider.request({ method: "eth_chainId" }), 16);
      if (c !== core.CHAIN_ID) throw new Error(`wallet is on chain ${c}`);
    }
    if (!/^0x[0-9a-fA-F]{40}$/.test(String(addr))) throw new Error("wallet returned no valid account");
    session.account = addr;
    guard = createGuardedProvider({ provider, session, sdk,
      log: ui.log, onHash: (hash, s) => savePending(s.binding, hash) });
    writeClient = sdk.createClient({ chain, account: addr, provider: guard });
    ui.log("wallet_connected", { address: addr, silent });
    return addr;
  }

  // Local disconnect: EIP-1193 has no revoke, so drop our session, cancel pending
  // actions and forget the write client.
  function disconnect(reason = "disconnected by user") {
    onWalletChange(reason);
  }

  // ---- one guarded write ---------------------------------------------------
  async function submitWrite(list, method, args, act) {
    const client = writeClient, g = guard;
    if (!client || !g) throw new core.Invalidated("wallet not connected");
    await core.validateWriteContext({ provider, session, generation: act.generation, caseWallet: state.wallet });
    core.ensureLive(session, act);

    const binding = bindingFor(state, method, args, session.account);
    const slot = { act, binding, wallet: state.wallet, contract: state.contract, method, args };
    g.authorize(slot);
    ui.stage(list, "wallet prompt opening — confirm in your wallet");
    let sdkError = null;
    try {
      await client.writeContract({ address: core.toChecksumAddress(state.contract), functionName: method, args, value: 0n,
        leaderOnly: false });
    } catch (e) { sdkError = e; }
    finally { g.revoke(slot); }

    const hash = slot.hash ?? null;
    if (hash && slot.lateRejection) ui.stage(list, `sent once (${hash}); a second send was refused: ${slot.lateRejection.message}`);
    if (!hash && slot.rejection) {
      ui.stage(list, `NOT SUBMITTED: refused at the wallet boundary — ${slot.rejection.message}`, "bad");
      throw slot.rejection;
    }
    if (!hash) {
      const msg = sdkError ? (sdkError.shortMessage || sdkError.message) : "no transaction hash returned";
      ui.stage(list, "NOT SUBMITTED: " + msg, "bad");
      ui.log(method + "_not_submitted", { error: msg });
      throw sdkError || new Error(msg);
    }
    if (sdkError) ui.stage(list, `sent (${hash}); SDK then reported: ${sdkError.shortMessage || sdkError.message}`);
    ui.stage(list, `submitted (not yet executed): ${hash}`);
    ui.log(method + "_submitted", { hash });
    core.ensureLive(session, act);       // if cancelled during the prompt, the hash is kept for resume
    return hash;
  }

  async function followRecord(list, hash, method, args, act) {
    const raw = await core.pollUntil(async () => {
      let r; try { r = await rpc("eth_getTransactionByHash", [hash]); } catch { return null; }
      return core.DECIDED_OK.has(r?.status) || core.TERMINAL_BAD.has(r?.status) ? r : null;
    }, { timeoutMs: T.waitDecidedMs, intervalMs: T.pollMs, signal: act.signal, sleep, now });
    const s = core.summarizeRecord(raw, { hash, contract: state.contract, sender: state.wallet, method, args, sdk });
    ui.log(method + "_record", s);
    ui.stage(list, `consensus: ${s.status}, ${s.outcome}, votes ${JSON.stringify(s.votes)}`, s.outcome === "MAJORITY_AGREE" ? "ok" : "bad");
    ui.stage(list, `GenVM: ${s.execution}; mode ${s.mode}`, s.execution === "FINISHED_WITH_RETURN" && s.mode === "NORMAL" ? "ok" : "bad");
    if (s.problems.length) ui.stage(list, "problems: " + s.problems.join("; "), "bad");
    return s;
  }

  const running = new Map();
  async function runAction(list, body) {
    if (!state) throw new Error("load the case first");
    if (state.live_state_verified !== true) {
      ui.stage(list, "contract state for this case has not been verified by a live read " +
        "(run operator_studionet.py check-state); wallet writes are disabled", "bad");
      return { refused: "live_state_unverified" };
    }
    if (running.has(list)) throw new Error(`${list} is already running`);
    const act = core.newAction(session);
    running.set(list, act);
    try { return await body(act); }
    catch (e) {
      ui.stage(list, `${e.name || "Error"}: ${e.message}`, "bad");
      ui.log("action_stopped", { list, kind: e.name, error: e.message });
      return { stopped: e.name || "Error", error: e.message };
    } finally { act.done(); running.delete(list); }
  }

  // ---- accept --------------------------------------------------------------
  async function armAccept(list = "accept") {
    return runAction(list, async (act) => {
      const assertions = await loadAssertions();
      core.ensureLive(session, act);
      const args = [state.payment_id, state.policy_id, assertions];
      let hash = pendingFor("accept_payment", args);
      if (hash) {
        ui.stage(list, `resuming the accept_payment already sent from this wallet for this case: ${hash}`);
      } else {
        ui.stage(list, "armed — waiting for the attestation to become readable");
        const ready = await core.pollUntil(async () => {
          const st = await read("get_status", [state.payment_id], act.signal);
          if (st !== "REGISTERED") return { refuse: st };
          return (await read("has_attestation", [state.payment_id], act.signal)) === true ? { ok: true } : null;
        }, { timeoutMs: T.waitAttestMs, intervalMs: T.pollMs, signal: act.signal, sleep, now });
        core.ensureLive(session, act);
        if (ready.refuse) {
          ui.stage(list, `payment is ${ready.refuse} and this page has no accept transaction recorded for this case and wallet; not sending`, "bad");
          return { refused: ready.refuse };
        }
        hash = await submitWrite(list, "accept_payment", args, act);
      }
      const summary = await followRecord(list, hash, "accept_payment", args, act);
      const status = await read("get_status", [state.payment_id], act.signal);
      const fresh = await read("is_attestation_fresh", [state.payment_id], act.signal);
      ui.log("accept_readback", { status, attestation_fresh: fresh });
      ui.stage(list, `persisted: status ${status}, attestation fresh ${fresh}`, status === "ACCEPTED" && fresh === true ? "ok" : "bad");
      ui.stage(list, `next: python spike/studionet/operator_studionet.py flag --accept-hash ${hash}`);
      return { hash, summary, status, fresh };
    });
  }

  // ---- claim ---------------------------------------------------------------
  async function armClaim(list = "claim") {
    return runAction(list, async (act) => {
      const args = [state.payment_id];
      let hash = pendingFor("open_claim", args);
      if (hash) {
        ui.stage(list, `resuming the open_claim already sent from this wallet for this case: ${hash}`);
      } else {
        ui.stage(list, "armed — waiting for FLAGGED");
        const ready = await core.pollUntil(async () => {
          const st = await read("get_status", [state.payment_id], act.signal);
          if (st === "FLAGGED") return { ok: true };
          return st === "ACCEPTED" ? null : { refuse: st };
        }, { timeoutMs: T.waitFlagMs, intervalMs: T.pollMs, signal: act.signal, sleep, now });
        core.ensureLive(session, act);
        if (ready.refuse) {
          ui.stage(list, `payment is ${ready.refuse} and this page has no claim transaction recorded for this case and wallet; not sending`, "bad");
          return { refused: ready.refuse };
        }
        hash = await submitWrite(list, "open_claim", args, act);
      }
      const summary = await followRecord(list, hash, "open_claim", args, act);
      const fin = {
        status: await read("get_status", [state.payment_id], act.signal),
        verdict: await read("get_verdict", [state.payment_id], act.signal),
        paid_out: await read("get_paid_out", [state.payment_id], act.signal),
        claim_balance: await read("get_claim_balance", [state.wallet], act.signal),
      };
      ui.log("claim_readback", fin);
      ui.stage(list, `persisted: ${fin.status} / ${fin.verdict} / paid ${fin.paid_out} / balance ${fin.claim_balance}`,
        fin.verdict === "PROTECTED" ? "ok" : "bad");
      ui.stage(list, `next: python spike/studionet/operator_studionet.py verify --claim-hash ${hash}`);
      return { hash, summary, fin };
    });
  }

  function cancel(list, reason = "cancelled by user") {
    running.get(list)?.controller.abort(reason);
  }

  return {
    session, connect, disconnect, onWalletChange, armAccept, armClaim, cancel, pendingFor,
    setCase(s) { state = s; ui.log("case_loaded", { contract: s.contract, payment_id: s.payment_id, wallet: s.wallet }); },
    get connected() { return !!writeClient; },
    get account() { return session.account; },
  };
}
