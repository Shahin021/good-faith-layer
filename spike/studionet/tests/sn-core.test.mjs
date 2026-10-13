// node --test spike/studionet/tests/  (core helpers; copied from the 61997 suite, plus Studionet-specific cases)
import test from "node:test";
import assert from "node:assert/strict";
import * as core from "../public/sn-core.js";

const WALLET = "0x" + "a".repeat(40);

function wallet({ chain = core.CHAIN_HEX, accounts = [WALLET] } = {}) {
  const w = { chain, accounts, sent: [],
    async request({ method, params }) {
      if (method === "eth_chainId") return w.chain;
      if (method === "eth_accounts") return w.accounts;
      if (method === "eth_sendTransaction") { w.sent.push(params); return "0x" + "ab".repeat(32); }
      throw new Error("unexpected " + method);
    } };
  return w;
}

function connected() { const s = core.createSession(); s.account = WALLET; return s; }

test("write context passes when chain, exposed account and case recipient agree", async () => {
  const s = connected();
  assert.equal(await core.validateWriteContext({ provider: wallet(), session: s, generation: 0, caseWallet: WALLET }),
    WALLET.toLowerCase());
});

for (const [name, w, caseWallet, session] of [
  ["wrong chain", wallet({ chain: "0x1" }), WALLET, connected()],
  ["no exposed account", wallet({ accounts: [] }), WALLET, connected()],
  ["exposed account differs from connected", wallet({ accounts: ["0x" + "b".repeat(40)] }), WALLET, connected()],
  ["connected account is not the case recipient", wallet(), "0x" + "c".repeat(40), connected()],
  ["never connected", wallet(), WALLET, core.createSession()],
]) {
  test(`write context rejects: ${name}`, async () => {
    await assert.rejects(core.validateWriteContext({ provider: w, session, generation: session.generation, caseWallet }),
      core.Invalidated);
  });
}

test("write context rejects an action armed before an account/chain change", async () => {
  const s = connected();
  const act = core.newAction(s);
  core.invalidate(s, "accountsChanged");
  s.account = WALLET;                                   // even after reconnecting
  await assert.rejects(core.validateWriteContext({ provider: wallet(), session: s, generation: act.generation, caseWallet: WALLET }),
    core.Invalidated);
});

test("write context rejects a change that happens during validation", async () => {
  const s = connected();
  const w = wallet();
  const orig = w.request;
  w.request = async (a) => { const r = await orig(a); if (a.method === "eth_accounts") { core.invalidate(s, "chainChanged"); s.account = WALLET; } return r; };
  await assert.rejects(core.validateWriteContext({ provider: w, session: s, generation: 0, caseWallet: WALLET }), core.Invalidated);
});

test("invalidate cancels pending polls", { timeout: 2000 }, async () => {
  const s = connected();
  const act = core.newAction(s);
  const p = core.pollUntil(async () => null, { timeoutMs: 60_000, intervalMs: 50, signal: act.signal });
  setTimeout(() => core.invalidate(s, "chainChanged to 1"), 20);
  await assert.rejects(p, core.Cancelled);
});

test("user cancel stops a poll", { timeout: 2000 }, async () => {
  const s = connected();
  const act = core.newAction(s);
  const p = core.pollUntil(async () => null, { timeoutMs: 60_000, intervalMs: 50, signal: act.signal });
  setTimeout(() => act.controller.abort("cancelled by user"), 20);
  await assert.rejects(p, core.Cancelled);
});

test("polling is bounded", { timeout: 2000 }, async () => {
  let t = 0, calls = 0;
  await assert.rejects(core.pollUntil(async () => { calls++; return null; },
    { timeoutMs: 9000, intervalMs: 3000, now: () => t, sleep: async (ms) => { t += ms; } }), core.TimedOut);
  assert.equal(calls, 4);
  await assert.rejects(core.pollUntil(async () => null, {}), /positive timeoutMs/);
});

test("poll returns the first non-empty result", async () => {
  let n = 0;
  assert.equal(await core.pollUntil(async () => (++n === 3 ? "done" : null),
    { timeoutMs: 1000, intervalMs: 1, sleep: async () => {} }), "done");
});

test("read retries are bounded and never retry invalidation or cancel", async () => {
  let n = 0;
  await assert.rejects(core.withReadRetry(async () => { n++; throw new Error("rpc down"); },
    { delaysMs: [1, 1], sleep: async () => {} }), /rpc down/);
  assert.equal(n, 3);
  n = 0;
  await assert.rejects(core.withReadRetry(async () => { n++; throw new core.Invalidated("x"); },
    { delaysMs: [1, 1], sleep: async () => {} }), core.Invalidated);
  assert.equal(n, 1);
  n = 0;
  assert.equal(await core.withReadRetry(async () => (++n < 2 ? Promise.reject(new Error("blip")) : 7),
    { delaysMs: [1, 1], sleep: async () => {} }), 7);
});



test("consensus rule matches studionet_checks.py (via spike_checks.consensus_outcome) vectors", () => {
  const cases = [
    [["agree", "agree", "agree", "disagree", "disagree"], "MAJORITY_AGREE"],
    [["agree", "agree", "idle", "idle", "idle"], "TIMEOUT"],
    [["agree", "agree", "disagree", "disagree"], "NO_MAJORITY"],
    [["disagree", "disagree"], "NO_MAJORITY"],
    [["agree", "agree", "agree", "idle", "idle"], "MAJORITY_AGREE"],
  ];
  for (const [v, e] of cases) assert.equal(core.consensusOutcome(v, 5), e, JSON.stringify(v));
});


// ---- Studionet-specific ----------------------------------------------------
// ---- keccak / EIP-55 ---------------------------------------------------------
test("keccak256 known-answer vectors", () => {
  const h = (s) => core.bytesToHex(core.keccak256(new TextEncoder().encode(s)));
  assert.equal(h(""), "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470");
  assert.equal(h("abc"), "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45");
  assert.equal(h("a".repeat(135)).length, 66);          // one byte short of a block
  assert.equal(h("a".repeat(136)).length, 66);          // exactly one block: padding adds a block
  assert.notEqual(h("a".repeat(135)), h("a".repeat(136)));
});

test("EIP-55 specification vectors", () => {
  for (const a of ["0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
                   "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB", "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb"]) {
    assert.equal(core.toChecksumAddress(a.toLowerCase()), a);
    assert.equal(core.toChecksumAddress(a), a);
    assert.equal(core.toChecksumAddress("0x" + a.slice(2).toUpperCase()), a);
  }
  assert.equal(core.toChecksumAddress("0xcc88888f2eed5d8e457e6753faae64941dc33092"), "0xcC88888f2eeD5D8e457e6753FAaE64941dc33092");
});

test("EIP-55 rejects malformed or wrongly-checksummed input", () => {
  for (const bad of ["0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD", "5aaeb6053f3e94c9b9a09f33669435e7ef1beaed",
                     "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beae", "0xZZaeb6053f3e94c9b9a09f33669435e7ef1beaed", 42])
    assert.throws(() => core.toChecksumAddress(bad));
});

test("constants: chain 61999, pinned consensus, v5 selector = keccak of the v5 signature", () => {
  assert.equal(core.CHAIN_ID, 61999);
  assert.equal(core.CHAIN_HEX, "0xf22f");
  assert.equal(core.CONSENSUS_ADDRESS, "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575");
  const sel = core.bytesToHex(core.keccak256(new TextEncoder().encode("addTransaction(address,address,uint256,uint256,bytes)"))).slice(0, 10);
  assert.equal(core.ADD_TX_V5_SELECTOR, sel);
});

test("execution result name follows Studio rc.7's rule", () => {
  assert.equal(core.executionResultName(null), "NOT_VOTED");
  assert.equal(core.executionResultName({ leader_receipt: [{ execution_result: "SUCCESS" }] }), "FINISHED_WITH_RETURN");
  assert.equal(core.executionResultName({ leader_receipt: { execution_result: "ERROR" } }), "FINISHED_WITH_ERROR");
  assert.equal(core.executionResultName({ leader_receipt: [{ execution_result: "SUCCESS",
    genvm_result: { error_code: "CONSENSUS_LEADER_EXEC_TIMEOUT" } }] }), "TIMEOUT");
});

test("decodeAddTransaction refuses other selectors and trailing data", () => {
  const word = (h) => h.replace(/^0x/, "").padStart(64, "0");
  const body = word("a1".repeat(20)) + word("c0".repeat(20)) + word("5") + word("3") + word((32 * 5).toString(16)) + word("2") + "abcd".padEnd(64, "0");
  const ok = core.decodeAddTransaction(core.ADD_TX_V5_SELECTOR + body);
  assert.equal(ok.glRecipient, "0x" + "c0".repeat(20));
  assert.equal(core.bytesToHex(ok.txCalldata), "0xabcd");
  assert.throws(() => core.decodeAddTransaction("0xe71d5196" + body), /v5/);
  assert.throws(() => core.decodeAddTransaction(core.ADD_TX_V5_SELECTOR + body + "00".repeat(32)), /trailing/);
});
