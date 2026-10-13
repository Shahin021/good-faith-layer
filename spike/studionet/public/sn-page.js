// All wallet-page behaviour that touches the DOM. wallet.html only calls
// mountPage(); tests mount the same function with a fake document, wallet,
// fetch and storage, and click the same handlers.
import * as core from "./sn-core.js";
import { createFlow } from "./sn-flow.js";

export const RPC_URL = "https://studio.genlayer.com/api";
export const EXPLORER = "https://genlayer-explorer.vercel.app";      // genlayer-js 1.1.8 studionet blockExplorers
export const PREF_DISCONNECTED = "gfl-sn:explicitly-disconnected";

const J = (v) => JSON.stringify(v, (k, x) => (typeof x === "bigint" ? x.toString() : x), 2);
const short = (a) => `${String(a).slice(0, 6)}…${String(a).slice(-4)}`;
const safe = (fn, fallback = null) => { try { return fn(); } catch { return fallback; } };

export function mountPage({
  doc, provider, loadSdk, fetchImpl = (...a) => globalThis.fetch(...a),
  prefs = null,                 // localStorage-like: explicit-disconnect preference
  pendingStore = null,          // sessionStorage-like: pending transaction hashes
  rpcUrl = RPC_URL, timeoutMs = core.RPC_TIMEOUT_MS, timers = {}, flowTiming = {}, clock = () => new Date(),
  clipboard = globalThis.navigator?.clipboard ?? null,
}) {
  const $ = (id) => doc.getElementById(id);
  // Presentation-only elements (status badges, hints) may be absent; never required.
  const $opt = (id) => { try { return doc.getElementById(id) ?? null; } catch { return null; } };
  const LOG = [];
  const ui = {
    log(event, data = {}) { const e = { at: clock().toISOString(), event, ...data }; LOG.push(e); $("log").textContent = J(LOG); },
    stage(list, text, cls = "") { const li = doc.createElement("li"); li.textContent = text; if (cls) li.className = cls; $(list).appendChild(li); },
  };
  const rpc = core.createRpc({ url: rpcUrl, fetchImpl, timeoutMs, timers });
  const status = (id, text, cls = "") => { const el = $(id); el.textContent = text; el.className = cls; };
  let flow = null, readClient = null, state = null, walletNote = null;
  // What the page itself observed for each signed step (display only; verify is authoritative).
  const steps = { accept: { running: false, result: null }, claim: { running: false, result: null } };

  // ---- static files (served by spike/serve.py) ----------------------------
  async function fetchLocal(path) {
    const r = await core.withTimeout(fetchImpl(path, { cache: "no-store" }), timeoutMs, path, timers);
    if (!r || !r.ok) throw new Error(`${path}: HTTP ${r?.status ?? "no response"}`);
    return r;
  }

  // ---- view state -------------------------------------------------------------
  // Derived only from the real page/flow state; nothing here can send anything.
  function outcome(list) {
    const { running, result: r } = steps[list];
    if (running) return { key: "running", label: "In progress" };
    if (!r) return null;
    if (r.refused) return { key: "warn", label: r.refused === "live_state_unverified" ? "Blocked" : "Not sent" };
    if (r.stopped) return { key: "warn", label: "Stopped" };
    const clean = r.hash && Array.isArray(r.summary?.problems) && r.summary.problems.length === 0;
    if (list === "accept" && clean && r.status === "ACCEPTED" && r.fresh === true) return { key: "done", label: "Accepted" };
    if (list === "claim" && clean && r.fin?.verdict === "PROTECTED") return { key: "done", label: "Resolved" };
    if (r.hash) return { key: "warn", label: "Check evidence" };
    return { key: "warn", label: "Stopped" };
  }

  function snapshot() {
    const connected = !!flow?.connected;
    const caseLoaded = !!state;
    const verified = caseLoaded && state.live_state_verified === true;
    const ready = connected && verified;
    const acc = outcome("accept"), clm = outcome("claim");
    const s = {
      connected, account: connected ? core.toChecksumAddress(flow.account) : null, caseLoaded, verified,
      steps: {
        wallet: connected ? { key: "done", label: "Connected" } : { key: "active", label: "Not connected" },
        case: verified ? { key: "done", label: "Verified" }
          : caseLoaded ? { key: "warn", label: "Not verified" }
          : connected ? { key: "active", label: "Ready to load" } : { key: "locked", label: "Waiting" },
        accept: acc ?? (ready ? { key: "active", label: "Ready" } : { key: "locked", label: "Locked" }),
        claim: clm ?? (ready ? { key: "active", label: "Ready" } : { key: "locked", label: "Locked" }),
      },
      hints: {
        accept: steps.accept.running ? "Running. Use Cancel to stop waiting; a transaction already sent is kept."
          : !connected ? "Connect your wallet first." : !caseLoaded ? "Load the case first."
          : !verified ? "The case is not verified by a live read yet." : "",
        claim: steps.claim.running ? "Running. Use Cancel to stop waiting; a transaction already sent is kept."
          : !connected ? "Connect your wallet first." : !caseLoaded ? "Load the case first."
          : !verified ? "The case is not verified by a live read yet."
          : acc?.key !== "done" ? "Usually after Accept and the operator's flag step." : "",
      },
    };
    // Exactly one primary button: the next action.
    s.next = !connected ? "bConnect" : !verified ? "bLoad"
      : acc?.key !== "done" ? "bArmAccept" : clm?.key !== "done" ? "bArmClaim" : null;
    return s;
  }

  function badge(id, st) {
    const el = $opt(id);
    if (!el) return;
    el.textContent = st.label;
    el.className = `badge ${st.key}`;
  }

  const short = (a) => `${a.slice(0, 6)}…${a.slice(-4)}`;

  // ---- buttons --------------------------------------------------------------
  function render() {
    const v = snapshot();
    $("bConnect").textContent = v.connected ? "Disconnect" : "Connect wallet";
    $("bConnect").disabled = false;
    if (v.connected) {
      $("wallet").innerHTML = `connected <code title="${v.account}">${short(v.account)}</code>` +
        `<button type="button" class="copy" data-copy="${v.account}" aria-label="Copy full address ${v.account}">Copy</button>` +
        ` on chain ${core.CHAIN_ID}`;
    } else $("wallet").textContent = walletNote || "not connected";
    const ready = v.connected && v.verified;
    $("bArmAccept").disabled = !ready || steps.accept.running;
    $("bArmClaim").disabled = !ready || steps.claim.running;
    for (const id of ["bConnect", "bLoad", "bArmAccept", "bArmClaim"]) $(id).className = id === v.next ? "primary" : "secondary";
    badge("stWallet", v.steps.wallet); badge("stCase", v.steps.case);
    badge("stAccept", v.steps.accept); badge("stClaim", v.steps.claim);
    for (const [list, id] of [["accept", "hintAccept"], ["claim", "hintClaim"]]) {
      const el = $opt(id);
      if (el) { el.textContent = v.hints[list]; el.hidden = !v.hints[list]; }
    }
    return v;
  }

  // Copy the full address (the short form is display only).
  $("wallet").onclick = async (ev) => {
    const b = ev?.target?.closest ? ev.target.closest("[data-copy]") : null;
    if (!b) return;
    const full = b.dataset?.copy;
    try {
      if (!clipboard?.writeText) throw new Error("clipboard unavailable");
      await clipboard.writeText(full);
      b.textContent = "Copied";
      ui.log("address_copied", { address: full });
    } catch (e) {
      b.textContent = "Copy failed";
      ui.log("address_copy_failed", { error: String(e.message || e) });
    }
  };

  // ---- init: SDK, consensus address, silent restore -------------------------
  async function init() {
    const sdk = await loadSdk();
    const chain = sdk.chains.studionet;
    if (chain.id !== core.CHAIN_ID || chain.consensusMainContract?.address !== core.CONSENSUS_ADDRESS)
      throw new Error("the loaded SDK is not genlayer-js 1.1.8's studionet configuration");
    const rpcChain = parseInt(await core.withReadRetry(() => rpc("eth_chainId", [])), 16);
    if (rpcChain !== core.CHAIN_ID) throw new Error(`RPC reports chain ${rpcChain}, expected ${core.CHAIN_ID}`);
    // Informational only: Studionet reports the zero address here; the pinned SDK address is used.
    let hosted = null;
    try { hosted = (await rpc("sim_getConsensusContract", ["ConsensusMain"]))?.address ?? null; } catch { /* optional */ }
    ui.log("consensus_contract", { pinned: core.CONSENSUS_ADDRESS, hosted_reports: hosted });
    readClient = sdk.createClient({ chain });
    flow = createFlow({
      provider, sdk, chain, ui, storage: pendingStore, rpc, timing: flowTiming,
      readContract: (st, fn, args) => core.withTimeout(
        readClient.readContract({ address: core.toChecksumAddress(st.contract), functionName: fn, args }),
        timeoutMs, `read ${fn}`, timers),
      loadAssertions: async () => (await fetchLocal("./assertions.json")).text(),
    });
    if (provider?.on) {
      const changed = (reason) => {
        $("banner").style.display = "block"; $("bannerText").textContent = reason;
        render();
      };
      provider.on("accountsChanged", (a) => changed(`accounts changed to ${J(a)}`));
      provider.on("chainChanged", (c) => changed(`chain changed to ${parseInt(c, 16)}`));
    }
    if (safe(() => prefs?.getItem(PREF_DISCONNECTED)) === "1") {
      ui.log("silent_restore_skipped", { reason: "explicitly disconnected earlier" });
    } else if (provider) {
      try {
        const addr = await flow.connect({ silent: true });
        if (addr) ui.log("connection_restored", { address: addr });
      } catch (e) { ui.log("silent_restore_failed", { error: String(e.message || e) }); }
    }
    render();
    return flow;
  }

  // ---- connect / disconnect ---------------------------------------------------
  async function addChain() {
    try {
      await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: core.CHAIN_HEX }] });
    } catch (err) {
      if (err?.code !== 4902) throw err;
      await provider.request({ method: "wallet_addEthereumChain", params: [{
        chainId: core.CHAIN_HEX, chainName: "GenLayer Studionet",
        nativeCurrency: { name: "GEN", symbol: "GEN", decimals: 18 }, rpcUrls: [rpcUrl], blockExplorerUrls: [EXPLORER] }] });
    }
  }

  async function onConnectClick() {
    $("bConnect").disabled = true;
    try {
      if (!provider) throw new Error("No EIP-1193 wallet found in this browser");
      await ready;
      walletNote = null;
      if (flow.connected) {
        flow.disconnect("disconnected by user");
        safe(() => prefs?.setItem(PREF_DISCONNECTED, "1"));
        $("banner").style.display = "none";
        ui.log("disconnected", {});
      } else {
        const addr = await flow.connect({ addChain });
        safe(() => prefs?.removeItem(PREF_DISCONNECTED));
        $("banner").style.display = "none";
        ui.log("connected", { address: addr });
      }
    } catch (e) {
      ui.log("wallet_connect_failed", { error: String(e.message || e) });
      walletNote = `connection failed: ${e.message || e}`;
    } finally { render(); }
  }

  // ---- case -----------------------------------------------------------------------
  async function onLoadClick() {
    try {
      await ready;
      const r = await fetchLocal("./state.json");
      let s;
      try { s = await r.json(); } catch { throw new Error("state.json is not valid JSON"); }
      if (!s || typeof s !== "object" || !s.contract || !s.payment_id || !s.wallet) throw new Error("state.json is not a case file");
      if (s.chain_id !== core.CHAIN_ID) throw new Error(`state.json is a chain ${s.chain_id} case, not Studionet ${core.CHAIN_ID}`);
      if (String(s.consensus_target).toLowerCase() !== core.CONSENSUS_ADDRESS.toLowerCase())
        throw new Error(`state.json consensus target ${s.consensus_target} is not ${core.CONSENSUS_ADDRESS}`);
      state = s;
      flow.setCase(state);
      $("case").innerHTML = `contract <code>${state.contract}</code><br>payment <code>${state.payment_id}</code>,
        policy <code>${state.policy_id}</code>, TTL ${state.ttl_seconds}s<br>recipient <code>${state.wallet}</code>`;
      if (state.live_state_verified !== true)
        $("case").innerHTML += `<br><strong class="bad">Contract state not verified by a live read; wallet writes disabled.
          Run <code>operator_studionet.py check-state</code>, then load the case again.</strong>`;
    } catch (e) {
      ui.log("case_load_failed", { error: String(e.message || e) });
      $("case").textContent = `could not load the case: ${e.message || e}`;
    } finally { render(); }
  }

  function wire(list, armBtn, cancelBtn, run) {
    const handler = async () => {
      $(armBtn).disabled = true; $(cancelBtn).disabled = false;
      $(cancelBtn).onclick = () => flow.cancel(list);
      steps[list].running = true; render();
      let r = null;
      try { r = await run(list); return r; } catch (e) { ui.stage(list, e.message, "bad"); return null; }
      finally { steps[list] = { running: false, result: r }; $(cancelBtn).disabled = true; render(); }
    };
    $(armBtn).onclick = handler;
    return handler;
  }

  const ready = init().catch((e) => {
    ui.log("init_failed", { error: String(e.message || e) });
    walletNote = `page could not initialise: ${e.message || e}`;
    $("wallet").textContent = walletNote;
    throw e;
  });
  ready.catch(() => {});

  $("bConnect").onclick = onConnectClick;
  $("bLoad").onclick = onLoadClick;
  const armAccept = wire("accept", "bArmAccept", "bCancelAccept", (l) => flow.armAccept(l));
  const armClaim = wire("claim", "bArmClaim", "bCancelClaim", (l) => flow.armClaim(l));
  $("bDownload").onclick = () => {
    const a = doc.createElement("a");
    a.href = URL.createObjectURL(new Blob([J(LOG)], { type: "application/json" }));
    a.download = "wallet_log.json"; a.click();
  };

  return {
    ready, log: LOG, get flow() { return flow; }, get state() { return state; }, view: () => snapshot(),
    handlers: { connect: onConnectClick, load: onLoadClick, armAccept, armClaim },
  };
}
