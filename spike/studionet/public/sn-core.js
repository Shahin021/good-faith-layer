// Pure helpers for the Studionet wallet page (chain 61999): no DOM, no network
// of its own. Everything external (wallet provider, sleep, clock) is passed in
// so the offline tests exercise exactly this code. Same rules as
// spike/studionet/studionet_checks.py (the authoritative verifier).

export const CHAIN_ID = 61999;
export const CHAIN_HEX = "0x" + CHAIN_ID.toString(16);
export const POLL_MS = 3000;                 // equal to POLL_SECONDS in operator_studionet.py
export const DECIDED_OK = new Set(["ACCEPTED", "FINALIZED"]);
export const TERMINAL_BAD = new Set(["UNDETERMINED", "LEADER_TIMEOUT", "VALIDATORS_TIMEOUT", "CANCELED"]);
// Static studionet consensus address of genlayer-js 1.1.8 (and genlayer-py 0.18.0).
// Studionet's sim_getConsensusContract reports the zero address; it is not used.
export const CONSENSUS_ADDRESS = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575";
// keccak("addTransaction(address,address,uint256,uint256,bytes)")[:4]; a test cross-checks the SDK output.
export const ADD_TX_V5_SELECTOR = "0x27241a99";

export class Cancelled extends Error { constructor(m) { super(m); this.name = "Cancelled"; } }
export class Invalidated extends Error { constructor(m) { super(m); this.name = "Invalidated"; } }
export class TimedOut extends Error { constructor(m) { super(m); this.name = "TimedOut"; } }
export class EnvelopeRejected extends Error { constructor(m) { super(m); this.name = "EnvelopeRejected"; } }

export const defaultSleep = (ms, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) return reject(new Cancelled(String(signal.reason ?? "cancelled")));
  const t = setTimeout(resolve, ms);
  signal?.addEventListener("abort", () => { clearTimeout(t); reject(new Cancelled(String(signal.reason ?? "cancelled"))); }, { once: true });
});

// ---- session: any wallet/network change bumps the generation -------------
export function createSession() {
  return { generation: 0, account: null, controllers: new Set(), invalidReason: null };
}

export function invalidate(session, reason) {
  session.generation += 1;
  session.account = null;
  session.invalidReason = reason;
  for (const c of session.controllers) c.abort(reason);
  session.controllers.clear();
}

export function newAction(session) {
  const controller = new AbortController();
  session.controllers.add(controller);
  return { controller, signal: controller.signal, generation: session.generation,
           done: () => session.controllers.delete(controller) };
}

// Throw if the action was cancelled or the wallet/network changed since it started.
// Called after every await in a write path.
export function ensureLive(session, act) {
  if (act.signal.aborted) throw new Cancelled(String(act.signal.reason ?? "cancelled"));
  if (act.generation !== session.generation)
    throw new Invalidated(session.invalidReason ? `wallet or network changed (${session.invalidReason})` : "wallet or network changed");
}

// ---- revalidate wallet context ------------------------------------------
export async function validateWriteContext({ provider, session, generation, caseWallet }) {
  const stale = () => generation !== session.generation;
  if (stale()) throw new Invalidated("wallet or network changed since this action was armed; reconnect and re-arm");
  const chainHex = await provider.request({ method: "eth_chainId" });
  const chain = parseInt(chainHex, 16);
  if (chain !== CHAIN_ID) throw new Invalidated(`wallet is on chain ${chain}, expected ${CHAIN_ID}`);
  const accounts = await provider.request({ method: "eth_accounts" });
  if (!Array.isArray(accounts) || accounts.length === 0) throw new Invalidated("wallet exposes no account");
  const exposed = String(accounts[0]).toLowerCase();
  if (!session.account || exposed !== session.account.toLowerCase())
    throw new Invalidated("exposed wallet account differs from the connected account");
  if (!caseWallet || exposed !== caseWallet.toLowerCase())
    throw new Invalidated("exposed wallet account is not the case recipient");
  if (stale()) throw new Invalidated("wallet or network changed during validation");
  return exposed;
}

// ---- bounded polling with cancellation -----------------------------------
// fn returns undefined/null/false to keep polling, anything else to stop.
export async function pollUntil(fn, { timeoutMs, intervalMs = POLL_MS, signal,
                                      sleep = defaultSleep, now = Date.now } = {}) {
  if (!(timeoutMs > 0)) throw new Error("pollUntil requires a positive timeoutMs");
  const deadline = now() + timeoutMs;
  for (;;) {
    if (signal?.aborted) throw new Cancelled(String(signal.reason ?? "cancelled"));
    const v = await fn();
    if (signal?.aborted) throw new Cancelled(String(signal.reason ?? "cancelled"));
    if (v !== undefined && v !== null && v !== false) return v;
    if (now() >= deadline) throw new TimedOut(`gave up after ${timeoutMs} ms`);
    await sleep(intervalMs, signal);
  }
}

// ---- retries are for reads only ------------------------------------------
export async function withReadRetry(fn, { delaysMs = [600, 1500], signal, sleep = defaultSleep } = {}) {
  let last;
  for (let i = 0; i <= delaysMs.length; i++) {
    if (signal?.aborted) throw new Cancelled(String(signal.reason ?? "cancelled"));
    try { return await fn(); }
    catch (e) {
      if (e instanceof Cancelled || e instanceof Invalidated) throw e;
      last = e;
      if (i < delaysMs.length) await sleep(delaysMs[i], signal);
    }
  }
  throw last;
}

// ---- bytes, strict RLP ---------------------------------------------------
export function hexToBytes(hex) {
  if (typeof hex !== "string" || !/^0x([0-9a-fA-F]{2})*$/.test(hex)) throw new EnvelopeRejected("not 0x-hex");
  const out = new Uint8Array((hex.length - 2) / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(2 + 2 * i, 4 + 2 * i), 16);
  return out;
}
export const bytesToHex = (b) => "0x" + Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
const beInt = (b) => b.reduce((n, x) => n * 256n + BigInt(x), 0n);

export function rlpDecode(bytes) {
  const [item, end] = rlpItem(bytes, 0);
  if (end !== bytes.length) throw new EnvelopeRejected("trailing bytes after RLP item");
  return item;
}
function rlpItem(b, i) {
  if (i >= b.length) throw new EnvelopeRejected("truncated RLP");
  const p = b[i];
  const len = (n, at) => {
    if (b[at] === 0) throw new EnvelopeRejected("non-canonical RLP length");
    const v = Number(beInt(b.subarray(at, at + n)));
    if (v < 56) throw new EnvelopeRejected("non-canonical RLP long form");
    return v;
  };
  let start, l, isList;
  if (p < 0x80) return [b.subarray(i, i + 1), i + 1];
  if (p <= 0xb7) { l = p - 0x80; start = i + 1; isList = false;
    if (l === 1 && b[start] < 0x80) throw new EnvelopeRejected("non-canonical single byte"); }
  else if (p < 0xc0) { const n = p - 0xb7; l = len(n, i + 1); start = i + 1 + n; isList = false; }
  else if (p <= 0xf7) { l = p - 0xc0; start = i + 1; isList = true; }
  else { const n = p - 0xf7; l = len(n, i + 1); start = i + 1 + n; isList = true; }
  const end = start + l;
  if (end > b.length) throw new EnvelopeRejected("truncated RLP");
  if (!isList) return [b.subarray(start, end), end];
  const items = [];
  let j = start;
  while (j < end) { const [it, nj] = rlpItem(b, j); items.push(it); j = nj; }
  if (j !== end) throw new EnvelopeRejected("RLP list length mismatch");
  return [items, end];
}

// ---- consensus addTransaction, v5: (address sender, address recipient, uint256 validators,
//      uint256 rotations, bytes txData). No GenLayer value field: value is the EVM value.
export function decodeAddTransaction(dataHex) {
  const d = hexToBytes(dataHex);
  if (bytesToHex(d.subarray(0, 4)) !== ADD_TX_V5_SELECTOR)
    throw new EnvelopeRejected(`unexpected consensus selector ${bytesToHex(d.subarray(0, 4))} (expected v5 ${ADD_TX_V5_SELECTOR})`);
  const body = d.subarray(4);
  const word = (off) => { if (off + 32 > body.length) throw new EnvelopeRejected("truncated ABI data"); return body.subarray(off, off + 32); };
  const num = (off) => beInt(word(off));
  const addr = (off) => { const w = word(off); if (w.subarray(0, 12).some((x) => x)) throw new EnvelopeRejected("dirty address word"); return bytesToHex(w.subarray(12)); };
  const off = Number(num(32 * 4));
  if (off !== 32 * 5) throw new EnvelopeRejected("unexpected txData offset");
  const len = Number(num(off));
  if (off + 32 + len > body.length) throw new EnvelopeRejected("truncated txData");
  if (body.length !== off + 32 + Math.ceil(len / 32) * 32) throw new EnvelopeRejected("trailing ABI data");
  return {
    glSender: addr(0), glRecipient: addr(32), validators: num(64), rotations: num(96),
    txCalldata: body.subarray(off + 32, off + 32 + len),
  };
}

// inner rlp([genvm_calldata, mode]); mode must be empty or 0x00 (NORMAL / not leader-only).
// GenVM v0.2.16 calldata: exactly {method, args?}; a "" key, kwargs or other keys are refused.
export function decodeInnerCall(sdk, txCalldata) {
  const items = rlpDecode(txCalldata);
  if (!Array.isArray(items) || items.length !== 2 || items.some(Array.isArray))
    throw new EnvelopeRejected("inner transaction data is not a 2-item RLP list (not a contract call)");
  const mode = items[1];
  if (!(mode.length === 0 || (mode.length === 1 && mode[0] === 0)))
    throw new EnvelopeRejected(`execution mode/leader_only is ${bytesToHex(mode)}, not normal`);
  let call;
  try { call = sdk.abi.calldata.decode(items[0]); } catch (e) { throw new EnvelopeRejected("GenVM calldata does not decode: " + e.message); }
  if (!(call instanceof Map)) throw new EnvelopeRejected("GenVM calldata is not an object");
  const keys = [...call.keys()];
  if (keys.includes("")) throw new EnvelopeRejected("GenVM calldata uses the v0.6 '' method key");
  const extra = keys.filter((k) => k !== "method" && k !== "args");
  if (extra.length) throw new EnvelopeRejected(`unexpected GenVM calldata keys ${JSON.stringify(extra)}`);
  return { method: call.get("method"), args: call.get("args") ?? [], kwargs: null };
}

export const sameArgs = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// Signed raw EVM tx -> {chainId, to, data}. No signature recovery here (display only;
// the authoritative recovery is studionet_checks.py).
export function decodeRawTx(rawHex) {
  const b = hexToBytes(rawHex);
  if (!b.length) throw new EnvelopeRejected("empty envelope");
  if (b[0] >= 0xc0) {
    const f = rlpDecode(b);
    if (f.length !== 9) throw new EnvelopeRejected("legacy envelope field count");
    const v = beInt(f[6]);
    if (v === 27n || v === 28n) throw new EnvelopeRejected("envelope is not EIP-155 protected");
    return { chainId: Number((v - 35n) / 2n), to: bytesToHex(f[3]), value: beInt(f[4]), data: bytesToHex(f[5]) };
  }
  if (b[0] === 2) {
    const f = rlpDecode(b.subarray(1));
    if (f.length !== 12) throw new EnvelopeRejected("type-2 envelope field count");
    return { chainId: Number(beInt(f[0])), to: bytesToHex(f[5]), value: beInt(f[6]), data: bytesToHex(f[7]) };
  }
  throw new EnvelopeRejected(`unsupported envelope type ${b[0]}`);
}

// ---- Studio record adapter (same rules as spike_checks.normalize_record) --
export const RECORD_ALIASES = {
  txHash: ["tx_id", "hash"], sender: ["sender", "from_address"], recipient: ["recipient", "to_address"],
  electorate: ["num_of_initial_validators", "electorate"],
};
export const SIM_EVIDENCE_KEYS = new Set(["signed_rollup_transaction"]);
export const SIM_OVERRIDE_KEYS = new Set(["genvm_datetime", "genvm_executor_selector", "validators"]);

export function normalizeRecord(raw) {
  if (!raw || typeof raw !== "object") throw new EnvelopeRejected("no transaction record");
  const out = {};
  for (const [canon, names] of Object.entries(RECORD_ALIASES)) {
    const seen = new Map();
    for (const n of names) {
      const v = raw[n];
      if (v === undefined || v === null || v === "") continue;
      seen.set(n, canon === "electorate" ? Number(v) : String(v).toLowerCase());
    }
    const vals = new Set(seen.values());
    if (vals.size > 1) throw new EnvelopeRejected(`conflicting aliases for ${canon}: ${JSON.stringify([...seen])}`);
    out[canon] = vals.size ? [...vals][0] : null;
  }
  for (const k of ["type", "status", "txExecutionResultName", "execution_mode", "leader_only", "sim_config",
                   "consensus_data", "consensus_history", "data"])
    out[k] = raw[k];
  return out;
}

export function simConfigProblems(sim) {
  if (sim === null || sim === undefined) return [];
  if (typeof sim !== "object" || Array.isArray(sim)) return ["sim_config is not an object"];
  const p = [];
  for (const [k, v] of Object.entries(sim)) {
    if (SIM_EVIDENCE_KEYS.has(k)) continue;
    if (SIM_OVERRIDE_KEYS.has(k)) {
      const empty = v === null || v === "" || (Array.isArray(v) && !v.length) || (typeof v === "object" && v && !Object.keys(v).length);
      if (!empty) p.push(`simulation override sim_config.${k}`);
    } else p.push(`unsupported sim_config key ${k}`);
  }
  return p;
}

export function consensusOutcome(votes, electorate) {
  const vs = votes.map((v) => String(v).toLowerCase());
  const total = electorate == null ? vs.length : Math.max(vs.length, Number(electorate));
  const majority = total / 2;
  const n = (x) => vs.filter((v) => v === x).length;
  if (n("agree") > majority) return "MAJORITY_AGREE";
  if (n("disagree") > majority) return "MAJORITY_DISAGREE";
  if (n("timeout") + n("idle") > majority) return "TIMEOUT";
  if (n("deterministic_violation") > majority) return "DETERMINISTIC_VIOLATION";
  return "NO_MAJORITY";
}

// Studio rc.7's rule, applied to v0.121 records (which carry no txExecutionResultName).
export function executionResultName(cd) {
  let lr = cd && typeof cd === "object" ? cd.leader_receipt : null;
  if (Array.isArray(lr)) lr = lr[0];
  if (!lr || typeof lr !== "object") return "NOT_VOTED";
  const code = String(lr.genvm_result?.error_code ?? "");
  if (code === "CONSENSUS_LEADER_EXEC_TIMEOUT" || code === "CONSENSUS_VALIDATOR_EXEC_TIMEOUT") return "TIMEOUT";
  const v = String(lr.execution_result ?? "").toUpperCase();
  if (v === "SUCCESS") return "FINISHED_WITH_RETURN";
  if (["ERROR", "FAILURE", "FINISHEDWITHERROR", "FINISHED_WITH_ERROR"].includes(v)) return "FINISHED_WITH_ERROR";
  return "NOT_VOTED";
}

const b64 = (bytes) => { let s = ""; for (const x of bytes) s += String.fromCharCode(x); return btoa(s); };

// Display-only summary; the authoritative pass/fail is operator_studionet.py verify.
export function summarizeRecord(raw, { hash, contract, sender, method, args, sdk }) {
  let r;
  try { r = normalizeRecord(raw); } catch (e) { return { problems: [e.message] }; }
  const problems = [];
  const votes = r.consensus_data?.votes || {};
  const outcome = Object.keys(votes).length ? consensusOutcome(Object.values(votes), r.electorate) : "NO_VOTES";
  const execution = executionResultName(r.consensus_data);
  if (hash && r.txHash !== String(hash).toLowerCase()) problems.push(`record hash ${r.txHash} != ${hash}`);
  if (!DECIDED_OK.has(r.status)) problems.push(`status ${r.status}`);
  if (r.electorate == null) problems.push("electorate missing");
  if (outcome !== "MAJORITY_AGREE") problems.push(`consensus ${outcome}`);
  if (execution !== "FINISHED_WITH_RETURN") problems.push(`execution ${execution}`);
  if (r.txExecutionResultName != null && r.txExecutionResultName !== execution) problems.push(`record says ${r.txExecutionResultName}`);
  const rounds = r.consensus_history?.consensus_results;
  if (!Array.isArray(rounds) || !rounds.length || rounds[rounds.length - 1]?.consensus_round !== "Accepted")
    problems.push("last consensus round is not Accepted");
  if (r.leader_only !== false || r.execution_mode !== "NORMAL") problems.push(`mode ${r.execution_mode}/leader_only=${r.leader_only}`);
  problems.push(...simConfigProblems(r.sim_config));
  if (r.sender !== String(sender).toLowerCase()) problems.push(`sender ${r.sender}`);
  if (r.recipient !== String(contract).toLowerCase()) problems.push(`target ${r.recipient}`);
  if (r.type !== 2) problems.push(`type ${r.type}`);
  const env = r.sim_config?.signed_rollup_transaction;
  if (!env) problems.push("signed envelope missing");
  else if (sdk) {
    try {
      const tx = decodeRawTx(env);
      if (tx.chainId !== CHAIN_ID) problems.push(`envelope chain ${tx.chainId}`);
      if (tx.to.toLowerCase() !== CONSENSUS_ADDRESS.toLowerCase()) problems.push(`envelope target ${tx.to}`);
      if (tx.value !== 0n) problems.push(`envelope value ${tx.value}`);
      if (hash && bytesToHex(keccak256(hexToBytes(env))) !== String(hash).toLowerCase()) problems.push("keccak(envelope) != hash");
      const at = decodeAddTransaction(tx.data);
      if (at.glSender !== String(sender).toLowerCase()) problems.push(`envelope sender ${at.glSender}`);
      if (at.glRecipient !== String(contract).toLowerCase()) problems.push(`envelope recipient ${at.glRecipient}`);
      const items = rlpDecode(at.txCalldata);
      const call = decodeInnerCall(sdk, at.txCalldata);
      if (call.method !== method || !sameArgs(call.args, args)) problems.push(`envelope call ${call.method}`);
      if (r.data?.calldata !== b64(items[0])) problems.push("record calldata differs from the envelope");
    } catch (e) { problems.push(`envelope: ${e.message}`); }
  }
  return { status: r.status, outcome, votes, execution, mode: r.execution_mode, method, problems };
}

// ---- Keccak-256 and EIP-55 checksum addresses -----------------------------
// Studio looks contracts up by exact string, so addresses go out in EIP-55 form.
// Self-contained (the bundled SDK does not export keccak); verified in tests
// against Keccak known-answer vectors, the EIP-55 spec vectors, and
// eth_utils.to_checksum_address for random addresses.
const KECCAK_RC = [
  0x0000000000000001n, 0x0000000000008082n, 0x800000000000808an, 0x8000000080008000n,
  0x000000000000808bn, 0x0000000080000001n, 0x8000000080008081n, 0x8000000000008009n,
  0x000000000000008an, 0x0000000000000088n, 0x0000000080008009n, 0x000000008000000an,
  0x000000008000808bn, 0x800000000000008bn, 0x8000000000008089n, 0x8000000000008003n,
  0x8000000000008002n, 0x8000000000000080n, 0x000000000000800an, 0x800000008000000an,
  0x8000000080008081n, 0x8000000000008080n, 0x0000000080000001n, 0x8000000080008008n,
];
const KECCAK_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]];
const M64 = (1n << 64n) - 1n;
const rotl = (v, n) => (n === 0 ? v : ((v << BigInt(n)) | (v >> BigInt(64 - n))) & M64);

function keccakF(s) {
  const C = new Array(5), B = new Array(25);
  for (let round = 0; round < 24; round++) {
    for (let x = 0; x < 5; x++) C[x] = s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20];
    for (let x = 0; x < 5; x++) {
      const D = C[(x + 4) % 5] ^ rotl(C[(x + 1) % 5], 1);
      for (let y = 0; y < 5; y++) s[x + 5 * y] ^= D;
    }
    for (let x = 0; x < 5; x++)
      for (let y = 0; y < 5; y++) B[y + 5 * ((2 * x + 3 * y) % 5)] = rotl(s[x + 5 * y], KECCAK_ROT[x][y]);
    for (let x = 0; x < 5; x++)
      for (let y = 0; y < 5; y++)
        s[x + 5 * y] = B[x + 5 * y] ^ ((~B[((x + 1) % 5) + 5 * y] & M64) & B[((x + 2) % 5) + 5 * y]);
    s[0] ^= KECCAK_RC[round];
  }
}

export function keccak256(bytes) {
  const rate = 136;
  const padded = new Uint8Array(Math.ceil((bytes.length + 1) / rate) * rate);
  padded.set(bytes);
  padded[bytes.length] ^= 0x01;
  padded[padded.length - 1] ^= 0x80;
  const s = new Array(25).fill(0n);
  for (let off = 0; off < padded.length; off += rate) {
    for (let i = 0; i < rate / 8; i++) {
      let lane = 0n;
      for (let b = 7; b >= 0; b--) lane = (lane << 8n) | BigInt(padded[off + 8 * i + b]);
      s[i] ^= lane;
    }
    keccakF(s);
  }
  const out = new Uint8Array(32);
  for (let i = 0; i < 4; i++) for (let b = 0; b < 8; b++) out[8 * i + b] = Number((s[i] >> BigInt(8 * b)) & 0xffn);
  return out;
}

export function toChecksumAddress(address) {
  if (typeof address !== "string" || !/^0x[0-9a-fA-F]{40}$/.test(address))
    throw new Error(`not a 20-byte 0x address: ${address}`);
  const lowerHex = address.slice(2).toLowerCase();
  const hash = keccak256(new TextEncoder().encode(lowerHex));
  let out = "0x";
  for (let i = 0; i < 40; i++) {
    const nibble = (hash[i >> 1] >> (i % 2 === 0 ? 4 : 0)) & 0xf;
    out += nibble >= 8 ? lowerHex[i].toUpperCase() : lowerHex[i];
  }
  const body = address.slice(2);
  const mixed = body !== body.toLowerCase() && body !== body.toUpperCase();
  if (mixed && address !== out) throw new Error(`invalid EIP-55 checksum: ${address}`);
  return out;
}

// ---- JSON-RPC over fetch with timeout and response validation -------------
export const RPC_TIMEOUT_MS = 12000;
export class RpcError extends Error {            // the endpoint answered with a JSON-RPC error
  constructor(method, err) { super(`${method} failed (code=${err?.code ?? "unknown"}): ${err?.message ?? "unknown error"}`); this.name = "RpcError"; this.code = err?.code; }
}
export class RpcTimeout extends Error { constructor(m) { super(m); this.name = "RpcTimeout"; } }
export class RpcTransportError extends Error { constructor(m) { super(m); this.name = "RpcTransportError"; } }
export class Busy extends Error { constructor(m) { super(m); this.name = "Busy"; } }

// Resolve, or reject with RpcTimeout after ms even if the underlying work ignores aborts.
export function withTimeout(promise, ms, label, { setTimer = setTimeout, clearTimer = clearTimeout, onTimeout } = {}) {
  let t;
  const timer = new Promise((_, reject) => {
    t = setTimer(() => { onTimeout?.(); reject(new RpcTimeout(`${label}: no response within ${ms / 1000}s`)); }, ms);
  });
  return Promise.race([promise, timer]).finally(() => clearTimer(t));
}

export function createRpc({ url, fetchImpl = (...a) => globalThis.fetch(...a), timeoutMs = RPC_TIMEOUT_MS, timers = {} } = {}) {
  let nextId = 0;
  return async function rpc(method, params = []) {
    const id = ++nextId;
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    const work = (async () => {
      let r;
      try {
        r = await fetchImpl(url, { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ jsonrpc: "2.0", id, method, params }), signal: ctl?.signal });
      } catch (e) {
        if (e instanceof RpcTimeout) throw e;
        throw new RpcTransportError(`${method}: request failed: ${e?.message ?? e}`);
      }
      if (!r || typeof r.ok !== "boolean") throw new RpcTransportError(`${method}: no HTTP response`);
      if (!r.ok) throw new RpcTransportError(`${method}: HTTP ${r.status}`);
      let body;
      try { body = await r.json(); } catch { throw new RpcTransportError(`${method}: response is not JSON`); }
      if (!body || typeof body !== "object" || body.jsonrpc !== "2.0" || body.id !== id)
        throw new RpcTransportError(`${method}: malformed JSON-RPC response`);
      if (body.error) throw new RpcError(method, body.error);
      if (!("result" in body)) throw new RpcTransportError(`${method}: JSON-RPC response has no result`);
      return body.result;
    })();
    return withTimeout(work, timeoutMs, method, { ...timers, onTimeout: () => ctl?.abort("timeout") });
  };
}
