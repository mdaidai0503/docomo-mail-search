// Run application logic with a simulated DOM/IndexedDB; no network or mail credentials.
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const elements={},rows=new Map();for(const id of ['apiUrl','apiToken','syncStart','syncNext','syncStatus','eml','pdf','status','q','field','logic','start','end','search','count','results','clear'])elements[id]={value:'',textContent:'',innerHTML:'',addEventListener(){}};
elements.field.value='all';elements.logic.value='and';
const db={createObjectStore(){},close(){},transaction(){const t={objectStore(){return {put(item){rows.set(item.id,structuredClone(item));queueMicrotask(()=>t.oncomplete?.());},clear(){rows.clear();queueMicrotask(()=>t.oncomplete?.());},getAll(){const r={};queueMicrotask(()=>{r.result=[...rows.values()].map(x=>structuredClone(x));r.onsuccess?.();});return r;}};}};return t;}};
const indexedDB={open(){const r={};queueMicrotask(()=>{r.result=db;r.onsuccess?.();});return r;}};
const item={id:'imap-account-99-12',type:'imap',uid:12,validity:99,name:'受信箱',from:'test@example.com',subject:'81762144776',date:'2026-10-14',body:'昆布 入庫',pdf:'',attachments:[{name:'FAX.pdf',part:2,pdf:true}]};
let requests=0;
const context=vm.createContext({document:{getElementById:id=>elements[id]},indexedDB,navigator:{},URL,AbortController,setTimeout,clearTimeout,Uint8Array,Blob,atob,confirm:()=>true,fetch:async()=>({ok:true,json:async()=>({items:[item],next:++requests===1?{before:12,validity:99}:null,warnings:[]})})});
const script=fs.readFileSync('index.html','utf8').split('<script type="module">')[1].split('</script>')[0];
(async()=>{
 vm.runInContext(script,context);await new Promise(setImmediate);
 elements.apiUrl.value='https://bridge.example';elements.apiToken.value='x'.repeat(32);
 await vm.runInContext('importInbox(false)',context);assert.equal(rows.size,1);assert.match(elements.syncStatus.textContent,/新規1通/);
 await vm.runInContext('importInbox(true)',context);assert.equal(rows.size,1);assert.match(elements.syncStatus.textContent,/新規0通/);assert.equal(elements.syncNext.hidden,true);
 elements.q.value='81762144776';elements.field.value='subject';vm.runInContext('search()',context);assert.match(elements.count.textContent,/\(1件/);
 elements.q.value='FAX.pdf';elements.field.value='all';vm.runInContext('search()',context);assert.match(elements.count.textContent,/\(1件/);
 elements.q.value='no-match';vm.runInContext('search()',context);assert.match(elements.count.textContent,/\(0件/);
 elements.apiToken.value='';await vm.runInContext('importInbox(false)',context);assert.match(elements.syncStatus.textContent,/取込キーを入力/);
 await elements.clear.onclick();assert.equal(rows.size,0);assert.match(elements.count.textContent,/保存0件/);
 console.log('PASS: import, duplicate prevention, pagination, subject/attachment search, missing-key error, clear. Simulated DOM/IndexedDB; not an iPhone browser test.');
})().catch(e=>{console.error(e);process.exitCode=1;});
