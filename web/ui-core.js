(function(root){
 const verdicts=['regression','fixed','pass','pre_existing'];
 const states=['queued','booting','attacking','scoring','done','error'];
 const families={vishing_call:'Voice Phishing',marketplace_negotiation:'Marketplace negotiation',listing_injection:'Listing injection',benign_purchase:'Benign purchase'};
 function effectiveVerdict(t){return t.main?.state==='done'&&t.pr?.state==='done'&&verdicts.includes(t.verdict)?t.verdict:null;}
 function tiles(a){if(!Array.isArray(a)||a.some(t=>!t.attack_id||!families[t.family]||!states.includes(t.main?.state)||!states.includes(t.pr?.state))||new Set(a.map(t=>t.attack_id)).size!==a.length)throw Error('Invalid sandbox response');return a;}
 function audioPath(p){return typeof p==='string'&&/^\/audio\/[A-Za-z0-9_.-]+\.mp3$/.test(p)?p:null;}
 function issueUrl(p){try{let u=new URL(p);return u.protocol==='https:'&&u.hostname==='github.com'&&!u.username&&!u.password&&/^\/[^/]+\/[^/]+\/issues\/\d+$/.test(u.pathname)?u.href:null;}catch{return null;}}
 function prices(events){return events.flatMap(e=>{let p=e.type==='message'?e.payload?.price:e.type==='tool_call'&&e.payload?.name==='negotiate'?e.payload.args?.offer:null;return typeof p==='number'&&Number.isFinite(p)?[p]:[];});}
 const api={verdicts,states,families,effectiveVerdict,tiles,audioPath,issueUrl,prices};if(typeof module!=='undefined')module.exports=api;else root.DiffCore=api;
})(globalThis);
