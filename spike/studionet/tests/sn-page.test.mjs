// Behavioural tests for the Studionet page handlers (sn-page.js): fake DOM,
// wallet, storage and HTTP endpoint; real flow module and real genlayer-js 1.1.8.
import test from "node:test";
import assert from "node:assert/strict";
import * as core from "../public/sn-core.js";
import { mountPage, RPC_URL, PREF_DISCONNECTED } from "../public/sn-page.js";

const loadSdk = () => import("../public/vendor/genlayer-js-1.1.8.js");
const SDK = await loadSdk();
const W = "0xcc88888f2eed5d8e457e6753faae64941dc33092";
const W_CHECKSUM = "0xcC88888f2eeD5D8e457e6753FAaE64941dc33092";
const CONTRACT_LOWER = "0x" + "c0".repeat(20);
const IDS = ["bConnect", "wallet", "case", "bLoad", "bArmAccept", "bCancelAccept", "accept", "bArmClaim",
  "bCancelClaim", "claim", "banner", "bannerText", "log", "bDownload"];
const SNAP = ["wallet_getSnaps", "wallet_requestSnaps", "wallet_invokeSnap"];

function fakeDoc() {
  const els = {};
  const mk = (id) => ({ id, textContent: "", innerHTML: "", disabled: false, className: "", style: {}, value: "",
    onclick: null, children: [], appendChild(c) { this.children.push(c); } });
  for (const id of IDS) els[id] = mk(id);
  return { els, getElementById: (id) => { if (!els[id]) throw new Error("missing #" + id); return els[id]; }, createElement: (t) => mk(t) };
}
function memStore() { const m = new Map(); return { m, getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; }

function fakeWallet({ authorized = true, chain = core.CHAIN_HEX, account = W, known = true } = {}) {
  const listeners = {};
  const w = { authorized, chain, account, calls: [], params: [], known,
    on(ev, cb) { (listeners[ev] ||= []).push(cb); },
    emit(ev, payload) { for (const cb of listeners[ev] || []) cb(payload); },
    async request({ method, params }) {
      w.calls.push(method); w.params.push([method, params]);
      if (method === "eth_accounts") return w.authorized ? [w.account] : [];
      if (method === "eth_requestAccounts") { w.authorized = true; return [w.account]; }
      if (method === "eth_chainId") return w.chain;
      if (method === "wallet_switchEthereumChain") {
        if (!w.known) { const e = new Error("unknown chain"); e.code = 4902; throw e; }
        w.chain = params[0].chainId; return null;
      }
      if (method === "wallet_addEthereumChain") { w.known = true; w.chain = params[0].chainId; return null; }
      throw new Error("wallet: unexpected " + method);
    } };
  return w;
}

function fakeEndpoint() {
  const e = { calls: [], files: {}, chain: "0xf22f" };
  e.fetch = async (url, init = {}) => {
    if (url !== RPC_URL) {
      const body = e.files[url];
      if (body === undefined) return { ok: false, status: 404, json: async () => ({}), text: async () => "" };
      return { ok: true, status: 200, json: async () => JSON.parse(body), text: async () => body };
    }
    const req = JSON.parse(init.body);
    e.calls.push([req.method, req.params]);
    const reply = (x) => ({ ok: true, status: 200, json: async () => ({ jsonrpc: "2.0", id: req.id, ...x }),
      headers: new Headers({ "content-type": "application/json" }), text: async () => JSON.stringify({ jsonrpc: "2.0", id: req.id, ...x }) });
    if (req.method === "eth_chainId") return reply({ result: e.chain });
    if (req.method === "sim_getConsensusContract") return reply({ result: { address: "0x" + "00".repeat(20) } });
    return reply({ error: { code: -32601, message: "fake endpoint: " + req.method } });
  };
  return e;
}

async function mount({ wallet = fakeWallet(), endpoint = fakeEndpoint(), prefs = memStore(), timeoutMs = 50 } = {}) {
  const doc = fakeDoc();
  const page = mountPage({ doc, provider: wallet, loadSdk, fetchImpl: (u, i) => endpoint.fetch(u, i), prefs,
    pendingStore: memStore(), timeoutMs, flowTiming: { readDelaysMs: [1] } });
  try { await page.ready; } catch { /* init errors are asserted by the caller */ }
  return { page, doc, els: doc.els, wallet, endpoint, prefs };
}

const CASE = (over = {}) => JSON.stringify({ chain_id: 61999, consensus_target: core.CONSENSUS_ADDRESS,
  contract: CONTRACT_LOWER, payment_id: "sn-1", policy_id: "gfl-sn-1", wallet: W, ttl_seconds: 600,
  live_state_verified: true, ...over });

test("init pins the SDK consensus address and ignores the hosted zero address", async () => {
  const { page } = await mount();
  const ev = page.log.find((e) => e.event === "consensus_contract");
  assert.equal(ev.pinned, core.CONSENSUS_ADDRESS);
  assert.equal(ev.hosted_reports, "0x" + "00".repeat(20));
});

test("init refuses an RPC that is not chain 61999", async () => {
  const endpoint = fakeEndpoint(); endpoint.chain = "0xf22d";
  const { page, els } = await mount({ endpoint });
  await assert.rejects(page.ready, /61997/);
  assert.match(els.wallet.textContent, /could not initialise/);
});

test("silent restore on reload: no prompt, no Snap", async () => {
  const { els, wallet } = await mount();
  assert.match(els.wallet.innerHTML, new RegExp(W_CHECKSUM));
  assert.ok(!wallet.calls.includes("eth_requestAccounts"));
  assert.ok(!wallet.calls.some((m) => SNAP.includes(m)));
});

test("Connect on an unknown network adds Studionet (61999, studio.genlayer.com) without a Snap", async () => {
  const wallet = fakeWallet({ authorized: false, chain: "0x1", known: false });
  const { page, els } = await mount({ wallet });
  await page.handlers.connect();
  const add = wallet.params.find(([m]) => m === "wallet_addEthereumChain")[1][0];
  assert.equal(add.chainId, "0xf22f");
  assert.deepEqual(add.rpcUrls, ["https://studio.genlayer.com/api"]);
  assert.ok(!wallet.calls.some((m) => SNAP.includes(m)), wallet.calls.join());
  assert.match(els.wallet.innerHTML, /chain 61999/);
});

test("Disconnect is remembered across reloads", async () => {
  const prefs = memStore();
  const { page } = await mount({ prefs });
  await page.handlers.connect();
  assert.equal(prefs.getItem(PREF_DISCONNECTED), "1");
  const again = await mount({ prefs });
  assert.equal(again.page.flow.connected, false);
});

test("a 61997 case file is refused", async () => {
  const { page, els, endpoint } = await mount();
  endpoint.files["./state.json"] = CASE({ chain_id: 61997 });
  await page.handlers.load();
  assert.match(els.case.textContent, /not Studionet/);
  assert.equal(els.bArmAccept.disabled, true);
});

test("a case with another consensus target is refused", async () => {
  const { page, els, endpoint } = await mount();
  endpoint.files["./state.json"] = CASE({ consensus_target: "0x" + "00".repeat(20) });
  await page.handlers.load();
  assert.match(els.case.textContent, /consensus target/);
});

test("unverified case keeps Arm disabled; verified enables it", async () => {
  const { page, els, endpoint } = await mount();
  endpoint.files["./state.json"] = CASE({ live_state_verified: false });
  await page.handlers.load();
  assert.equal(els.bArmAccept.disabled, true);
  endpoint.files["./state.json"] = CASE();
  await page.handlers.load();
  assert.equal(els.bArmAccept.disabled, false);
});

test("browser reads use gen_call with the EIP-55 contract and the 'method' calldata key", async () => {
  const { page, endpoint } = await mount();
  endpoint.files["./state.json"] = CASE();
  endpoint.files["./assertions.json"] = '"a"';
  await page.handlers.load();
  let seen = null;
  // The SDK's read client posts to the chain's RPC URL with the global fetch.
  globalThis.fetch = async (u, i) => {
    const r = JSON.parse(i.body);
    if (r.method === "gen_call") seen = r.params[0];
    return new Response(JSON.stringify({ jsonrpc: "2.0", id: r.id, error: { code: -32000, message: "test stop" } }),
      { headers: { "content-type": "application/json" } });
  };
  page.flow.cancel("accept");
  const run = page.handlers.armAccept();
  const t = Date.now();
  while (!seen && Date.now() - t < 2000) await new Promise((r) => setTimeout(r, 5));
  page.flow.cancel("accept");
  await run;
  assert.ok(seen, "a gen_call was made");
  assert.equal(seen.to, core.toChecksumAddress(CONTRACT_LOWER));
  const items = core.rlpDecode(core.hexToBytes(seen.data));
  const call = SDK.abi.calldata.decode(items[0]);
  assert.equal(call.get("method"), "get_status");
  assert.ok(!call.has(""));
});
