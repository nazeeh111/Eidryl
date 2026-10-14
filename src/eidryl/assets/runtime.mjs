/** Fixed interpreter for bounded data-transducer/1 models.
 * Elementary scalar/sequence operations and UTF codecs are supplied priors.
 * Models contain data only. No native target, evaluation or network is used.
 */
export const LIMITS = Object.freeze({inputNodes: 300000, inputUnits: 8388608,
  expressionNodes: 4096, expressionDepth: 64, modelExpressionNodes: 100000,
  actions: 512, inputSequence: 65536, sequence: 1048576, integer: 1000000,
  evaluationSteps: 250000, materializedUnits: 16777216, responseBytes: 8388608});
const own = (o,k) => Object.hasOwn(o,k);
const fail = message => { throw new Error(message); };
class RunBudgetError extends Error {}
function responseSize(value) { return new TextEncoder().encode(JSON.stringify(value)).length; }
const object = o => o !== null && typeof o === 'object' && !Array.isArray(o)
  && (Object.getPrototypeOf(o) === Object.prototype || Object.getPrototypeOf(o) === null);
function exact(o, keys) {
  return object(o) && Object.keys(o).length === keys.length && keys.every(k=>own(o,k));
}
function inspect(data) {
  let nodes=0, units=0;
  const active=new Set();
  function visit(v,depth) {
    if (++nodes>LIMITS.inputNodes || depth>100) fail('Input structure budget exceeded');
    if (typeof v==='string') units+=v.length;
    else if (v===null || typeof v==='boolean') return;
    else if (typeof v==='number') { if(!Number.isFinite(v)) fail('Only finite JSON numeric values are supported'); }
    else if (typeof v==='object') {
      if ((!Array.isArray(v)&&!object(v)) || active.has(v)) fail('Input must be acyclic plain JSON data');
      active.add(v);
      const keys=Reflect.ownKeys(v);
      if(Array.isArray(v) && (v.length>LIMITS.inputNodes || keys.length!==v.length+1)) fail('Arrays must be dense JSON arrays');
      for(const k of keys) {
        if(Array.isArray(v)&&k==='length') continue;
        if(typeof k!=='string') fail('Symbol properties are unsupported');
        const d=Object.getOwnPropertyDescriptor(v,k);
        if(!d || !own(d,'value') || !d.enumerable) fail('Input accessors and hidden properties are unsupported');
        if(Array.isArray(v)&&(!/^(0|[1-9]\d*)$/.test(k)||Number(k)>=v.length)) fail('Array properties are unsupported');
        units+=k.length; visit(d.value,depth+1);
      }
      active.delete(v);
    } else fail('Input must contain JSON values only');
    if(units>LIMITS.inputUnits) fail('Input text budget exceeded');
  }
  visit(data,0);
}
function text(v,max=LIMITS.sequence) {
  if(typeof v!=='string') return false;
  let count=0;
  for(let i=0;i<v.length;i++,count++) {
    const n=v.charCodeAt(i);
    if(n>=0xd800&&n<=0xdbff) {
      const next=v.charCodeAt(++i);
      if(!(next>=0xdc00&&next<=0xdfff)) return false;
    } else if(n>=0xdc00&&n<=0xdfff) return false;
    if(count>=max) return false;
  }
  return true;
}
function matches(v,t) {
  if(t==='str') return text(v,LIMITS.inputSequence);
  if(t==='int') return Number.isSafeInteger(v)&&Math.abs(v)<=LIMITS.integer;
  return t==='list'&&Array.isArray(v)&&v.length<=LIMITS.inputSequence&&v.every(x=>text(x,LIMITS.inputSequence));
}
const BASE64='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
function fromBase64(s) {
  if(typeof s!=='string' || s.length>Math.ceil(LIMITS.sequence/3)*4
    || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(s)) fail('Invalid bounded base64 bytes');
  const padding=s.endsWith('==')?2:s.endsWith('=')?1:0;
  if(padding===2&&(BASE64.indexOf(s[s.length-3])&15) || padding===1&&(BASE64.indexOf(s[s.length-2])&3)) fail('Noncanonical base64 bytes');
  const b=new Uint8Array(s.length/4*3-padding);
  for(let i=0,j=0;i<s.length;i+=4) {
    const n=BASE64.indexOf(s[i])*262144+BASE64.indexOf(s[i+1])*4096
      +(s[i+2]==='='?0:BASE64.indexOf(s[i+2]))*64+(s[i+3]==='='?0:BASE64.indexOf(s[i+3]));
    if(j<b.length)b[j++]=(n>>>16)&255;
    if(j<b.length)b[j++]=(n>>>8)&255;
    if(j<b.length)b[j++]=n&255;
  }
  return b;
}
function toBase64(b) {
  const chunks=[];
  for(let i=0;i<b.length;i+=3) {
    const n=b[i]*65536+(b[i+1]??0)*256+(b[i+2]??0);
    chunks.push(BASE64[n>>>18],BASE64[(n>>>12)&63],i+1<b.length?BASE64[(n>>>6)&63]:'=',i+2<b.length?BASE64[n&63]:'=');
  }
  return chunks.join('');
}
function unpack(v) { return exact(v,['bytes'])?fromBase64(v.bytes):Array.isArray(v)?v.map(unpack):v; }
function pack(v) { return v instanceof Uint8Array?{bytes:toBase64(v)}:Array.isArray(v)?v.map(pack):v; }
function constant(v) {
  if(v===null||typeof v==='boolean'||Number.isSafeInteger(v)) return true;
  if(typeof v==='string') return text(v);
  if(Array.isArray(v)) return v.length<=LIMITS.sequence && v.every(x=>text(x));
  if(exact(v,['bytes'])) { fromBase64(v.bytes);return true; }
  return false;
}
const arities=Object.freeze({len:1,at:2,add:2,sub:2,min:2,max:2,eq:2,lt:2,not:1,if:3,
  slice:3,concat:2,single:1,reverse:1,tail:2,mod:2,pad:2,encode:2});
export function validateModel(m) {
  inspect(m);
  const keys=['schema','constructor_types','constructor_guard','factory','fields','initial','initial_mode','surface','rules','latent_modes','latent_evidence','training_records'];
  const types=x=>Array.isArray(x)&&x.length<=8&&x.every(t=>['str','list','int'].includes(t));
  if(!exact(m,keys)||m.schema!=='data-transducer/1'||!text(m.factory,128)||!m.factory
    ||!Array.isArray(m.fields)||!m.fields.length||m.fields.length>32||new Set(m.fields).size!==m.fields.length
    ||!m.fields.every(x=>text(x,128)&&x.length>0)||!types(m.constructor_types)
    ||!Array.isArray(m.initial)||m.initial.length!==m.fields.length
    ||!Number.isSafeInteger(m.latent_modes)||m.latent_modes<1||m.latent_modes>4
    ||!Number.isSafeInteger(m.initial_mode)||m.initial_mode<0||m.initial_mode>=m.latent_modes
    ||!Number.isSafeInteger(m.training_records)||m.training_records<1
    ||!object(m.surface)||!object(m.rules)) fail('Invalid model schema');
  const names=Object.keys(m.surface);
  if(!names.length||names.length>64||names.some(n=>!text(n,128)||!n||!types(m.surface[n]))
    ||Object.keys(m.rules).some(n=>!own(m.surface,n))) fail('Invalid action surface');
  if(m.latent_evidence!==null) {
    const d=m.latent_evidence;
    if(!exact(d,['action','argument','byte_field','position_field','basis','signatures'])
      ||typeof d.action!=='string'||!own(m.surface,d.action)||!text(d.argument,LIMITS.inputSequence)
      ||!Number.isSafeInteger(d.byte_field)||d.byte_field<0||d.byte_field>=m.fields.length
      ||!Number.isSafeInteger(d.position_field)||d.position_field<0||d.position_field>=m.fields.length
      ||!['utf-8','utf-16-le','utf-16-be'].includes(d.basis)
      ||!Array.isArray(d.signatures)||d.signatures.length!==m.latent_modes
      ||!d.signatures.every(v=>exact(v,['bytes'])&&constant(v))) fail('Invalid latent evidence');
  }
  let aggregate=0;
  function expr(e,arity,states,count={n:0},depth=0) {
    if(!Array.isArray(e)||typeof e[0]!=='string'||++count.n>LIMITS.expressionNodes
      ||++aggregate>LIMITS.modelExpressionNodes||depth>LIMITS.expressionDepth) fail('Expression shape or budget exceeded');
    const op=e[0];
    if(op==='const') { if(e.length!==2||!constant(e[1])) fail('Invalid expression constant');return; }
    if(op==='s'||op==='a'||op==='c') {
      const bound=op==='s'?states:op==='a'?arity:m.constructor_types.length;
      if(e.length!==2||!Number.isSafeInteger(e[1])||e[1]<0||e[1]>=bound) fail('Invalid expression variable');return;
    }
    if(!own(arities,op)||e.length!==arities[op]+1) fail('Unsupported expression operation or arity');
    e.slice(1).forEach(v=>expr(v,arity,states,count,depth+1));
  }
  expr(m.constructor_guard,0,0);m.initial.forEach(e=>expr(e,0,0));
  for(const [action,modes] of Object.entries(m.rules)) {
    if(!object(modes)) fail('Invalid rule modes');
    for(const [mode,r] of Object.entries(modes)) {
      if(!/^(0|[1-3])$/.test(mode)||Number(mode)>=m.latent_modes
        ||!exact(r,['return','state','mode','guard','examples','invalid_examples'])
        ||!Array.isArray(r.state)||r.state.length!==m.fields.length
        ||!Number.isSafeInteger(r.examples)||r.examples<3
        ||!Number.isSafeInteger(r.invalid_examples)||r.invalid_examples<0) fail('Invalid rule schema');
      [r.return,r.mode,r.guard,...r.state].forEach(e=>expr(e,m.surface[action].length,m.fields.length));
    }
  }
  return true;
}
function truth(v) {
  if(v===null||v===false||v===0||v==='')return false;
  if(Array.isArray(v)||v instanceof Uint8Array)return v.length!==0;
  return true;
}
function equal(a,b) {
  if(typeof a!==typeof b || Array.isArray(a)!==Array.isArray(b)
    ||(a instanceof Uint8Array)!==(b instanceof Uint8Array))return false;
  if(Array.isArray(a)||a instanceof Uint8Array)return a.length===b.length&&a.every((v,i)=>equal(v,b[i]));
  return a===b;
}
function integer(v) { if(!Number.isSafeInteger(v))fail('Operation requires a safe integer');return v; }
function sequence(v) { if(typeof v==='string'||Array.isArray(v)||v instanceof Uint8Array)return v;fail('Operation requires a sequence'); }
function units(v) { return typeof v==='string'?Array.from(v):sequence(v); }
function restore(template,u) { return typeof template==='string'?u.join(''):template instanceof Uint8Array?new Uint8Array(u):Array.from(u); }
function bounded(v,budget) {
  if(Number.isSafeInteger(v)||v===null||typeof v==='boolean')return v;
  if(typeof v==='number')fail('Numeric result outside safe integral bounds');
  if(typeof v==='string') {
    if(!text(v))fail('String result outside Unicode or length bounds');
    budget.units+=v.length;
  } else if(Array.isArray(v)||v instanceof Uint8Array) {
    if(v.length>LIMITS.sequence)fail('Sequence result exceeds length bound');
    budget.units+=v.length;
    if(Array.isArray(v))v.forEach(x=>bounded(x,budget));
  } else fail('Unsupported interpreter value');
  if(budget.units>LIMITS.materializedUnits)throw new RunBudgetError('Materialized value budget exceeded');
  return v;
}
function encode(s,codec) {
  if(!text(s))fail('Encoding requires valid Unicode text');
  if(codec==='utf-8')return new TextEncoder().encode(s);
  if(codec!=='utf-16-le'&&codec!=='utf-16-be')fail('Unknown representation codec');
  if(s.length*2>LIMITS.sequence)fail('Encoded value exceeds length bound');
  const result=new Uint8Array(s.length*2),be=codec==='utf-16-be';
  for(let i=0;i<s.length;i++) { const n=s.charCodeAt(i);result[2*i]=be?n>>>8:n&255;result[2*i+1]=be?n&255:n>>>8; }
  return result;
}
function evaluate(e,r,b) {
  if(++b.steps>LIMITS.evaluationSteps)throw new RunBudgetError('Evaluation step budget exceeded');
  const op=e[0];
  if(op==='const')return bounded(unpack(e[1]),b);
  if(op==='s'||op==='a'||op==='c')return r[op][e[1]];
  if(op==='if')return evaluate(e[truth(evaluate(e[1],r,b))?2:3],r,b);
  const v=e.slice(1).map(x=>evaluate(x,r,b)); let out;
  switch(op) {
    case 'len':out=units(v[0]).length;break;
    case 'add':out=integer(v[0])+integer(v[1]);break;
    case 'sub':out=integer(v[0])-integer(v[1]);break;
    case 'min':out=Math.min(integer(v[0]),integer(v[1]));break;
    case 'max':out=Math.max(integer(v[0]),integer(v[1]));break;
    case 'eq':out=equal(v[0],v[1]);break;
    case 'lt':out=integer(v[0])<integer(v[1]);break;
    case 'not':out=!truth(v[0]);break;
    case 'at': {
      const u=units(v[0]),i=integer(v[1])<0?u.length+v[1]:v[1];
      if(i<0||i>=u.length)fail('Learned index outside bounds');out=u[i];break;
    }
    case 'slice':out=restore(v[0],units(v[0]).slice(integer(v[1]),integer(v[2])));break;
    case 'concat': {
      if(typeof v[0]!==typeof v[1]||Array.isArray(v[0])!==Array.isArray(v[1])
        ||(v[0] instanceof Uint8Array)!==(v[1] instanceof Uint8Array))fail('Concatenation requires matching sequence types');
      const a=units(v[0]),c=units(v[1]);
      if(a.length+c.length>LIMITS.sequence)fail('Concatenation length bound exceeded');
      const u=Array.from(a);for(const x of c)u.push(x);out=restore(v[0],u);break;
    }
    case 'single':out=[v[0]];break;
    case 'reverse':out=restore(v[0],Array.from(units(v[0])).reverse());break;
    case 'tail':out=restore(v[0],v[1]>0?units(v[0]).slice(-integer(v[1])):[]);break;
    case 'mod': {
      const a=integer(v[0]),c=integer(v[1]);
      if(!c)out=0;
      else {const rem=BigInt(a)%BigInt(c);out=Number(rem!==0n&&((rem<0n)!==(c<0))?rem+BigInt(c):rem);}
      break;
    }
    case 'pad': {
      sequence(v[0]);const n=Math.max(0,integer(v[1]));
      if(n>LIMITS.sequence)fail('Padding length bound exceeded');
      out=typeof v[0]==='string'?'\x00'.repeat(n):v[0] instanceof Uint8Array?new Uint8Array(n):Array(n).fill(null);break;
    }
    case 'encode':out=encode(v[0],v[1]);break;
    default:fail('Unsupported operation');
  }
  return bounded(out,b);
}
/** Run from the constructor for each request. Refused operations preserve state/mode. */
export function run(model,request) {
  try {
    validateModel(model);inspect(request);
    if(!exact(request,['model','constructor','actions'])||request.model!==model.factory)fail('Malformed request or model identity mismatch');
    const c=request.constructor;
    if(!Array.isArray(c)||c.length!==model.constructor_types.length||!c.every((v,i)=>matches(v,model.constructor_types[i])))fail('Unsupported constructor types or bounds');
    if(!Array.isArray(request.actions)||request.actions.length>LIMITS.actions)fail('Action budget exceeded');
    const budget={steps:0,units:0},row={s:[],a:[],c};
    if(evaluate(model.constructor_guard,row,budget)!==true)fail('Constructor outside learned domain');
    let s=model.initial.map(e=>evaluate(e,row,budget)),mode=model.initial_mode;
    const initial=pack(s),trace=[];
    let responseBytes=responseSize(initial)+256;
    if(responseBytes>LIMITS.responseBytes)throw new RunBudgetError('Response byte budget exceeded');
    function append(record) {
      responseBytes+=responseSize(record)+1;
      if(responseBytes>LIMITS.responseBytes)throw new RunBudgetError('Response byte budget exceeded');
      trace.push(record);
    }
    for(const entry of request.actions) {
      const before=pack(s),beforeMode=mode;
      try {
        if(!Array.isArray(entry)||entry.length!==2||typeof entry[0]!=='string'||!Array.isArray(entry[1])||!own(model.surface,entry[0]))fail('Unsupported action shape/name');
        const [action,a]=entry,types=model.surface[action];
        if(a.length!==types.length||!a.every((v,i)=>matches(v,types[i])))fail('Unsupported action/types/mode');
        const modes=own(model.rules,action)?model.rules[action]:null;
        if(!modes||!own(modes,String(mode)))fail('Unsupported action/types/mode');
        const rule=modes[String(mode)],r={s,a,c};
        if(evaluate(rule.guard,r,budget)!==true)fail('Outside learned successful domain');
        const value=evaluate(rule.return,r,budget),next=rule.state.map(e=>evaluate(e,r,budget)),nextMode=evaluate(rule.mode,r,budget);
        if(!Number.isSafeInteger(nextMode)||nextMode<0||nextMode>=model.latent_modes)fail('Invalid learned mode');
        // All next values are computed and checked before committing the transition.
        const packedValue=pack(value),packedNext=pack(next);
        append({accepted:true,value:packedValue,state:packedNext,before_mode:beforeMode,mode:nextMode});
        s=next;mode=nextMode;
      } catch(error) {
        if(error instanceof RunBudgetError)throw error;
        append({accepted:false,reason:String(error.message),state:before,before_mode:beforeMode,mode});
      }
    }
    return {ok:true,initial,initial_mode:model.initial_mode,trace,final_mode:mode};
  } catch(error) { return {ok:false,error:String(error.message)}; }
}
