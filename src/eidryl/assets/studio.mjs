import {validateModel, run} from './runtime.mjs';

export const IMPORT_BYTES = 4 * 1024 * 1024;
const own = (object, key) => Object.hasOwn(object, key);
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => plain(value) && Object.keys(value).length === keys.length && keys.every(key => own(value, key));
const json = value => JSON.stringify(value, null, 2);
const actionLabel = entry => Array.isArray(entry) && typeof entry[0] === 'string' ? entry[0] : 'Invalid action';
const compact = (value, limit = 6000) => {
  const valueText = json(value) ?? 'null';
  return valueText.length > limit ? valueText.slice(0, limit) + '\n… display truncated; the saved data remains complete' : valueText;
};

/** Byte presentation only: no text codec, target semantics or inferred meaning. */
export function bytePreview(value) {
  if (!exact(value, ['bytes']) || typeof value.bytes !== 'string' || value.bytes.length > IMPORT_BYTES) return null;
  const encoded = value.bytes;
  if (encoded.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) return null;
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const padding = encoded.endsWith('==') ? 2 : encoded.endsWith('=') ? 1 : 0;
  if ((padding === 2 && (alphabet.indexOf(encoded[encoded.length - 3]) & 15))
    || (padding === 1 && (alphabet.indexOf(encoded[encoded.length - 2]) & 3))) return null;
  const count = encoded.length / 4 * 3 - padding, preview = [];
  for (let offset = 0; offset < encoded.length && preview.length < 64; offset += 4) {
    const number = alphabet.indexOf(encoded[offset]) * 262144 + alphabet.indexOf(encoded[offset + 1]) * 4096
      + (encoded[offset + 2] === '=' ? 0 : alphabet.indexOf(encoded[offset + 2])) * 64
      + (encoded[offset + 3] === '=' ? 0 : alphabet.indexOf(encoded[offset + 3]));
    for (const byte of [(number >>> 16) & 255, (number >>> 8) & 255, number & 255]) {
      if (preview.length < Math.min(64, count)) preview.push(byte);
    }
  }
  return {count, hex: preview.map(byte => byte.toString(16).padStart(2, '0')).join(''), truncated: count > 64};
}

export function formatObservedValue(value) {
  const bytes = bytePreview(value);
  if (!bytes) return compact(value);
  return bytes.count + (bytes.count === 1 ? ' byte' : ' bytes') + ' · hex ' + (bytes.hex || '(empty)')
    + (bytes.truncated ? '\n… hex preview truncated after 64 bytes' : '') + '\n\nPacked JSON\n' + compact(value);
}

export function parseJSON(text) {
  if (typeof text !== 'string' || new TextEncoder().encode(text).length > IMPORT_BYTES) throw Error('JSON exceeds the 4 MiB studio input limit.');
  return JSON.parse(text);
}

export function defaultRequest(model) {
  return {model: model.factory, constructor: model.constructor_types.map(type => type === 'str' ? '' : type === 'list' ? [] : 0), actions: []};
}

/** Validation completes before the caller replaces its current model or inputs. */
export function prepareImport(text) {
  const value = parseJSON(text);
  if (plain(value) && value.schema === 'eidryl-session/1') {
    if (!exact(value, ['schema', 'model', 'request'])) throw Error('Invalid session fields. Expected schema, model and request.');
    validateModel(value.model);
    const result = run(value.model, value.request);
    if (!result.ok) throw Error('Invalid session request: ' + result.error);
    return {model: value.model, request: value.request, result};
  }
  validateModel(value);
  const request = defaultRequest(value);
  const result = run(value, request);
  return {model: value, request, result: result.ok ? result : null};
}

export function encodeSession(model, request) {
  validateModel(model);
  const result = run(model, request);
  if (!result.ok) throw Error('Cannot save an invalid request: ' + result.error);
  const content = json({schema: 'eidryl-session/1', model, request}) + '\n';
  if (new TextEncoder().encode(content).length > IMPORT_BYTES) throw Error('This session exceeds the 4 MiB import/export limit. Save the model with the CLI instead.');
  return content;
}

export function describeExpression(expression, fields = []) {
  if (!Array.isArray(expression)) return '?';
  const [op, ...args] = expression;
  if (op === 'const') return JSON.stringify(args[0]);
  if (op === 's') return 'state[' + JSON.stringify(fields[args[0]] ?? args[0]) + ']';
  if (op === 'a') return 'args[' + args[0] + ']';
  if (op === 'c') return 'constructor[' + args[0] + ']';
  return op + '(' + args.map(value => describeExpression(value, fields)).join(', ') + ')';
}

export function describeRule(model, rule) {
  if (!rule) return 'No rule learned for this action in the previous mode.';
  const expression = value => describeExpression(value, model.fields);
  return 'accept if ' + expression(rule.guard) + '\n\nreturn ' + expression(rule.return)
    + '\n\nnext mode = ' + expression(rule.mode) + '\n\n'
    + rule.state.map((value, index) => model.fields[index] + ' = ' + expression(value)).join('\n');
}

export function recordedSummary(receipt) {
  if (!plain(receipt) || receipt.schema !== 'eidryl-check/1') return null;
  const count = key => Number.isSafeInteger(receipt[key]) && receipt[key] >= 0 ? receipt[key] : 'unknown';
  return {reported_passed: typeof receipt.passed === 'boolean' ? receipt.passed : 'unknown', requested_histories: count('requested_histories'),
    completed_histories: count('completed_histories'), transitions: count('transitions'),
    mismatches: receipt.mismatches, charged_operations: count('charged_operations'),
    model_sha256: typeof receipt.model_sha256 === 'string' ? receipt.model_sha256 : 'not supplied',
    scope: receipt.scope ?? 'not supplied', refusal: receipt.refusal ?? null};
}

export function createStudio(document, environment = globalThis) {
  const element = id => document.getElementById(id);
  const ids = ['model-name', 'action-count', 'field-count', 'mode-count', 'import-file', 'download-model', 'status',
    'constructor', 'actions', 'action-name', 'action-args', 'append-action', 'action-signature', 'run', 'restore',
    'download-session', 'prediction-label', 'prediction-count', 'timeline', 'step-caption', 'step-title',
    'mode-strip', 'step-result', 'state-fields', 'rules-panel', 'rule-context', 'rule-code',
    'evidence-badge', 'evidence-description', 'evidence-content'];
  const ui = Object.fromEntries(ids.map(id => [id, element(id)]));
  if (Object.values(ui).some(value => !value)) throw Error('Incomplete studio document.');
  const state = {model: null, request: null, baseline: null, result: null, selected: 0, recorded: null, importVersion: 0};
  function node(tag, className, content) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (content !== undefined) result.textContent = String(content);
    return result;
  }
  function status(message, error = false) {
    ui.status.textContent = message;
    ui.status.classList.toggle('error', error);
  }
  function clear(element) { element.replaceChildren(); }
  function recordEvidence() {
    const summary = recordedSummary(state.recorded);
    ui['evidence-badge'].textContent = summary ? 'Unsigned · recorded' : 'No receipt';
    ui['evidence-description'].textContent = summary
      ? 'This file records comparisons made by the CLI at an earlier time. Its counts and model hash are unsigned claims in the supplied receipt. Browser edits do not run or refresh these comparisons.'
      : 'No native oracle runs in this studio. Importing a model or session clears the previous recorded receipt.';
    ui['evidence-content'].textContent = summary ? compact(summary, 6000) : '';
  }
  function signature() {
    const types = state.model?.surface[ui['action-name'].value];
    ui['action-signature'].textContent = types ? 'Declared arguments: ' + (types.length ? types.join(', ') : 'none') : 'Choose a declared action.';
  }
  function configureModel() {
    const model = state.model;
    ui['model-name'].textContent = model.factory;
    ui['action-count'].textContent = Object.keys(model.surface).length;
    ui['field-count'].textContent = model.fields.length;
    ui['mode-count'].textContent = model.latent_modes;
    const memoryNote = document.getElementById('memory-note');
    if (memoryNote) memoryNote.textContent = model.latent_modes > 1
      ? 'This model distinguishes ' + model.latent_modes + ' hidden modes. Equal visible observations can belong to different modes and lead to different next outputs.'
      : 'This model uses one inferred mode. New browser predictions do not test the original component.';
    clear(ui['action-name']);
    for (const name of Object.keys(model.surface)) {
      const option = node('option', '', name); option.value = name; ui['action-name'].append(option);
    }
    ui['action-name'].value = Object.keys(model.surface)[0];
    signature();
    ui.run.disabled = ui.restore.disabled = ui['download-model'].disabled = false;
  }
  function writeInputs(request) {
    ui.constructor.value = json(request.constructor);
    ui.actions.value = json(request.actions);
  }
  function updateTimeline() {
    clear(ui.timeline);
    if (!state.result) { ui.timeline.append(node('p', 'empty', 'Predict a history to explore its transitions.')); return; }
    const steps = [{title: 'Constructor', meta: 'Initial state'}, ...state.result.trace.map((row, index) => ({
      title: actionLabel(state.request.actions[index]), meta: row.accepted ? 'Mode ' + row.before_mode + ' → ' + row.mode : 'Refused · unchanged', refused: !row.accepted
    }))];
    steps.forEach((step, index) => {
      const button = node('button', 'step-button' + (index === state.selected ? ' active' : '') + (step.refused ? ' refused' : ''));
      button.type = 'button'; button.setAttribute('aria-current', index === state.selected ? 'step' : 'false');
      button.setAttribute('aria-label', 'Step ' + index + ': ' + step.title + ', ' + step.meta);
      const label = node('span', 'step-label', step.title); label.append(node('span', 'step-meta', step.meta));
      button.append(node('span', 'step-number', index), label);
      button.addEventListener('click', () => { state.selected = index; renderPrediction(); });
      ui.timeline.append(button);
    });
  }
  function renderPrediction() {
    updateTimeline();
    clear(ui['mode-strip']); clear(ui['step-result']); clear(ui['state-fields']);
    ui['download-session'].disabled = !state.result;
    if (!state.result) {
      ui['prediction-label'].textContent = state.model ? 'Inputs ready · prediction required' : 'Awaiting a model';
      ui['prediction-count'].textContent = '';
      ui['step-title'].textContent = 'Start with a history';
      ui['step-caption'].textContent = 'Selected step';
      ui['rule-code'].textContent = ''; ui['rule-context'].textContent = '';
      ui['rules-panel'].hidden = true;
      return;
    }
    const {model, request, result, selected} = state;
    const refusals = result.trace.filter(row => !row.accepted).length;
    ui['prediction-label'].textContent = 'Browser prediction · no native oracle';
    ui['prediction-count'].textContent = result.trace.length + ' actions · ' + refusals + ' refused';
    ui['step-caption'].textContent = 'Step ' + selected + ' / ' + result.trace.length;
    const row = selected ? result.trace[selected - 1] : null;
    const mode = row ? row.mode : result.initial_mode;
    const values = row ? row.state : result.initial;
    const previous = selected > 1 ? result.trace[selected - 2].state : result.initial;
    ui['step-title'].textContent = row ? actionLabel(request.actions[selected - 1]) : 'Constructor';
    ui['mode-strip'].append(node('span', 'mode-caption', 'Inferred hidden mode' + (row ? ' · ' + row.before_mode + ' → ' + row.mode : ' · initial')));
    for (let index = 0; index < model.latent_modes; index++) ui['mode-strip'].append(node('span', 'mode-pill' + (index === mode ? ' selected' : ''), 'Mode ' + index));
    ui['step-result'].classList.toggle('refused', Boolean(row && !row.accepted));
    if (row) {
      ui['step-result'].append(node('strong', '', row.accepted ? 'Predicted return' : 'Action refused; state unchanged'));
      ui['step-result'].append(node('pre', '', row.accepted ? formatObservedValue(row.value) : row.reason));
    } else ui['step-result'].append(node('span', '', 'Initial observations predicted from the constructor arguments.'));
    model.fields.forEach((name, index) => {
      const changed = Boolean(row && JSON.stringify(values[index]) !== JSON.stringify(previous[index]));
      const field = node('div', 'field' + (changed ? ' changed' : ''));
      field.append(node('span', 'field-name', name), node('span', 'field-value', formatObservedValue(values[index])));
      ui['state-fields'].append(field);
    });
    ui['rules-panel'].hidden = false;
    if (row) {
      const action = actionLabel(request.actions[selected - 1]);
      const modes = own(model.rules, action) ? model.rules[action] : null;
      const rule = modes && own(modes, String(row.before_mode)) ? modes[String(row.before_mode)] : null;
      ui['rule-context'].textContent = rule
        ? 'Mode ' + row.before_mode + ' · ' + rule.examples + ' successful training observations · ' + rule.invalid_examples + ' invalid observations. Variables refer to the state before this action.'
        : 'This action has no learned rule in its previous mode.';
      ui['rule-code'].textContent = compactRule(describeRule(model, rule));
    } else {
      ui['rule-context'].textContent = 'Inferred initial state. Constructor types are declared inputs, not learned signatures.';
      ui['rule-code'].textContent = compactRule('accept if ' + describeExpression(model.constructor_guard, model.fields) + '\n\n'
        + model.initial.map((value, index) => model.fields[index] + ' = ' + describeExpression(value, model.fields)).join('\n'));
    }
  }
  function compactRule(value) { return value.length > 16000 ? value.slice(0, 16000) + '\n… rule display truncated; save the model for the full expression' : value; }
  function commit(prepared, receipt = null) {
    state.model = prepared.model; state.request = prepared.request; state.baseline = JSON.parse(JSON.stringify(prepared.request));
    state.result = prepared.result; state.selected = 0; state.recorded = receipt;
    configureModel(); writeInputs(prepared.request); recordEvidence(); renderPrediction();
  }
  function readInputs() {
    if (!state.model) throw Error('Load a model first.');
    return {model: state.model.factory, constructor: parseJSON(ui.constructor.value), actions: parseJSON(ui.actions.value)};
  }
  function predict() {
    try {
      const request = readInputs();
      const result = run(state.model, request);
      if (!result.ok) throw Error(result.error);
      state.request = request; state.result = result; state.selected = result.trace.length ? 1 : 0;
      renderPrediction(); status('Prediction ready. These results come from the learned model, not a new native comparison.');
      return result;
    } catch (error) {
      state.result = null; state.selected = 0; renderPrediction(); status('Prediction refused: ' + error.message, true);
      return null;
    }
  }
  function dirty() {
    state.result = null; state.selected = 0; renderPrediction();
    status('Inputs changed. Predict again to inspect this history. Recorded native evidence has not changed.');
  }
  function loadSession(text) {
    const prepared = prepareImport(text);
    commit(prepared);
    status('Imported data validated. The previous native receipt was cleared; predictions are model-only.');
    return prepared;
  }
  function download(content, filename) {
    if (new TextEncoder().encode(content).length > IMPORT_BYTES) throw Error('Download exceeds the 4 MiB studio import/export limit. Use the CLI for a larger model.');
    const url = environment.URL.createObjectURL(new environment.Blob([content], {type: 'application/json'}));
    const link = node('a'); link.href = url; link.download = filename;
    document.body.append(link);
    try { link.click(); } finally { link.remove(); environment.setTimeout(() => environment.URL.revokeObjectURL(url), 1000); }
  }
  ui.run.addEventListener('click', predict);
  ui.constructor.addEventListener('input', dirty); ui.actions.addEventListener('input', dirty);
  ui['action-name'].addEventListener('change', signature);
  ui.restore.addEventListener('click', () => { if (state.baseline) { writeInputs(state.baseline); dirty(); } });
  ui['append-action'].addEventListener('click', () => {
    try {
      const actions = parseJSON(ui.actions.value), args = parseJSON(ui['action-args'].value);
      if (!Array.isArray(actions) || !Array.isArray(args)) throw Error('History and arguments must both be JSON arrays.');
      if (actions.length >= 512) throw Error('The history already contains 512 actions.');
      ui.actions.value = json([...actions, [ui['action-name'].value, args]]); dirty();
    } catch (error) { status('Action was not appended: ' + error.message, true); }
  });
  ui['import-file'].addEventListener('change', async () => {
    const file = ui['import-file'].files?.[0]; if (!file) return;
    const version = ++state.importVersion;
    try {
      if (file.size > IMPORT_BYTES) throw Error('Import exceeds the 4 MiB studio limit.');
      const text = await file.text();
      if (version !== state.importVersion) return;
      loadSession(text);
    } catch (error) { if (version === state.importVersion) status('Import rejected; previous session preserved: ' + error.message, true); }
    finally { if (version === state.importVersion) ui['import-file'].value = ''; }
  });
  ui['download-model'].addEventListener('click', () => {
    try { if (state.model) download(json(state.model) + '\n', 'eidryl-model.json'); }
    catch (error) { status('Model was not saved: ' + error.message, true); }
  });
  ui['download-session'].addEventListener('click', () => {
    try { if (!state.result) throw Error('Predict the current history before saving.'); download(encodeSession(state.model, state.request), 'eidryl-session.json'); }
    catch (error) { status('Session was not saved: ' + error.message, true); }
  });
  async function fetchJSON(path, required) {
    try {
      const response = await environment.fetch(path);
      if (!response.ok) throw Error('HTTP ' + response.status);
      const length = response.headers?.get('content-length');
      if (length && Number(length) > IMPORT_BYTES) throw Error('File exceeds 4 MiB studio input limit.');
      return parseJSON(await response.text());
    } catch (error) { if (required) throw Error(path + ': ' + error.message); return null; }
  }
  async function boot() {
    try {
      const [model, request, receipt] = await Promise.all([fetchJSON('./model.json', true), fetchJSON('./request.json', true), fetchJSON('./recorded.json', false)]);
      if (state.model) return;
      validateModel(model); const result = run(model, request);
      if (!result.ok) throw Error('Initial request: ' + result.error);
      commit({model, request, result}, receipt);
      status('Portable model loaded. Edit a history or select a step; all displayed predictions execute the local interpreter.');
    } catch (error) { if (!state.model) status('Could not load the bundled session: ' + error.message + '. You can import a model or session JSON file.', true); }
  }
  return {boot, predict, loadSession, getSession: () => state.model ? {schema: 'eidryl-session/1', model: state.model, request: state.request} : null};
}

if (typeof document !== 'undefined') createStudio(document).boot();
