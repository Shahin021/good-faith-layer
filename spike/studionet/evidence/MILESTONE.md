# Good Faith Layer: verified external-wallet flow on Studionet

## New progress

This update adds a separate Studionet deployment and an external-wallet sandbox.
MetaMask signed payment acceptance and claim opening, followed by normal validator
consensus and finalized transactions. The original contract, six proof deployments
and original verifier remain unchanged.

Public review page (available after the Pages deployment completes):
https://shahin021.github.io/good-faith-layer/studionet/

## Verified outcome

- Chain: GenLayer Studionet, 61999.
- Contract: `0xA218e344BB0691B0FFfD4938bF84cc21ff32Ec12`.
- Payment: `sn-20261013T202258Z`.
- Verdict and state: `PROTECTED`, `RESOLVED`.
- Payout and recipient claim balance: 1000 internal contract units.
- Acceptance: 5 AGREE votes. Claim: 3 AGREE, 2 IDLE, electorate 5.
- Attestation TTL: 140 seconds; acceptance age: 12 seconds.
- All nine verification groups passed; exit code 0.

The verifier binds each signed envelope to the chain, pinned consensus target,
recipient contract, wallet signer, exact method and arguments. It checks NORMAL
mode, zero value, execution success, majority, finality, source identity and live
contract state. Independent verification and the raw deploy/accept/claim records
are downloadable from the public page.

## Review and reproduction

- Public evidence page: `/studionet/` on the existing GitHub Pages site.
- Runnable kit: `spike/studionet/README.md`.
- Report: `spike/studionet/evidence/report.json`.
- Wallet log: `spike/studionet/evidence/wallet-log.json`.
- Runtime compatibility diff: `spike/studionet/contract/port.diff`.
- Port SHA256: `b6d7bb600370c8b8ab91bc4339206283ffb46e0006cd92a96ae0942ad9631d79`.

The public page is read-only and needs no wallet connection. Its optional live
check reads state and both wallet transaction records; it complements the saved
Python report and does not replace its full verification. To reproduce a new
interactive case, use the local wallet kit with your own operators and wallet.

## Scope and limitations

This demonstrates one successful protected scenario with live AI claim execution.
Amounts are internal ledger units, not real USDC transfers. Studionet is a demo
network with possible resets. Existing six proofs cover separate scenarios;
this update does not claim six new MetaMask runs or a permissionless operator
service. No eligibility or acceptance decision by GenLayer is claimed here.
