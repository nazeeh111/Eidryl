import assert from 'node:assert/strict';
import {createStudio, prepareImport, encodeSession, parseJSON, describeRule, recordedSummary, bytePreview, formatObservedValue, IMPORT_BYTES} from '../src/eidryl/assets/studio.mjs';

// This deliberately small DOM double verifies controller behavior, not layout,
// browser downloads or full DOM compatibility. Those require an actual browser.
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.events = {}; this.attributes = {}; this.value = ''; this.disabled = false; this.files = [];
    this.classes = new Set(); this.classList = {toggle: (name, force) => force ? this.classes.add(name) : this.classes.delete(name)}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text ?? '') + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, fn) { this.events[name] = fn; }
  async fire(name) { await this.events[name]?.({target: this}); }
  click() { this.clicked = true; this.events.click?.(); }
  remove() { this.removed = true; }
}
function controller() {
  const elements = Object.create(null), body = new Element('body'), downloads = [], urls = new Map();
  const document = {body, createElement: tag => new Element(tag), getElementById: id => elements[id] ??= new Element()};
  const environment = {Blob, URL: {createObjectURL: blob => { const url = 'blob:fake-' + urls.size; urls.set(url, blob); downloads.push(blob); return url; }, revokeObjectURL: url => urls.delete(url)},
    setTimeout: fn => fn(), fetch: async () => { throw Error('Unexpected network'); }};
  return {studio: createStudio(document, environment), elements, downloads, body, environment};
}
const model = {
  schema: 'data-transducer/1', factory: 'controller-fixture', constructor_types: ['int'], constructor_guard: ['const', true],
  fields: ['count'], initial: [['c', 0]], initial_mode: 0, surface: {increase: ['int']}, latent_modes: 1,
  latent_evidence: null, training_records: 3,
  rules: {increase: {'0': {return: ['add', ['s', 0], ['a', 0]], state: [['add', ['s', 0], ['a', 0]]],
    mode: ['const', 0], guard: ['const', true], examples: 3, invalid_examples: 0}}}
};
const request = {model: model.factory, constructor: [10], actions: [['increase', [3]], ['increase', [4]]]};
const serialized = encodeSession(model, request);
let checks = 0;
function test(name, fn) { return Promise.resolve().then(fn).then(() => { checks++; process.stdout.write('PASS ' + name + '\n'); }); }

await test('session serialization roundtrip executes actual runtime', () => {
  const prepared = prepareImport(serialized);
  assert.equal(prepared.result.trace[1].value, 17);
  assert.deepEqual(prepared.request, request);
});
await test('standalone model has usable typed constructor defaults', () => {
  assert.deepEqual(prepareImport(JSON.stringify(model)).request.constructor, [0]);
});
await test('input byte cap measures UTF-8 rather than code units', () => {
  assert.throws(() => parseJSON(JSON.stringify('🌋'.repeat(IMPORT_BYTES / 3))), /4 MiB/);
});
await test('session extra fields and invalid identity are rejected before replacement', () => {
  const {studio} = controller(); studio.loadSession(serialized);
  const before = JSON.stringify(studio.getSession());
  assert.throws(() => studio.loadSession(JSON.stringify({...JSON.parse(serialized), receipt: {passed: true}})), /Invalid session fields/);
  assert.throws(() => studio.loadSession(JSON.stringify({schema: 'eidryl-session/1', model, request: {...request, model: 'wrong'}})), /identity mismatch/);
  assert.equal(JSON.stringify(studio.getSession()), before);
});
await test('dirty inputs revoke prediction and session download; real execution follows edits', async () => {
  const {studio, elements} = controller(); studio.loadSession(serialized);
  elements.constructor.value = '[20]'; await elements.constructor.fire('input');
  assert.equal(elements['download-session'].disabled, true);
  assert.equal(elements['prediction-label'].textContent, 'Inputs ready · prediction required');
  const result = studio.predict(); assert.equal(result.trace[1].value, 27);
  assert.equal(elements['download-session'].disabled, false);
});
await test('native receipt is not refreshed or carried into an imported session', async () => {
  const {studio, elements, environment} = controller();
  environment.fetch = async path => ({ok: true, headers: {get: () => null}, text: async () => JSON.stringify(path.includes('model') ? model : path.includes('request') ? request : {
    schema: 'eidryl-check/1', passed: true, transitions: 7, completed_histories: 2, requested_histories: 2, charged_operations: 99, mismatches: [], model_sha256: 'unsigned', scope: 'test fixture'
  })});
  await studio.boot();
  assert.equal(elements['evidence-badge'].textContent, 'Unsigned · recorded');
  const before = elements['evidence-content'].textContent;
  elements.constructor.value = '[20]'; await elements.constructor.fire('input'); studio.predict();
  assert.equal(elements['evidence-content'].textContent, before);
  studio.loadSession(serialized); assert.equal(elements['evidence-content'].textContent, '');
});
await test('malformed action records remain inspectable explicit refusals', () => {
  const {studio, elements} = controller();
  const malformed = {...request, actions: [null, [], ['increase', [true]], ['increase', [4]]]};
  studio.loadSession(JSON.stringify({schema: 'eidryl-session/1', model, request: malformed}));
  const result = studio.predict(); assert.equal(result.trace.length, 4); assert.equal(result.trace[0].accepted, false);
  assert.equal(elements['step-title'].textContent, 'Invalid action');
  assert.equal(result.trace[3].value, 14);
});
await test('imported strings are text, never interpreted markup', () => {
  const {studio, elements} = controller();
  const hostile = JSON.parse(JSON.stringify(model)); hostile.factory = '<img src=x onerror=alert(1)>'; hostile.fields = ['<script>bad()</script>'];
  studio.loadSession(JSON.stringify(hostile));
  assert.equal(elements['model-name'].textContent, hostile.factory);
  assert.equal(elements['state-fields'].children[0].children[0].textContent, hostile.fields[0]);
});
await test('session download creates actual JSON Blob that imports with identical output', async () => {
  const {studio, elements, downloads, body} = controller(); studio.loadSession(serialized);
  await elements['download-session'].fire('click');
  assert.equal(downloads.length, 1); const text = await downloads[0].text();
  assert.deepEqual(prepareImport(text).result.trace.map(row => row.value), [13, 17]);
  assert.equal(body.children[0].download, 'eidryl-session.json'); assert.equal(body.children[0].clicked, true); assert.equal(body.children[0].removed, true);
});
await test('invalid asynchronous import preserves model and valid inputs', async () => {
  const {studio, elements} = controller(); studio.loadSession(serialized);
  elements['import-file'].files = [{size: 4, text: async () => 'null'}];
  await elements['import-file'].fire('change');
  assert.deepEqual(studio.getSession().request, request);
  assert.match(elements.status.textContent, /previous session preserved/);
});
await test('newer import wins an overlapping older read', async () => {
  const {studio, elements} = controller(); studio.loadSession(serialized);
  let complete; const old = new Promise(resolve => complete = resolve);
  elements['import-file'].files = [{size: serialized.length, text: () => old}]; const pending = elements['import-file'].fire('change');
  const newer = encodeSession(model, {...request, constructor: [77]});
  elements['import-file'].files = [{size: newer.length, text: async () => newer}]; await elements['import-file'].fire('change');
  complete(serialized); await pending;
  assert.deepEqual(studio.getSession().request.constructor, [77]);
});
await test('initial failed fetch permits later local import', async () => {
  const {studio, elements} = controller(); await studio.boot(); assert.match(elements.status.textContent, /Could not load/);
  studio.loadSession(serialized); assert.equal(elements['model-name'].textContent, model.factory);
});
await test('model rules are inspectable and recorded counts are labelled reported', () => {
  assert.match(describeRule(model, model.rules.increase['0']), /state\["count"\]/);
  assert.equal(recordedSummary({schema: 'something-else', passed: true}), null);
  assert.equal(recordedSummary({schema: 'eidryl-check/1', passed: true}).transitions, 'unknown');
});
await test('action builder refuses bad JSON without changing the history', async () => {
  const {studio, elements} = controller(); studio.loadSession(serialized);
  const history = elements.actions.value; elements['action-args'].value = '{';
  await elements['append-action'].fire('click'); assert.equal(elements.actions.value, history);
  assert.match(elements.status.textContent, /not appended/);
  elements['action-args'].value = '[8]'; await elements['append-action'].fire('click');
  assert.equal(studio.predict().trace[2].value, 25);
});
await test('reset restores the imported history but requires a fresh prediction', async () => {
  const {studio, elements} = controller(); studio.loadSession(serialized);
  elements.constructor.value = '[50]'; studio.predict(); await elements.restore.fire('click');
  assert.equal(elements['download-session'].disabled, true);
  assert.deepEqual(JSON.parse(elements.constructor.value), [10]);
  assert.equal(studio.predict().trace[1].value, 17);
});
await test('model download roundtrips and dirty session cannot be saved through a direct event', async () => {
  const {studio, elements, downloads} = controller(); studio.loadSession(serialized);
  await elements['download-model'].fire('click');
  assert.deepEqual(prepareImport(await downloads[0].text()).model, model);
  await elements.actions.fire('input'); await elements['download-session'].fire('click');
  assert.equal(downloads.length, 1); assert.match(elements.status.textContent, /Predict the current history/);
});
await test('canonical bytes show exact hex and byte count without text decoding', () => {
  assert.deepEqual(bytePreview({bytes: 'QgA='}), {count: 2, hex: '4200', truncated: false});
  assert.deepEqual(bytePreview({bytes: '//5CAA=='}), {count: 4, hex: 'fffe4200', truncated: false});
  assert.deepEqual(bytePreview({bytes: ''}), {count: 0, hex: '', truncated: false});
  assert.match(formatObservedValue({bytes: 'QgA='}), /2 bytes · hex 4200[\s\S]*"bytes": "QgA="/);
});
await test('byte preview bounds hex output and rejects noncanonical or ambiguous shapes', () => {
  const long = {bytes: Buffer.alloc(100, 0xab).toString('base64')};
  assert.deepEqual(bytePreview(long), {count: 100, hex: 'ab'.repeat(64), truncated: true});
  assert.match(formatObservedValue(long), /preview truncated after 64 bytes/);
  assert.equal(bytePreview({bytes: Buffer.alloc(1000000, 0xab).toString('base64')}).hex, 'ab'.repeat(64));
  for (const value of [{bytes: 'QR=='}, {bytes: 'QgA'}, {bytes: 'QgA=\n'}, {bytes: 'QgA=', extra: true}, {bytes: 3}]) assert.equal(bytePreview(value), null);
});
process.stdout.write(checks + ' studio controller and data contract groups passed; no browser-layout claim.\n');
