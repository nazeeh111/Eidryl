import {validateModel, run} from './runtime.mjs';
let input = '', size = 0;
const decoder = new TextDecoder('utf-8', {fatal: true});
try {
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > 12 * 1024 * 1024) throw Error('Input byte budget');
    input += decoder.decode(chunk, {stream: true});
  }
  input += decoder.decode();
  const record = JSON.parse(input);
  if (!record || typeof record !== 'object' || Array.isArray(record) || Object.keys(record).sort().join() !== 'model,requests') throw Error('Malformed batch');
  validateModel(record.model);
  if (record.requests !== null && (!Array.isArray(record.requests) || record.requests.length > 128)) throw Error('Request batch budget');
  let result = {valid: true};
  if (record.requests !== null) {
    let transitions = 0, outputBytes = 2;
    result = [];
    for (const request of record.requests) {
      transitions += Array.isArray(request?.actions) ? request.actions.length : 0;
      if (transitions > 4096) throw Error('Batch transition budget');
      const answer = run(record.model, request);
      outputBytes += new TextEncoder().encode(JSON.stringify(answer)).length + 1;
      if (outputBytes > 32 * 1024 * 1024) throw Error('Output byte budget');
      result.push(answer);
    }
  }
  const output = JSON.stringify(result);
  if (new TextEncoder().encode(output).length > 32 * 1024 * 1024) throw Error('Output byte budget');
  process.stdout.write(output);
} catch (error) {
  process.stderr.write(String(error.message).slice(0, 500));
  process.exitCode = 2;
}
