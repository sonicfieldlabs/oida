/** Native Pi tools over the existing local Oida owner API. No provider credentials. */
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

export function localOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) || !url.port || url.username || url.password || url.search || url.hash || url.pathname !== '/')
    throw new Error('Oida Pi requires an explicit loopback HTTP origin');
  return url.origin;
}

export function register(pi, origin, transport = fetch) {
  origin = localOrigin(origin);
  async function call(path, body, signal) {
    const response = await transport(origin + path, {
      method: body === undefined ? 'GET' : 'POST', redirect: 'error',
      headers: {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(120000)]),
    });
    const reader = response.body.getReader(); let size = 0; const chunks = [];
    try {
      while (true) {
        const {done, value} = await reader.read(); if (done) break;
        size += value.length; if (size > 2 * 1024 * 1024) throw new Error('Oversized Oida response');
        chunks.push(value);
      }
    } finally { await reader.cancel(); }
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!response.ok) {
      const error = new Error(`Oida ${response.status}: ${JSON.stringify(value)}`);
      error.status = response.status; error.value = value; throw error;
    }
    return value;
  }
  const result = value => ({content: [{type: 'text', text: JSON.stringify(value)}], details: value});
  pi.registerTool({name: 'oida_capabilities', label: 'Oida capabilities',
    description: 'Read the local Oida owner capabilities. Does not start a daemon or model.',
    parameters: {type: 'object', properties: {}, additionalProperties: false},
    async execute(id, params, signal) { return result(await call('/gateway/capabilities', undefined, signal)); },
  });
  pi.registerTool({name: 'oida_listen', label: 'Listen with Oida',
    description: 'Listen to a user-authorized local file through Oida. Session only; no permanent memory. Reuse operation_id only for an exact retry. Never invent permission or a listening account.',
    parameters: {type: 'object', properties: {
      path: {type: 'string', minLength: 1}, operation_id: {type: 'string', pattern: '^[A-Za-z0-9_-]{1,80}$'},
      permission: {type: 'boolean', const: true},
    }, required: ['path', 'operation_id', 'permission'], additionalProperties: false},
    async execute(id, params, signal) {
      if (params.permission !== true || !/^[A-Za-z0-9_-]{1,80}$/.test(params.operation_id) || typeof params.path !== 'string' || !params.path)
        throw new Error('Explicit permission, local path and operation identity are required');
      if (signal?.aborted) throw new Error('Cancelled before dispatch');
      const operation = 'pi_' + createHash('sha256').update(params.operation_id).digest('hex');
      try {
        return result(await call('/gateway/listen', {path: params.path, operation_id: operation,
          remember: false, privacy_mode: 'incognito', ephemeral_delivery: true,
          raw_audio_policy: 'not_stored', route_preset: 'basic', source_type: 'file'}, signal));
      } catch (error) {
        if (error.status === 409 && error.value?.detail?.receipt)
          return result({status: "existing_operation", receipt: error.value.detail.receipt, report_available: false, note: "Owner refused duplicate dispatch; inspect the receipt. No result replay or payload equivalence is inferred."});
        // A lost response is uncertain execution. Fence commit; never resubmit automatically.
        try { await call(`/operations/${operation}/cancel`, {}, AbortSignal.timeout(5000)); } catch {}
        throw new Error(`Operation ${operation} is unconfirmed; inspect its receipt before retry: ${error.message}`);
      }
    },
  });
  pi.registerTool({name: 'oida_operation', label: 'Oida operation receipt',
    description: 'Inspect or explicitly cancel a prior Pi listening operation. No automatic retry.',
    parameters: {type: 'object', properties: {operation_id: {type: 'string', pattern: '^[A-Za-z0-9_-]{1,80}$'}, cancel: {type: 'boolean'}}, required: ['operation_id'], additionalProperties: false},
    async execute(id, params, signal) {
      if (!/^[A-Za-z0-9_-]{1,80}$/.test(params.operation_id)) throw new Error('Invalid operation identity');
      const operation = 'pi_' + createHash('sha256').update(params.operation_id).digest('hex');
      return result(await call(`/operations/${operation}${params.cancel ? '/cancel' : ''}`, params.cancel ? {} : undefined, signal));
    },
  });
}

export default function (pi) {
  const config = JSON.parse(readFileSync(new URL('./runtime.json', import.meta.url), 'utf8'));
  register(pi, config.origin);
}
