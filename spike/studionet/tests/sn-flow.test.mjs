// Behavioural tests for the Studionet wallet flow (sn-flow.js), driven through
// the real vendored genlayer-js 1.1.8 bundle. The fake wallet signs every
// request it is given with a throwaway in-memory key (EIP-155, legacy), the
// way MetaMask would, so records carry real envelopes. Only the wallet, the
// HTTP transport and the contract state are fakes.
//
//   node --test spike/studionet/tests/
import test from "node:test";
import assert from "node:assert/strict";
import * as core from "../public/sn-core.js";
import { createFlow, createGuardedProvider } from "../public/sn-flow.js";
import { keccak256, parseTransaction } from "../sdk/node_modules/viem/_esm/index.js";
import { generatePrivateKey, privateKeyToAccount } from "../sdk/node_modules/viem/_esm/accounts/index.js";

const sdk = await import("../public/vendor/genlayer-js-1.1.8.js");
const CHAIN = sdk.chains.studionet;
const CONTRACT = "0x" + "c0".repeat(20);
const ASSERTIONS = '{"deliverable":"x"}\n';
const SNAP = ["wallet_getSnaps", "wallet_requestSnaps", "wallet_invokeSnap", "wallet_snap"];
const b64 = (bytes) => Buffer.from(bytes).toString("base64");

const deferred = () => { let resolve, reject; const p = new Promise((a, b) => { resolve = a; reject = b; }); return { p, resolve, reject }; };
const until = async (cond, ms = 2000) => { const t = Date.now(); while (!cond()) { if (Date.now() - t > ms) throw new Error("until timeout"); await new Promise((r) => setTimeout(r, 2)); } };

function innerCalldata(raw) {
  const tx = parseTransaction(raw);
  const at = core.decodeAddTransaction(tx.data);
  return core.rlpDecode(at.txCalldata)[0];
}

function harness({ status = "REGISTERED", hasAtt = true, storage = null, sendErrors = [] } = {}) {
  const key = privateKeyToAccount(generatePrivateKey());       // memory only
  const W = key.address;
  const h = { W, key, sent: [], raws: new Map(), records: new Map(), walletCalls: [], fetchLog: [],
              contract: { status, has_attestation: hasAtt, fresh: true, verdict: "", paid: 0, balance: 0 },
              stages: [], logs: [], sendErrors: [...sendErrors], recordOverrides: {} };
  const listeners = {};
  h.wallet = {
    chain: core.CHAIN_HEX, accounts: [W],
    on(ev, cb) { (listeners[ev] ||= []).push(cb); },
    emit(ev, payload) { for (const cb of listeners[ev] || []) cb(payload); },
    async request({ method, params }) {
      h.walletCalls.push(method);
      if (method === "eth_requestAccounts" || method === "eth_accounts") return h.wallet.accounts;
      if (method === "eth_chainId") return h.wallet.chain;
      if (method === "eth_sendTransaction") {
        const p = params[0];
        h.sent.push(p);
        if (h.walletDefer) await h.walletDefer.p;
        if (h.sendErrors.length) throw new Error(h.sendErrors.shift());
        const raw = await key.signTransaction({ type: "legacy", to: p.to, data: p.data, value: BigInt(p.value),
          gas: BigInt(p.gas), gasPrice: BigInt(p.gasPrice ?? "0x0"), nonce: Number(BigInt(p.nonce)),
          chainId: Number(BigInt(p.chainId)) });
        const hash = keccak256(raw);
        h.raws.set(hash, raw);
        h.onSent?.(p, hash);
        return hash;
      }
      throw new Error("wallet: unexpected " + method);
    },
  };
  globalThis.fetch = async (_url, init) => {
    const { method } = JSON.parse(init.body);
    h.fetchLog.push(method);
    if (h.fetchPause && h.fetchPause.method === method) { const d = h.fetchPause.d; h.fetchPause = null; h.fetchPaused = true; await d.p; }
    const result = { eth_getTransactionCount: "0x3", eth_estimateGas: "0x7a120", eth_gasPrice: "0x0", eth_chainId: "0xf22f" }[method];
    if (result === undefined) throw new Error("unexpected RPC " + method);
    return new Response(JSON.stringify({ jsonrpc: "2.0", id: 1, result }), { headers: { "content-type": "application/json" } });
  };
  // v0.121-shaped record built from the real signed raw transaction
  h.recordFor = (hash) => {
    const raw = h.raws.get(hash);
    return { hash, from_address: W, to_address: CONTRACT, type: 2, status: "FINALIZED",
      execution_mode: "NORMAL", leader_only: false, num_of_initial_validators: 5,
      data: { calldata: b64(innerCalldata(raw)) },
      sim_config: { signed_rollup_transaction: raw },
      consensus_data: { votes: { a: "agree", b: "agree", c: "agree", d: "idle", e: "idle" },
                        leader_receipt: [{ execution_result: "SUCCESS", genvm_result: {} }] },
      consensus_history: { consensus_results: [{ consensus_round: "Accepted" }] }, ...h.recordOverrides };
  };
  h.writeCalls = 0;
  const createClient = (opts) => {
    const c = sdk.createClient(opts);
    const orig = c.writeContract;
    return { ...c, writeContract: (a) => { h.writeCalls++; h.writeArgs = a; return orig(a); } };
  };
  h.flow = createFlow({
    provider: h.wallet, sdk: { ...sdk, createClient }, chain: CHAIN, storage,
    rpc: async (m, p) => { h.rpcMethods = [...(h.rpcMethods || []), m]; return h.raws.has(p[0]) ? h.recordFor(p[0]) : null; },
    readContract: async (_st, fn) => ({ get_status: h.contract.status, has_attestation: h.contract.has_attestation,
      is_attestation_fresh: h.contract.fresh, get_verdict: h.contract.verdict, get_paid_out: h.contract.paid,
      get_claim_balance: h.contract.balance })[fn],
    loadAssertions: async () => ASSERTIONS,
    ui: { stage: (l, t, c) => h.stages.push([l, t, c]), log: (e, d) => h.logs.push([e, d]) },
    timing: { pollMs: 1, readDelaysMs: [1], waitAttestMs: 2000, waitFlagMs: 2000, waitDecidedMs: 300 },
  });
  h.onSent = () => { h.contract.status = h.contract.status === "REGISTERED" ? "ACCEPTED" : h.contract.status === "FLAGGED" ? "RESOLVED" : h.contract.status; };
  h.case = { chain_id: core.CHAIN_ID, consensus_target: core.CONSENSUS_ADDRESS, contract: CONTRACT, payment_id: "sn-1",
             policy_id: "gfl-sn-1", wallet: W, live_state_verified: true };
  return h;
}

async function ready(h, c = h.case) { h.flow.setCase(c); await h.flow.connect(); }

// =========================================================================
test("bundle is genlayer-js 1.1.8 studionet: chain 61999, pinned consensus, v5 ABI", () => {
  assert.equal(CHAIN.id, 61999);
  assert.equal(CHAIN.consensusMainContract.address, core.CONSENSUS_ADDRESS);
  const add = CHAIN.consensusMainContract.abi.filter((e) => e.name === "addTransaction");
  assert.deepEqual(add.map((e) => e.inputs.length), [5]);
});

test("createFlow refuses any other chain config", () => {
  assert.throws(() => createFlow({ chain: { ...CHAIN, consensusMainContract: { ...CHAIN.consensusMainContract, address: "0x" + "00".repeat(20) } } }), /pinned/);
  assert.throws(() => createFlow({ chain: { ...CHAIN, id: 61997 } }), /pinned/);
});

test("accept: one guarded send, real signature, record summarizes cleanly, no Snap, no fees", async () => {
  const h = harness(); await ready(h);
  const r = await h.flow.armAccept();
  assert.equal(h.sent.length, 1);
  const p = h.sent[0];
  assert.equal(p.to, core.CONSENSUS_ADDRESS);
  assert.equal(p.chainId, core.CHAIN_HEX);
  assert.equal(p.value, "0x0");
  assert.ok(p.data.startsWith(core.ADD_TX_V5_SELECTOR));
  assert.ok(!("fees" in h.writeArgs), "no v0.6 fee preset");
  assert.ok(!h.walletCalls.some((m) => SNAP.includes(m)), h.walletCalls.join(","));
  assert.deepEqual(r.summary.problems, []);
  assert.equal(r.summary.execution, "FINISHED_WITH_RETURN");
  assert.equal(r.status, "ACCEPTED");
  assert.deepEqual(h.rpcMethods.filter((m, i, a) => a.indexOf(m) === i), ["eth_getTransactionByHash"]);
});

test("claim: guarded send of open_claim, readback reported", async () => {
  const h = harness({ status: "FLAGGED" }); await ready(h);
  h.onSent = () => { Object.assign(h.contract, { status: "RESOLVED", verdict: "PROTECTED", paid: 1000, balance: 1000 }); };
  const r = await h.flow.armClaim();
  assert.equal(h.sent.length, 1);
  assert.deepEqual(r.summary.problems, []);
  assert.equal(r.fin.verdict, "PROTECTED");
});

test("SDK fallback re-send after an ABI-mismatch error is refused by the guard: at most one wallet prompt", async () => {
  // First send "fails" inside the wallet with an error the SDK treats as an ABI mismatch;
  // the SDK then tries again with the v6 encoding. That second request must never reach the wallet.
  const h = harness({ sendErrors: ["could not decode result data"] }); await ready(h);
  const r = await h.flow.armAccept();
  assert.equal(h.sent.length, 1, "exactly one eth_sendTransaction reached the wallet");
  assert.ok(h.sent[0].data.startsWith(core.ADD_TX_V5_SELECTOR));
  assert.ok(r.stopped, "no hash: the action stops");
  assert.ok(h.stages.some(([, t]) => /NOT SUBMITTED.*already forwarded/.test(t)), JSON.stringify(h.stages));
});

test("second eth_sendTransaction for the same authorization is refused even after a hash", async () => {
  const h = harness();
  const session = core.createSession(); session.account = h.W;
  const act = core.newAction(session);
  const g = createGuardedProvider({ provider: h.wallet, session, sdk, onHash: () => {} });
  const c = sdk.createClient({ chain: CHAIN, account: h.W, provider: g });
  const slot = { act, binding: {}, wallet: h.W, contract: CONTRACT, method: "open_claim", args: ["sn-1"] };
  g.authorize(slot);
  await c.writeContract({ address: CONTRACT, functionName: "open_claim", args: ["sn-1"], value: 0n, leaderOnly: false });
  assert.equal(h.sent.length, 1);
  await assert.rejects(g.request({ method: "eth_sendTransaction", params: [h.sent[0]] }), /already forwarded/);
  assert.equal(h.sent.length, 1);
});

const guardCase = async (mutate, re) => {
  const h = harness();
  const session = core.createSession(); session.account = h.W;
  const act = core.newAction(session);
  const g = createGuardedProvider({ provider: h.wallet, session, sdk, onHash: () => {} });
  const c = sdk.createClient({ chain: CHAIN, account: h.W, provider: { request: async (a) => {
    if (a.method === "eth_sendTransaction") a = { ...a, params: [mutate({ ...a.params[0] })] };
    return g.request(a);
  } } });
  g.authorize({ act, binding: {}, wallet: h.W, contract: CONTRACT, method: "open_claim", args: ["sn-1"] });
  await assert.rejects(c.writeContract({ address: CONTRACT, functionName: "open_claim", args: ["sn-1"], value: 0n, leaderOnly: false }), re);
  assert.equal(h.sent.length, 0, "nothing reached the wallet");
};

test("guard: wrong consensus target refused", () => guardCase((t) => ({ ...t, to: "0x" + "00".repeat(20) }), /consensus contract/));
test("guard: non-zero value refused", () => guardCase((t) => ({ ...t, value: "0x5" }), /not zero/));
test("guard: wrong chainId refused", () => guardCase((t) => ({ ...t, chainId: "0xf22d" }), /chainId/));
test("guard: missing chainId refused", () => guardCase((t) => { delete t.chainId; return t; }, /chainId/));
test("guard: v6 encoding refused", () => guardCase((t) => ({ ...t, data: "0xe71d5196" + t.data.slice(10) }), /v5/));

test("guard: leaderOnly true refused", async () => {
  const h = harness();
  const session = core.createSession(); session.account = h.W;
  const act = core.newAction(session);
  const g = createGuardedProvider({ provider: h.wallet, session, sdk, onHash: () => {} });
  const c = sdk.createClient({ chain: CHAIN, account: h.W, provider: g });
  g.authorize({ act, binding: {}, wallet: h.W, contract: CONTRACT, method: "open_claim", args: ["sn-1"] });
  await assert.rejects(c.writeContract({ address: CONTRACT, functionName: "open_claim", args: ["sn-1"], value: 0n, leaderOnly: true }), /not normal/);
  assert.equal(h.sent.length, 0);
});

test("guard: different args / method refused", async () => {
  const h = harness();
  const session = core.createSession(); session.account = h.W;
  const act = core.newAction(session);
  const g = createGuardedProvider({ provider: h.wallet, session, sdk, onHash: () => {} });
  const c = sdk.createClient({ chain: CHAIN, account: h.W, provider: g });
  g.authorize({ act, binding: {}, wallet: h.W, contract: CONTRACT, method: "open_claim", args: ["sn-1"] });
  await assert.rejects(c.writeContract({ address: CONTRACT, functionName: "open_claim", args: ["sn-2"], value: 0n }), /not the expected/);
  assert.equal(h.sent.length, 0);
});

test("guard: Snap and other wallet methods are not permitted", async () => {
  const h = harness();
  const g = createGuardedProvider({ provider: h.wallet, session: core.createSession(), sdk, onHash: () => {} });
  for (const m of [...SNAP, "personal_sign", "eth_signTypedData_v4", "wallet_addEthereumChain"])
    await assert.rejects(g.request({ method: m, params: [] }), /not permitted/);
});

test("wallet on another chain: nothing sent", async () => {
  const h = harness(); await ready(h);
  h.wallet.chain = "0xf22d";
  const r = await h.flow.armAccept();
  assert.equal(r.stopped, "Invalidated");
  assert.equal(h.sent.length, 0);
});

test("cancel while the SDK is inside writeContract: guard refuses, nothing sent", async () => {
  const h = harness(); await ready(h);
  h.fetchPause = { method: "eth_estimateGas", d: deferred() };
  const pause = h.fetchPause.d;
  const run = h.flow.armAccept();
  await until(() => h.fetchPaused);
  h.flow.cancel("accept");
  pause.resolve();
  const r = await run;
  assert.equal(r.stopped, "Cancelled");
  assert.equal(h.sent.length, 0);
});

test("accountsChanged while the SDK is inside writeContract: nothing sent", async () => {
  const h = harness(); await ready(h);
  h.fetchPause = { method: "eth_estimateGas", d: deferred() };
  const pause = h.fetchPause.d;
  const run = h.flow.armAccept();
  await until(() => h.fetchPaused);
  h.wallet.emit("accountsChanged", ["0x" + "b2".repeat(20)]);
  pause.resolve();
  const r = await run;
  assert.ok(["Cancelled", "Invalidated"].includes(r.stopped));
  assert.equal(h.sent.length, 0);
});

test("cancel while the wallet prompt is open: hash kept, re-arm resumes without re-sending", async () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const h = harness({ storage }); await ready(h);
  h.walletDefer = deferred();
  const run = h.flow.armAccept();
  await until(() => h.sent.length === 1);
  h.flow.cancel("accept");
  h.walletDefer.resolve();
  const r1 = await run;
  assert.equal(r1.stopped, "Cancelled");
  h.walletDefer = null;
  const r2 = await h.flow.armAccept();
  assert.equal(h.sent.length, 1, "resumed, not re-sent");
  assert.deepEqual(r2.summary.problems, []);
  assert.ok([...store.keys()].every((k) => k.startsWith("gfl-sn:61999|")), [...store.keys()].join());
});

test("live_state_verified false: wallet writes refused", async () => {
  const h = harness(); await ready(h, { ...h.case, live_state_verified: false });
  const r = await h.flow.armAccept();
  assert.equal(r.refused, "live_state_unverified");
  assert.equal(h.writeCalls, 0);
});

// ---- record summaries (display-only mirror of studionet_checks.py) ---------
async function sentRecord(over = {}) {
  const h = harness(); await ready(h);
  const r = await h.flow.armAccept();
  assert.deepEqual(r.summary.problems, []);
  const hash = r.hash;
  const rec = { ...h.recordFor(hash), ...over };
  return core.summarizeRecord(rec, { hash, contract: CONTRACT, sender: h.W, method: "accept_payment",
    args: ["sn-1", "gfl-sn-1", ASSERTIONS], sdk });
}

test("summary: missing envelope, minority, leader error, not Accepted, electorate missing, record calldata mismatch", async () => {
  const cases = [
    [{ sim_config: null }, /envelope missing/],
    [{ consensus_data: { votes: { a: "agree", b: "agree" }, leader_receipt: [{ execution_result: "SUCCESS" }] } }, /consensus/],
    [{ consensus_data: { votes: { a: "agree", b: "agree", c: "agree" }, leader_receipt: [{ execution_result: "ERROR" }] } }, /FINISHED_WITH_ERROR/],
    [{ consensus_history: { consensus_results: [{ consensus_round: "Undetermined" }] } }, /Accepted/],
    [{ num_of_initial_validators: null }, /electorate/],
    [{ data: { calldata: "AAAA" } }, /record calldata/],
    [{ leader_only: true }, /mode/],
    [{ sim_config: { signed_rollup_transaction: "0x00", validators: [1] } }, /override/],
  ];
  for (const [over, re] of cases) {
    const s = await sentRecord(over);
    assert.ok(s.problems.some((p) => re.test(p)), `${JSON.stringify(over).slice(0, 60)} -> ${s.problems}`);
  }
});

test("summary: a correctly signed envelope to the wrong target is flagged", async () => {
  const k = privateKeyToAccount(generatePrivateKey());
  const h = harness(); await ready(h);
  await h.flow.armAccept();
  const [hash, raw] = [...h.raws.entries()][0];
  const tx = parseTransaction(raw);
  const raw2 = await h.key.signTransaction({ ...tx, type: "legacy", to: "0x" + "ab".repeat(20) });
  const rec = { ...h.recordFor(hash), hash: keccak256(raw2), sim_config: { signed_rollup_transaction: raw2 } };
  const s = core.summarizeRecord(rec, { hash: keccak256(raw2), contract: CONTRACT, sender: h.W, method: "accept_payment",
    args: ["sn-1", "gfl-sn-1", ASSERTIONS], sdk });
  assert.ok(s.problems.some((p) => /envelope target/.test(p)), s.problems.join());
  void k;
});

test("decodeInnerCall refuses the v0.6 '' key and extra keys", () => {
  const enc = (obj) => sdk.abi.calldata.encode(new Map(Object.entries(obj)));
  const inner = (cd) => {
    // rlp([cd, 0x00]) via the SDK's own serializer path: build with a tiny RLP for two byte strings
    const bstr = (b) => (b.length === 1 && b[0] < 0x80 ? b : Uint8Array.from([...(b.length < 56 ? [0x80 + b.length] : [0xb7 + 1, b.length]), ...b]));
    const body = Uint8Array.from([...bstr(cd), 0x00]);
    return Uint8Array.from([...(body.length < 56 ? [0xc0 + body.length] : [0xf7 + 1, body.length]), ...body]);
  };
  assert.throws(() => core.decodeInnerCall(sdk, inner(enc({ "": "open_claim", args: ["x"] }))), /''/);
  assert.throws(() => core.decodeInnerCall(sdk, inner(enc({ method: "open_claim", args: ["x"], kwargs: {} }))), /unexpected/);
  assert.deepEqual(core.decodeInnerCall(sdk, inner(enc({ method: "open_claim", args: ["x"] }))).args, ["x"]);
});
