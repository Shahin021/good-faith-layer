import * as sdk from './genlayer-js-1.1.8.js';
import * as core from './sn-core.js';
const button=document.getElementById('refresh'), target=document.getElementById('live');
const rpc=core.createRpc({url:'https://studio.genlayer.com/api',timeoutMs:12000});
const client=sdk.createClient({chain:sdk.chains.studionet});
button.addEventListener('click',async()=>{
 button.disabled=true;target.textContent='Reading Studionet…';
 try{
 const [rr,ll]=await Promise.all([fetch('./report.json'),fetch('./wallet-log.json')]);
 if(!rr.ok||!ll.ok)throw Error('Evidence files unavailable');
 const report=await rr.json(),logs=await ll.json(),s=report.state;
 if(parseInt(await rpc('eth_chainId',[]),16)!==61999)throw Error('Unexpected chain');
 const read=(functionName,args=[])=>core.withTimeout(client.readContract({address:s.contract,functionName,args}),12000,functionName);
 const calls=[['accept',s.accept.hash,'accept_payment', [s.payment_id,s.policy_id,logs.find(e=>e.event==='pending_saved'&&e.method==='accept_payment').args[2]]],['claim',report.claim_hash,'open_claim',[s.payment_id]]];
 const states=await Promise.all([read('get_status',[s.payment_id]),read('get_verdict',[s.payment_id]),read('get_paid_out',[s.payment_id]),read('get_claim_balance',[s.wallet]),read('get_protection_pool')]);
 const txs=await Promise.all(calls.map(async([label,hash,method,args])=>{
 const raw=await rpc('eth_getTransactionByHash',[hash]);
 if(!raw)throw Error(label+' record missing');
 const summary=core.summarizeRecord(raw,{hash,contract:s.contract,sender:s.wallet,method,args,sdk});
 return {label,status:summary.status,consensus:summary.outcome,execution:summary.execution,problems:summary.problems};
 }));
 target.textContent=JSON.stringify({checked_at:new Date().toISOString(),status:states[0],verdict:states[1],paid_out:states[2],claim_balance:states[3],protection_pool:states[4],transactions:txs},(_,v)=>typeof v==='bigint'?v.toString():v,2);
 }catch(e){target.textContent='Live check unavailable: '+e.message+'\nThe saved execution report is still available above.';}
 finally{button.disabled=false;}
});
