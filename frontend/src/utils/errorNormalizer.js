/**
 * Robust Error Normalization Utility for Soundme AI
 *
 * Guarantees that any backend error (FastAPI, Pydantic, HTTP status codes,
 * reverse proxy HTML error pages, network drops, or runtime exceptions)
 * is converted into a safe, localized human-readable string.
 *
 * CRITICAL RULE: This utility NEVER returns a raw JavaScript object or array.
 * React 19 unmounts the component tree if an object is rendered into JSX text.
 */

/**
 * Normalizes any error input into a displayable string.
 *
 * @param {unknown} err - The error caught or response body received.
 * @param {boolean} [isEnglish=false] - Whether English localization is active (defaults to Khmer).
 * @param {string} [fallback] - Optional fallback message if no message can be extracted.
 * @returns {string} Safe human-readable string.
 */
export function normalizeError(err, isEnglish = false, fallback = '') {
  const defaultFallback = isEnglish
    ? 'An unexpected error occurred. Please try again.'
    : 'មានបញ្ហាមិនរំពឹងទុកបានកើតឡើង។ សូមព្យាយាមម្តងទៀត។';

  if (!err) {
    return fallback || defaultFallback;
  }

  // 1. If it's already a string
  if (typeof err === 'string') {
    const trimmed = err.trim();
    if (!trimmed) return fallback || defaultFallback;

    // Detect if the string is raw HTML (e.g., from Nginx/Caddy/Cloudflare 502/504)
    if (/<html|<!DOCTYPE|<body|<head/i.test(trimmed)) {
      if (/502\s*Bad\s*Gateway/i.test(trimmed)) {
        return isEnglish
          ? '[SERVER_UNAVAILABLE] Gateway error (502). Backend is temporarily unreachable.'
          : '[SERVER_UNAVAILABLE] ម៉ាស៊ីនមេមិនអាចទាក់ទងបាន (502)។ សូមពិនិត្យមើលម៉ាស៊ីនមេ។';
      }
      if (/504\s*Gateway\s*Timeout/i.test(trimmed)) {
        return isEnglish
          ? '[GATEWAY_TIMEOUT] Request timed out (504). Operation took too long.'
          : '[GATEWAY_TIMEOUT] ការស្នើសុំផុតកំណត់ពេលវេលា (504)។';
      }
      return isEnglish
        ? '[SERVER_ERROR] Server returned an unexpected HTML error page.'
        : '[SERVER_ERROR] ម៉ាស៊ីនមេបានបញ្ជូនទំព័រកំហុស HTML។';
    }

    // Try parsing as JSON string if it looks like JSON
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        const parsed = JSON.parse(trimmed);
        return normalizeError(parsed, isEnglish, fallback);
      } catch (_) {
        // Fall through to plain string
      }
    }

    return trimmed.slice(0, 500);
  }

  // 2. JavaScript Error instances
  if (err instanceof Error) {
    const msg = err.message || '';
    if (err.name === 'AbortError' || msg.includes('aborted')) {
      return isEnglish
        ? 'Operation was cancelled.'
        : 'ប្រតិបត្តិការត្រូវបានបោះបង់។';
    }
    if (msg.includes('Failed to fetch') || msg.includes('NetworkError') || msg.includes('net::ERR_')) {
      return isEnglish
        ? '[CONNECTION_FAILED] Unable to connect to backend server. Please verify the AI engine is running.'
        : '[CONNECTION_FAILED] មិនអាចតភ្ជាប់ទៅម៉ាស៊ីនមេបានទេ។ សូមពិនិត្យមើលថាតើម៉ាស៊ីនមេដំណើរការឬអត់។';
    }
    if (msg) {
      return normalizeError(msg, isEnglish, fallback);
    }
    return fallback || defaultFallback;
  }

  // 3. Object-based errors (FastAPI responses, Axios/Fetch bodies, custom errors)
  if (typeof err === 'object') {
    // 3a. FastAPI validation error: { detail: [ { loc: [...], msg: "..." } ] }
    if (Array.isArray(err.detail)) {
      const parts = err.detail
        .map((item) => {
          if (!item) return '';
          if (typeof item === 'string') return item;
          const field = Array.isArray(item.loc) ? item.loc.slice(-1)[0] : '';
          const m = item.msg || '';
          return field ? `${field}: ${m}` : m;
        })
        .filter(Boolean);
      if (parts.length > 0) {
        return `[VALIDATION_ERROR] ${parts.join('; ')}`.slice(0, 500);
      }
    }

    // 3b. Structured error inside detail: { detail: { code, message_en, message_km } }
    if (err.detail && typeof err.detail === 'object' && !Array.isArray(err.detail)) {
      return normalizeStructuredError(err.detail, isEnglish);
    }

    // 3c. Detail is a string: { detail: "..." }
    if (typeof err.detail === 'string') {
      return normalizeError(err.detail, isEnglish, fallback);
    }

    // 3d. Direct structured error: { code, message_en, message_km }
    if (err.code || err.message_en || err.message_km) {
      return normalizeStructuredError(err, isEnglish);
    }

    // 3e. Standard { code, message } from global exception handler
    if (typeof err.message === 'string') {
      const prefix = err.code ? `[${err.code}] ` : '';
      return `${prefix}${err.message}`.slice(0, 500);
    }

    // 3f. Generic { error: "..." } or { error: { code, message } }
    if (err.error) {
      return normalizeError(err.error, isEnglish, fallback);
    }

    // Fallback: extract any string value or safe JSON stringification
    try {
      const json = JSON.stringify(err);
      if (json && json !== '{}') {
        return json.slice(0, 300);
      }
    } catch (_) {
      // Ignored
    }
  }

  return fallback || defaultFallback;
}

/**
 * Extracts localized message from a structured error object.
 * Format: { code?: string, message_en?: string, message_km?: string, detail?: string }
 */
function normalizeStructuredError(obj, isEnglish) {
  const code = obj.code ? `[${obj.code}] ` : '';
  const enMsg = obj.message_en || obj.detail || obj.message;
  const kmMsg = obj.message_km || obj.message || obj.detail;

  if (isEnglish) {
    return `${code}${enMsg || kmMsg || 'Processing failed'}`.slice(0, 500);
  }
  return `${code}${kmMsg || enMsg || 'ដំណើរការបានបរាជ័យ'}`.slice(0, 500);
}

export default normalizeError;
