// Offline tests of genlayer-js 1.1.8 (the last pre-v0.6 release) on its
// studionet chain config, with an external (address-only) account and a fake
// EIP-1193 provider. No network: fetch is replaced, and nothing is submitted.
//
// They answer, from the real SDK code:
//   - which requests reach the wallet and which reach the RPC;
//   - whether a write needs a MetaMask Snap (it does not; connect() does);
//   - exactly what the wallet is asked to sign (target, chain, selector,
//     inner RLP, GenVM calldata key);
//   - whether one writeContract can ask the wallet to sign twice (it can).
// The signed envelope written to envelopes.json is signed with a throwaway key
// generated in memory, standing in for MetaMask's EIP-155 legacy signing, so
// the Python verifier can be run on an envelope of exactly this shape.
import test from "node:test";
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { createClient, abi } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import {
  decodeFunctionData, parseAbi, fromRlp, hexToBytes, toHex, keccak256,
  parseTransaction, recoverTransactionAddress, serializeTransaction,
} from "viem";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";

const SDK_CONSENSUS = "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575";
const CONTRACT = "0x1111111111111111111111111111111111111111";
const SNAP_METHODS = ["wallet_getSnaps", "wallet_requestSnaps", "wallet_invokeSnap", "wallet_snap"];
const V5 = parseAbi(["function addTransaction(address,address,uint256,uint256,bytes)"]);
const V6 = parseAbi(["function addTransaction(address,address,uint256,uint256,bytes,uint256)"]);

function harness({ sendErrors = [] } = {}) {
  const wallet = privateKeyToAccount(generatePrivateKey()); // in memory only
  const providerCalls = [];
  const rpcCalls = [];
  const signed = [];
  const errors = [...sendErrors];
  const provider = {
    async request({ method, params }) {
      providerCalls.push({ method, params });
      if (method === "eth_chainId") return "0xf22f";
      if (method === "eth_accounts" || method === "eth_requestAccounts") return [wallet.address];
      if (method === "eth_sendTransaction") {
        if (errors.length) throw new Error(errors.shift());
        const p = params[0];
        // What MetaMask does with this request: a legacy EIP-155 signature.
        const raw = await wallet.signTransaction({
          type: "legacy", to: p.to, data: p.data, value: BigInt(p.value),
          gas: BigInt(p.gas), gasPrice: BigInt(p.gasPrice ?? "0x0"),
          nonce: Number(BigInt(p.nonce)), chainId: Number(BigInt(p.chainId)),
        });
        signed.push({ request: p, raw });
        return keccak256(raw);
      }
      throw new Error(`unexpected wallet method ${method}`);
    },
  };
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    rpcCalls.push({ url, method: body.method, params: body.params });
    const result = {
      eth_getTransactionCount: "0x7",
      eth_estimateGas: "0x7a120",
      eth_gasPrice: "0x0",
      eth_chainId: "0xf22f",
    }[body.method];
    if (result === undefined) throw new Error(`unexpected RPC ${body.method}`);
    return new Response(JSON.stringify({ jsonrpc: "2.0", id: body.id, result }),
      { headers: { "content-type": "application/json" } });
  };
  const restore = () => { globalThis.fetch = realFetch; };
  const client = createClient({ chain: studionet, account: wallet.address, provider });
  return { wallet, client, providerCalls, rpcCalls, signed, restore };
}

function decodeSigned(raw) {
  const tx = parseTransaction(raw);
  let fn = null, args = null;
  for (const [name, a] of [["v5", V5], ["v6", V6]]) {
    try { ({ args } = decodeFunctionData({ abi: a, data: tx.data })); fn = name; break; } catch {}
  }
  const inner = fromRlp(args[4], "hex");
  return { tx, fn, args, inner, calldata: abi.calldata.decode(hexToBytes(inner[0])) };
}

const ACCEPT_ARGS = ["case-1", "gfl-standard-v1", "{\"x\":1}"];

test("studionet config pins chain 61999, the SDK consensus address and the v5 ABI", () => {
  assert.equal(studionet.id, 61999);
  assert.equal(studionet.isStudio, true);
  assert.equal(studionet.rpcUrls.default.http[0], "https://studio.genlayer.com/api");
  assert.equal(studionet.consensusMainContract.address, SDK_CONSENSUS);
  const add = studionet.consensusMainContract.abi.filter((e) => e.name === "addTransaction");
  assert.equal(add.length, 1);
  assert.equal(add[0].inputs.length, 5);
});

test("an external-account write uses one eth_sendTransaction and no Snap", async () => {
  const h = harness();
  try {
    const hash = await h.client.writeContract({
      address: CONTRACT, functionName: "accept_payment", args: ACCEPT_ARGS, value: 0n, leaderOnly: false,
    });
    const methods = h.providerCalls.map((c) => c.method);
    assert.deepEqual(methods.filter((m) => m === "eth_sendTransaction"), ["eth_sendTransaction"]);
    assert.ok(!methods.some((m) => SNAP_METHODS.includes(m)), methods.join(","));
    // isStudio: the SDK returns the EVM hash immediately and does not wait for a receipt
    assert.equal(hash, keccak256(h.signed[0].raw));
    assert.ok(!h.rpcCalls.some((c) => c.method === "eth_sendRawTransaction"), "SDK must not broadcast itself");
    assert.ok(!h.rpcCalls.some((c) => c.method === "eth_getTransactionReceipt"));
  } finally { h.restore(); }
});

test("the wallet is asked to sign exactly the expected call", async () => {
  const h = harness();
  try {
    await h.client.writeContract({
      address: CONTRACT, functionName: "accept_payment", args: ACCEPT_ARGS, value: 0n, leaderOnly: false,
    });
    const req = h.signed[0].request;
    assert.equal(req.to, SDK_CONSENSUS);
    assert.equal(req.chainId, "0xf22f");
    assert.equal(req.type, "0x0");
    assert.equal(req.value, "0x0");
    assert.equal(req.gasPrice, "0x0");
    assert.equal(req.from.toLowerCase(), h.wallet.address.toLowerCase());
    const d = decodeSigned(h.signed[0].raw);
    assert.equal(d.fn, "v5");
    assert.equal(d.tx.data.slice(0, 10), "0x27241a99");
    assert.equal(d.tx.chainId, 61999);
    assert.equal(d.args[0].toLowerCase(), h.wallet.address.toLowerCase());
    assert.equal(d.args[1].toLowerCase(), CONTRACT);
    assert.equal(d.args[2], 5n);
    assert.equal(d.args[3], 3n);
    assert.equal(d.inner.length, 2);
    // leader_only false is serialised as the single byte 0x00 (viem toRlp of
    // toHex(false) = "0x0"). Studio v0.121's parser has a custom Boolean sedes
    // that maps 0x00 to False (transactions_parser.py:29-59), i.e. NORMAL.
    assert.equal(d.inner[1], "0x00");
    // GenVM v0.2.16 calldata convention: key "method", not ""
    const cd = Object.fromEntries(d.calldata instanceof Map ? d.calldata : Object.entries(d.calldata));
    assert.deepEqual(Object.keys(cd).sort(), ["args", "method"]);
    assert.equal(cd.method, "accept_payment");
    assert.deepEqual(cd.args, ACCEPT_ARGS);
    const signer = await recoverTransactionAddress({ serializedTransaction: h.signed[0].raw });
    assert.equal(signer, h.wallet.address);
  } finally { h.restore(); }
});

test("leaderOnly:true is visible in the signed envelope (so a verifier can reject it)", async () => {
  const h = harness();
  try {
    await h.client.writeContract({
      address: CONTRACT, functionName: "open_claim", args: ["case-1"], value: 0n, leaderOnly: true,
    });
    assert.equal(decodeSigned(h.signed[0].raw).inner[1], "0x01");
  } finally { h.restore(); }
});

test("an ABI-mismatch error makes ONE writeContract ask the wallet to sign a second, different transaction", async () => {
  const h = harness({ sendErrors: ["could not decode result data"] });
  try {
    await h.client.writeContract({
      address: CONTRACT, functionName: "accept_payment", args: ACCEPT_ARGS, value: 0n, leaderOnly: false,
    });
    const sends = h.providerCalls.filter((c) => c.method === "eth_sendTransaction");
    assert.equal(sends.length, 2, "SDK retried the write with the fallback encoding");
    assert.equal(sends[0].params[0].data.slice(0, 10), "0x27241a99");
    assert.equal(sends[1].params[0].data.slice(0, 10), keccak256(new TextEncoder().encode(
      "addTransaction(address,address,uint256,uint256,bytes,uint256)")).slice(0, 10));
    assert.equal(sends[1].params[0].data.slice(0, 10), "0xe71d5196");
  } finally { h.restore(); }
});

test("connect() requires the GenLayer Snap (wallet_getSnaps / wallet_requestSnaps)", async () => {
  const calls = [];
  globalThis.window = {
    ethereum: {
      async request({ method }) {
        calls.push(method);
        if (method === "eth_chainId") return "0xf22f";
        if (method === "wallet_getSnaps") return {};
        if (method === "wallet_requestSnaps") throw new Error("user rejected Snap install");
        throw new Error(`unexpected ${method}`);
      },
    },
  };
  try {
    const client = createClient({ chain: studionet });
    await assert.rejects(client.connect("studionet"), /Snap/);
    assert.deepEqual(calls, ["eth_chainId", "wallet_getSnaps", "wallet_requestSnaps"]);
  } finally { delete globalThis.window; }
});

test("the SDK does not check the wallet's chain before signing on a Studio chain", async () => {
  const h = harness();
  try {
    await h.client.writeContract({
      address: CONTRACT, functionName: "accept_payment", args: ACCEPT_ARGS, value: 0n, leaderOnly: false,
    });
    // assertChainMatch() returns early when chain.isStudio; no eth_chainId reaches the wallet.
    assert.ok(!h.providerCalls.some((c) => c.method === "eth_chainId"));
  } finally { h.restore(); }
});

test("write fixture envelopes for the Python verifier", async () => {
  const out = [];
  let firstWallet = null;
  for (const [fn, args, leaderOnly] of [
    ["accept_payment", ACCEPT_ARGS, false],
    ["open_claim", ["case-1"], false],
    ["open_claim", ["case-1"], true],
  ]) {
    const h = harness();
    try {
      await h.client.writeContract({ address: CONTRACT, functionName: fn, args, value: 0n, leaderOnly });
      firstWallet ??= h.wallet;
      out.push({ fn, args, leaderOnly, signer: h.wallet.address, contract: CONTRACT, raw: h.signed[0].raw,
                 hash: keccak256(h.signed[0].raw) });
    } finally { h.restore(); }
  }
  // A wrong-chain envelope of the same shape (what a wallet on another chain would produce).
  const w = privateKeyToAccount(generatePrivateKey());
  const good = parseTransaction(out[0].raw);
  const raw = await w.signTransaction({ ...good, chainId: 61997, type: "legacy" });
  out.push({ fn: "accept_payment", args: ACCEPT_ARGS, leaderOnly: false, signer: w.address, contract: CONTRACT,
             raw, hash: keccak256(raw), wrongChain: true });
  // An envelope that also transfers EVM value (v5 carries value only at the EVM layer).
  // Signed by the same wallet the GenLayer sender names, so only the value differs.
  const raw2 = await firstWallet.signTransaction({ ...good, value: 5n, chainId: 61999, type: "legacy" });
  out.push({ fn: "accept_payment", args: ACCEPT_ARGS, leaderOnly: false, signer: firstWallet.address, contract: CONTRACT,
             raw: raw2, hash: keccak256(raw2), evmValue: 5 });
  writeFileSync(new URL("./envelopes.json", import.meta.url), JSON.stringify(out, null, 1));
  assert.equal(out.length, 5);
  void serializeTransaction;
});
