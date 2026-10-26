const token=process.env.OPENCLAW_GATEWAY_TOKEN;
if(!token){console.error("missing token env");process.exit(2);}
const {c:callGateway}=await import("USER_HOME/autodl-tmp/epidemic-openclaw/runtime/openclaw-npm/node_modules/openclaw/dist/call-DPn8NgHI.js");
const {i:names,r:modes}=await import("USER_HOME/autodl-tmp/epidemic-openclaw/runtime/openclaw-npm/node_modules/openclaw/dist/client-info-CcqJJIan.js");
const opts={token,mode:modes.BACKEND,clientName:names.GATEWAY_CLIENT,timeoutMs:10000,scopes:["operator.read","operator.write","operator.admin"]};
const listed=await callGateway({...opts,method:"device.pair.list",params:{}});
const pending=Array.isArray(listed?.pending)?listed.pending:[];
if(!pending.length){console.log(JSON.stringify({approved:false,reason:"no_pending"}));process.exit(3);}
const req=pending.toSorted((a,b)=>(b.ts??b.createdAtMs??0)-(a.ts??a.createdAtMs??0))[0];
await callGateway({...opts,method:"device.pair.approve",params:{requestId:req.requestId}});
console.log(JSON.stringify({approved:true}));
