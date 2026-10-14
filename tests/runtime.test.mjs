import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {validateModel,run,LIMITS} from '../src/eidryl/assets/runtime.mjs';
const clone=x=>JSON.parse(JSON.stringify(x));
const C=x=>['const',x];
const model=()=>({schema:'data-transducer/1',factory:'counter',constructor_types:['int'],constructor_guard:C(true),
 fields:['value'],initial:[['c',0]],initial_mode:0,surface:{increment:['int'],peek:[],divide:['int']},
 rules:{increment:{'0':{return:['add',['s',0],['a',0]],state:[['add',['s',0],['a',0]]],guard:C(true),mode:C(0),examples:3,invalid_examples:0}},
 peek:{'0':{return:['s',0],state:[['s',0]],guard:C(true),mode:C(0),examples:3,invalid_examples:0}},
 divide:{'0':{return:['mod',['s',0],['a',0]],state:[['s',0]],guard:C(true),mode:C(0),examples:3,invalid_examples:0}}},
 latent_modes:1,latent_evidence:null,training_records:9});
const request=(actions=[],constructor=[2])=>({model:'counter',constructor,actions});
function expression(expr,initial=0) {
 const m=model();m.constructor_types=[];m.initial=[C(initial)];m.surface.peek=[];
 m.rules.peek['0'].return=expr;
 const r=run(m,{model:'counter',constructor:[],actions:[['peek',[]]]});
 assert.equal(r.ok,true);return r.trace[0];
}
test('strict schemas and constructor numeric value semantics',()=>{
 assert.equal(validateModel(model()),true);
 assert.equal(run(model(),request([], [3.0])).ok,true);
 for(const v of [true,1.5,NaN,Infinity,1000001])assert.equal(run(model(),request([],[v])).ok,false);
 for(const mutate of [m=>m.extra=1,m=>m.fields=[],m=>m.initial=[],m=>m.initial_mode=1,
   m=>m.rules.peek['00']=m.rules.peek['0'],m=>m.rules.peek['0'].return=['splice',C('x'),C(0),C('a')],
   m=>m.rules.peek['0'].return=['rotate',C([]),C(1)],m=>m.rules.peek['0'].return=['c',1],
   m=>m.rules.peek['0'].return=C({bytes:'AB=='}),m=>m.rules.peek['0'].return=C('\ud800')]) {
   const m=model();mutate(m);assert.throws(()=>validateModel(m));assert.equal(run(m,request()).ok,false);
 }
 assert.equal(run(model(),{...request(),model:'other'}).ok,false);
 assert.equal(run(model(),{...request(),extra:true}).ok,false);
 assert.equal(run(model(),request(Array(513).fill(['peek',[]]))).ok,false);
});
test('malformed action pairs refuse without changing state or mode',()=>{
 const actions=[[['increment'],[100]],['increment',[100],'extra'],['increment',[true]],['increment',[1.2]],
 ['increment',[]],['increment',[1,2]],['toString',[]],['__proto__',[]],['increment',[5]],['peek',[]]];
 const r=run(model(),request(actions));assert.equal(r.ok,true);assert.equal(r.trace.length,actions.length);
 for(const row of r.trace.slice(0,8)) {assert.equal(row.accepted,false);assert.deepEqual(row.state,[2]);assert.equal(row.mode,0);}
 assert.deepEqual(r.trace[8],{accepted:true,value:7,state:[7],before_mode:0,mode:0});
 assert.equal(r.trace[9].value,7);
});
test('transactional evaluation and invalid mode refusal',()=>{
 const m=model();m.rules.increment['0'].mode=C(4);
 const r=run(m,request([['increment',[9]],['peek',[]]]));
 assert.equal(r.trace[0].accepted,false);assert.deepEqual(r.trace[0].state,[2]);assert.equal(r.trace[1].value,2);
 const b=model();b.rules.increment['0'].state=[['at',C([]),C(0)]];
 const z=run(b,request([['increment',[9]],['peek',[]]]));assert.equal(z.trace[0].accepted,false);assert.equal(z.trace[1].value,2);
});
test('elementary Unicode and Python-style sequence semantics',()=>{
 assert.equal(expression(['len',C('🙂é漢')]).value,4);
 assert.equal(expression(['slice',C('🙂é漢'),C(-3),C(-1)]).value,'é');
 assert.equal(expression(['at',C('🙂x'),C(-2)]).value,'🙂');
 assert.equal(expression(['at',C('🙂x'),C(-3)]).accepted,false);
 assert.equal(expression(['reverse',C('🙂é')]).value,'́e🙂');
 assert.equal(expression(['tail',C('abc'),C(-1)]).value,'');
 assert.equal(expression(['pad',C(''),C(-1)]).value,'');
 assert.equal(expression(['mod',C(-7),C(3)]).value,2);
 assert.equal(expression(['mod',C(7),C(-3)]).value,-2);
 assert.equal(expression(['mod',C(7),C(0)]).value,0);
 assert.equal(expression(['mod',C(9007199254740989),C(9007199254740991)]).value,9007199254740989);
 assert.equal(expression(['mod',C(-9007199254740989),C(9007199254740991)]).value,2);
 assert.equal(expression(['eq',C(true),C(1)]).value,false);
 assert.equal(expression(['not',C([])]).value,true);
 assert.equal(expression(['if',C([]),C('bad'),C('good')]).value,'good');
 assert.equal(expression(['add',C(true),C(1)]).accepted,false);
 assert.equal(expression(['add',C(Number.MAX_SAFE_INTEGER),C(1)]).accepted,false);
 assert.equal(expression(['concat',C('x'),C(['y'])]).accepted,false);
});
test('UTF codecs and strict base64 values without Buffer',()=>{
 const word='A🙂漢';
 for(const codec of ['utf-8','utf-16-le','utf-16-be']) {
   const actual=expression(['encode',C(word),C(codec)]).value.bytes;
   const expected=Buffer.from(word,codec==='utf-8'?'utf8':'utf16le');if(codec==='utf-16-be')expected.swap16();
   assert.equal(actual,expected.toString('base64'));
 }
 assert.deepEqual(expression(['concat',C({bytes:'AAE='}),C({bytes:'AgM='})]).value,{bytes:'AAECAw=='});
 assert.deepEqual(expression(['reverse',C({bytes:'AAECAw=='})]).value,{bytes:'AwIBAA=='});
 assert.deepEqual(expression(['pad',C({bytes:''}),C(3)]).value,{bytes:'AAAA'});
 assert.equal(expression(['encode',C('A'),C('unknown')]).accepted,false);
 for(const n of [0,1,2,3,254,255,256,257]) {
   const bytes=Uint8Array.from({length:n},(_,i)=>(i*47+19)%256);
   const value={bytes:Buffer.from(bytes).toString('base64')};assert.deepEqual(expression(C(value)).value,value);
 }
});
test('non-JSON accessors, cycles and sparse arrays reject before execution',()=>{
 let invoked=0;const m=model();Object.defineProperty(m,'factory',{enumerable:true,get(){invoked++;return 'counter';}});
 assert.equal(run(m,request()).ok,false);assert.equal(invoked,0);
 const r=request();r.actions.length=1;assert.equal(run(model(),r).ok,false);
 const c=request();c.actions=[c];assert.equal(run(model(),c).ok,false);
 const p=model();p.surface=Object.create({increment:['int']});assert.equal(run(p,request()).ok,false);
});
test('model depth, result length and cumulative response bounds',()=>{
 const m=model();let expr=C(true);for(let i=0;i<66;i++)expr=['not',expr];m.constructor_guard=expr;
 assert.throws(()=>validateModel(m));
 assert.equal(expression(['pad',C(''),C(LIMITS.sequence+1)]).accepted,false);
 const b=model();b.constructor_types=['str'];b.initial=[['c',0]];
 b.surface={peek:[]};b.rules={peek:{'0':{return:['s',0],state:[['s',0]],guard:C(true),mode:C(0),examples:3,invalid_examples:0}}};
 const r=run(b,{model:'counter',constructor:['🙂'.repeat(65000)],actions:Array(512).fill(['peek',[]])});
 assert.equal(r.ok,false);assert.match(r.error,/Response byte budget/);assert.equal(Object.hasOwn(r,'trace'),false);
});
// Public native receipts are unsigned recorded evidence, not an online oracle.
const here=path.dirname(fileURLToPath(import.meta.url));
test('all bundled model histories match complete recorded native states and types',()=>{
 let transitions=0;
 for(const name of ['text','sequence','encoded']) {
  const dir=path.resolve(here,'../examples');
  const m=JSON.parse(fs.readFileSync(path.join(dir,name+'.model.json'),'utf8'));
  const evidence=JSON.parse(fs.readFileSync(path.join(dir,name+'.check.json'),'utf8'));
  assert.equal(evidence.passed,true);
  for(const record of evidence.records) {
   const predicted=run(m,record.request),native=record.native;
   assert.equal(predicted.ok,true);
   assert.deepEqual(predicted.initial,native.initial);
   assert.equal(predicted.trace.length,native.trace.length);
   for(let i=0;i<native.trace.length;i++) {
    const a=predicted.trace[i],b=native.trace[i];transitions++;
    assert.equal(a.accepted,b.accepted);assert.deepEqual(a.state,b.state);
    if(b.accepted)assert.deepEqual(a.value,b.value);
   }
  }
 }
 assert.equal(transitions,36);
});

test('aggregate expression and cumulative execution budgets refuse whole runs',()=>{
 function tree(depth) {return depth?['add',tree(depth-1),tree(depth-1)]:C(0);}
 const m=model();m.rules.peek['0'].return=tree(11);
 const result=run(m,request(Array(512).fill(['peek',[]])));
 assert.equal(result.ok,false);assert.match(result.error,/Evaluation step budget/);assert.equal(Object.hasOwn(result,'trace'),false);
 const q=model();q.fields=Array.from({length:32},(_,i)=>'f'+i);q.initial=q.fields.map(()=>C(0));
 const t=tree(11);q.rules={peek:{'0':{return:t,state:q.fields.map(()=>t),guard:C(true),mode:C(0),examples:3,invalid_examples:0}}};
 assert.throws(()=>validateModel(q),/budget/);
});
