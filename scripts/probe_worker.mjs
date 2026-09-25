/** Actual Python DO probe; provider/audio fixtures are explicitly synthetic. */
import fs from 'node:fs/promises';
const base=process.argv[2]||'http://127.0.0.1:8787';
const created=await fetch(base+'/api/session',{method:'POST',headers:{'X-Demo-Key':process.env.DEMO_ACCESS_KEY||''}});
if(!created.ok)throw Error('Session creation failed: '+created.status);
const session=await created.json();
const response=await fetch(`${base}/api/session/${session.id}/probe?token=${encodeURIComponent(session.token)}`,{signal:AbortSignal.timeout(60000)});
if(!response.ok)throw Error('DO probe failed: '+response.status);
const evidence=await response.json();
await fs.writeFile(new URL('../evidence/workerd-probe.json',import.meta.url),JSON.stringify(evidence,null,2)+'\n');
console.log(JSON.stringify({passed:evidence.passed,runtime:evidence.runtime_platform,tests:evidence.test_count,actual_voice_conversation:evidence.actual_voice_conversation}));
if(!evidence.passed)process.exitCode=1;
