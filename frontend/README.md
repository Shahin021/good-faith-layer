# Good Faith Layer — frontend

A presentation page for the project with a live verifier that reads the six
hosted scenario deployments directly from the browser.

## Run it

This page **cannot be opened by double-clicking**. It loads `scenarios.json`
with `fetch` and imports the GenLayer SDK as an ES module, and browsers block
both on `file://`. Serve it over HTTP:

```
cd frontend
python3 -m http.server 8000
```

Then open `http://localhost:8000`.

For GitHub Pages, publish the full contents of the `frontend` directory at the root of the published branch.

## Files

```
index.html        the page, all CSS and JS inline
scenarios.json    the six hosted deployments and their expected outcomes
genlayer-logo.png
genlayer-cat.png
```

## Vendoring the SDK

The page tries `./vendor/genlayer-js.js` first and only falls back to esm.sh if
that file is absent. Produce it once and the CDN is never touched:

```
npm i genlayer-js@2.0.0-rc.1 esbuild
npx esbuild node_modules/genlayer-js/dist/index.js \
  --bundle --format=esm --platform=browser \
  --outfile=frontend/vendor/genlayer-js.js
```

The entry path may differ; check `main`/`module`/`exports` in
`node_modules/genlayer-js/package.json` if esbuild cannot resolve it.

Do not change the version. The bar above the verifier shows which source was
used, `local` or `esm.sh`, so you can confirm the vendored copy is the one
being loaded.

## How the live verification works

Reads go through the GenLayer read client, which needs no wallet:

```js
const { createClient } = await import("./vendor/genlayer-js.js");
const client = createClient({ chain });
await client.readContract({ address, functionName: "get_verdict", args: [paymentId] });
```

The underlying RPC method is `gen_call`. Its calldata is GenLayer's own
encoding rather than Ethereum ABI, which is why the SDK does the encoding and
why `ethers.js` would not work here.

Network: `studionet` / `studio_devnet`, chain id `61997`,
RPC `https://studio-dev.genlayer.com/api`. All of it lives in
`scenarios.json`, so pointing at a different endpoint means editing one file.

### Methods called

Taken from `contracts/good_faith_layer.py`, not assumed:

| method | signature | used for |
|---|---|---|
| `get_verdict` | `(payment_id: str) -> str` | decides the match |
| `get_paid_out` | `(payment_id: str) -> int` | decides the match |
| `get_status` | `(payment_id: str) -> str` | lifecycle state, single verify only |
| `get_claim_balance` | `(who: str) -> int` | recipient credit, single verify only |
| `get_protection_pool` | `() -> int` | pool balance, single verify only |

Only `get_verdict` and `get_paid_out` decide MATCH or MISMATCH, and a failure
on either fails the read. The three contextual getters are fetched separately
and each degrades to `unavailable` on its own without affecting the verdict.

Note the signature of `get_claim_balance`: it takes the **recipient address**,
not the payment id. Each scenario in `scenarios.json` carries its real
recipient address for that reason.

### Connection states

The indicator never claims a connection the page has not actually made.
Loading the SDK is not evidence that the endpoint is reachable, so it does not
produce *Connected*:

| state | means |
|---|---|
| `Loading SDK…` | the genlayer-js module is being fetched |
| `SDK ready` | the client exists, no RPC call has been made yet |
| `Connecting…` | a contract read is in flight |
| `Retrying… (n/3)` | a read failed and is being attempted again |
| `Connected` | **a real response came back from the hosted RPC** |
| `SDK unavailable` | the module failed to load |
| `RPC unavailable` | the read was attempted and failed |

### Retries

A hosted read is retried up to three times: immediately, then after 600 ms,
then after 1500 ms. **Only a thrown error triggers a retry.** A value that
comes back successfully is never retried or adjusted, whatever it says, so a
real difference from the expected value still shows as MISMATCH.

### Read volume

`Verify all 6` reads only the two fields the match depends on, one after the
other, one deployment at a time, with a 250 ms pause between deployments.
That is twelve sequential requests rather than thirty with bursts of five
concurrent calls, reducing burst load on the shared hosted endpoint.

`Verify this scenario` keeps the full five-field read, so status, claim
balance and protection pool are still visible on demand.

The summary only reads `6 / 6 live states match expected outcomes` when all
twelve required reads succeeded and all six scenarios matched.

There is no fallback to the expected values anywhere. If every attempt fails,
the live column shows the error and the stamp reads *Live verification
unavailable*.

## Verification checklist

1. Serve over HTTP and open the page.
2. The bar under *Verify it yourself* should reach **Connected**.
3. Select `01_protected`, press **Verify this scenario**. Expect
   `PROTECTED` / `1000` and a green MATCH.
4. Press **Verify all 6**. Expect `6 / 6 live states match expected outcomes`.
   The indicator should only read **Connected** after step 3 or 4, never
   before.
5. Check the `sdk` field in the bar reads `local` once you have vendored it.
6. Open devtools, block the RPC host, and verify the page retries three times
   and then reports the failure rather than showing the expected values.

## If the browser blocks the request

Two things can stop a browser-side read, and neither is visible from the code:

- **CORS.** If `studio-dev.genlayer.com` does not send
  `Access-Control-Allow-Origin`, the browser refuses the response and the
  page will show *Contract read failed*. The console will name CORS
  explicitly. A one-line check from any console:

  ```js
  fetch("https://studio-dev.genlayer.com/api",{method:"POST",
    headers:{"content-type":"application/json"},
    body:JSON.stringify({jsonrpc:"2.0",id:1,method:"sim_getFeeConfig",params:[]})
  }).then(r=>r.json()).then(console.log).catch(console.error)
  ```

- **The CDN build.** `esm.sh` bundles npm packages for the browser. If
  `genlayer-js@2.0.0-rc.1` pulls something that does not build there, the
  import fails and the page reports *SDK unavailable*. The fallback is to
  bundle the SDK locally with esbuild and import `./vendor/genlayer-js.js` instead.

The SDK fallback can be handled in `index.html`; CORS behavior depends on the RPC endpoint and hosting environment.
