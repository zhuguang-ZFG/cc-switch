// Sanitized, self-contained cases. Historical contracts are reduced reproductions,
// not measurements on full production incidents. Oracles/holdouts stay outside
// the model workspace (not an OS security boundary against a hostile model).
const testHeader =
  "const test=require('node:test');const assert=require('node:assert/strict');const subject=require('./subject.cjs');\n";
export const cases = [
  {
    id: "pagination",
    provenance: { kind: "synthetic", source: "original OMP bugfix canary" },
    prompt:
      "Fix subject.cjs pagination. Pages are one-based; reject nonpositive or noninteger page/size with RangeError. Return a fresh array without mutating input.",
    buggy:
      "module.exports=(items,page,size)=>items.slice(page*size,(page+1)*size);\n",
    corrected:
      "module.exports=(items,page,size)=>{if(!Number.isInteger(page)||page<1||!Number.isInteger(size)||size<1)throw new RangeError('invalid pagination');return items.slice((page-1)*size,page*size);};\n",
    regression:
      testHeader +
      `test('first page',()=>assert.deepEqual(subject(['a','b','c','d'],1,2),['a','b']));
test('second page',()=>assert.deepEqual(subject(['a','b','c','d'],2,2),['c','d']));
test('invalid page',()=>assert.throws(()=>subject(['a'],0,2),RangeError));
test('invalid size',()=>assert.throws(()=>subject(['a'],1,0),RangeError));`,
    holdout:
      "const a=[1,2,3,4,5];assert.deepEqual(subject(a,3,2),[5]);assert.deepEqual(subject([],1,2),[]);assert.deepEqual(subject(a,99,2),[]);assert.throws(()=>subject(a,1.5,2));assert.throws(()=>subject(a,1,2.5));assert.deepEqual(a,[1,2,3,4,5]);assert.notStrictEqual(subject(a,1,9),a);",
  },
  {
    id: "empty-response",
    provenance: {
      kind: "reduced-regression",
      source:
        "scripts/ops/test_omp_model_routing_observability.js: canary classification",
    },
    prompt:
      "Fix subject.cjs canary success predicate. It returns true only for exit code zero, no timeout/abort, nonblank string stdout, and all three explicit boolean proofs: toolStarted, toolSucceeded, finalContainsNonce. Malformed/missing results or proof return false. Never mutate arguments.",
    buggy: "module.exports=(result,proof)=>result.code===0;\n",
    corrected:
      "module.exports=(r,p)=>!!r&&r.code===0&&!r.timedOut&&!r.aborted&&typeof r.stdout==='string'&&r.stdout.trim().length>0&&!!p&&p.toolStarted===true&&p.toolSucceeded===true&&p.finalContainsNonce===true;\n",
    regression:
      testHeader +
      `const proof={toolStarted:true,toolSucceeded:true,finalContainsNonce:true};
test('valid',()=>assert.equal(subject({code:0,stdout:'ok'},proof),true));
test('empty',()=>assert.equal(subject({code:0,stdout:''},proof),false));
test('missing tool proof',()=>assert.equal(subject({code:0,stdout:'ok'},{}),false));
test('timeout',()=>assert.equal(subject({code:0,stdout:'ok',timedOut:true},proof),false));`,
    holdout:
      "const p=Object.freeze({toolStarted:true,toolSucceeded:true,finalContainsNonce:true});const r=Object.freeze({code:0,stdout:'ok'});assert.equal(subject(r,p),true);for(const x of [null,{}, {code:0,stdout:'  \\n'}, {code:0,stdout:42},{...r,aborted:true},{...r,code:1}])assert.equal(subject(x,p),false);for(const k of Object.keys(p))assert.equal(subject(r,{...p,[k]:'true'}),false);assert.equal(subject(r,null),false);",
  },
  {
    id: "config-preservation",
    provenance: {
      kind: "reduced-regression",
      source:
        "scripts/ops/test_deploy_omp_verification.py: test_preserves_existing_config_and_exact_rollback",
    },
    prompt:
      "Fix subject.cjs addServer(config,name,entry). Return a new config preserving all existing top-level keys and other mcpServers entries. Add the requested server. If that server name already exists, throw Error without replacing it. Accept an absent mcpServers map. Never mutate config or its map. Inputs are JSON objects, name is a string; arbitrary names including __proto__ must be handled as own data properties.",
    buggy:
      "module.exports=(config,name,entry)=>({mcpServers:{[name]:entry}});\n",
    corrected:
      "module.exports=(c,n,e)=>{const m=c.mcpServers||{};if(Object.hasOwn(m,n))throw new Error('server already exists');return {...c,mcpServers:{...m,[n]:e}};};\n",
    regression:
      testHeader +
      `test('preserves metadata',()=>assert.equal(subject({version:1},'new',{}).version,1));
test('preserves other servers',()=>assert.deepEqual(subject({mcpServers:{other:{command:'old'}}},'new',{}).mcpServers.other,{command:'old'}));
test('refuses replacement',()=>assert.throws(()=>subject({mcpServers:{same:{}}},'same',{})));`,
    holdout:
      "const map=Object.freeze({other:Object.freeze({command:'old'})});const c=Object.freeze({mcpServers:map,disabledServers:['off'],unknown:{keep:true}});const r=subject(c,'new',{command:'new'});assert.notStrictEqual(r,c);assert.notStrictEqual(r.mcpServers,map);assert.deepEqual(c.mcpServers,{other:{command:'old'}});assert.deepEqual(r.disabledServers,['off']);assert.deepEqual(r.unknown,{keep:true});const p=subject({},'__proto__',{safe:true});assert.equal(Object.hasOwn(p.mcpServers,'__proto__'),true);assert.deepEqual(subject({},'constructor',{}).mcpServers.constructor,{});assert.throws(()=>subject({mcpServers:JSON.parse('{\"__proto__\":{}}')},'__proto__',{}));",
  },
];

export function summarizeReports(reports) {
  const live = reports.filter((r) => r.mode === "live");
  const costsKnown =
    live.length > 0 &&
    live.every(
      (r) => Number.isFinite(r.estimatedCostUsd) && r.estimatedCostUsd > 0,
    );
  return {
    attempts: live.length,
    successes: live.filter((r) => r.ok).length,
    successRate: live.length
      ? live.filter((r) => r.ok).length / live.length
      : null,
    meanDurationMs: live.length
      ? live.reduce((n, r) => n + r.durationMs, 0) / live.length
      : null,
    unintendedEditRate: live.length
      ? live.filter((r) => r.unintendedEdits.length > 0).length / live.length
      : null,
    costUsd: null,
    estimatedCostUsd: costsKnown
      ? live.reduce((n, r) => n + r.estimatedCostUsd, 0)
      : null,
    costStatus: "unavailable-no-trusted-billing-observation",
  };
}

export function observedUsage(result) {
  const unknown = { tokens: null, estimatedCostUsd: null };
  if (
    result.outputTruncated ||
    result.code !== 0 ||
    result.timedOut ||
    result.aborted
  )
    return unknown;
  const messages = [];
  for (const line of result.output.split(/\r?\n/)) {
    try {
      const event = JSON.parse(line);
      if (event.type === "message_end" && event.message?.role === "assistant")
        messages.push(event.message);
    } catch {
      /* Non-event diagnostics are not usage evidence. */
    }
  }
  if (
    !messages.length ||
    messages.some(
      (m) => !Number.isFinite(m.usage?.totalTokens) || m.usage.totalTokens < 0,
    )
  )
    return unknown;
  return {
    tokens: messages.reduce((n, m) => n + m.usage.totalTokens, 0),
    estimatedCostUsd: messages.every(
      (m) => Number.isFinite(m.usage?.cost?.total) && m.usage.cost.total > 0,
    )
      ? messages.reduce((n, m) => n + m.usage.cost.total, 0)
      : null,
  };
}
