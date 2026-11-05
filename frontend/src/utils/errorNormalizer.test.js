import { describe, expect, it } from 'vitest';
import { normalizeError } from './errorNormalizer';

describe('errorNormalizer', () => {
  it('handles structured detail with code, message_en, message_km (English)', () => {
    const err = {
      detail: {
        code: 'DAILY_QUOTA_EXCEEDED',
        message_en: 'Daily quota of 60 minutes reached for this account.',
        message_km: 'កូតាប្រចាំថ្ងៃ 60 នាទីត្រូវបានប្រើប្រាស់អស់ហើយ។',
      },
    };
    const res = normalizeError(err, true);
    expect(res).toBe('[DAILY_QUOTA_EXCEEDED] Daily quota of 60 minutes reached for this account.');
    expect(typeof res).toBe('string');
  });

  it('handles structured detail with code, message_en, message_km (Khmer)', () => {
    const err = {
      detail: {
        code: 'DAILY_QUOTA_EXCEEDED',
        message_en: 'Daily quota of 60 minutes reached for this account.',
        message_km: 'កូតាប្រចាំថ្ងៃ 60 នាទីត្រូវបានប្រើប្រាស់អស់ហើយ។',
      },
    };
    const res = normalizeError(err, false);
    expect(res).toBe('[DAILY_QUOTA_EXCEEDED] កូតាប្រចាំថ្ងៃ 60 នាទីត្រូវបានប្រើប្រាស់អស់ហើយ។');
    expect(typeof res).toBe('string');
  });

  it('handles Pydantic 422 validation error arrays', () => {
    const err = {
      detail: [
        { loc: ['body', 'source_lang'], msg: 'Input should be a valid string' },
        { loc: ['body', 'file'], msg: 'Field required' },
      ],
    };
    const res = normalizeError(err, true);
    expect(res).toContain('[VALIDATION_ERROR]');
    expect(res).toContain('source_lang: Input should be a valid string');
    expect(res).toContain('file: Field required');
    expect(typeof res).toBe('string');
  });

  it('handles global exception handler { code, message }', () => {
    const err = {
      code: 'INTERNAL_SERVER_ERROR',
      message: 'An internal server error occurred. Please contact the administrator.',
    };
    const res = normalizeError(err, true);
    expect(res).toBe('[INTERNAL_SERVER_ERROR] An internal server error occurred. Please contact the administrator.');
  });

  it('handles plain string error', () => {
    const res = normalizeError('File size exceeds upload limit', true);
    expect(res).toBe('File size exceeds upload limit');
  });

  it('handles JSON string error', () => {
    const raw = JSON.stringify({
      detail: { code: 'DURATION_TOO_LONG', message_en: 'Exceeds duration limit' },
    });
    const res = normalizeError(raw, true);
    expect(res).toBe('[DURATION_TOO_LONG] Exceeds duration limit');
  });

  it('handles HTML 502/504 error pages gracefully without leaking HTML tags', () => {
    const html502 = '<html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center></body></html>';
    const res = normalizeError(html502, true);
    expect(res).toContain('[SERVER_UNAVAILABLE]');
    expect(res).not.toContain('<html>');

    const html504 = '<!DOCTYPE html><html><body>504 Gateway Timeout</body></html>';
    const res504 = normalizeError(html504, true);
    expect(res504).toContain('[GATEWAY_TIMEOUT]');
  });

  it('handles JavaScript Error instances (network failure)', () => {
    const netErr = new TypeError('Failed to fetch');
    const res = normalizeError(netErr, true);
    expect(res).toContain('[CONNECTION_FAILED]');
  });

  it('handles AbortError cleanly', () => {
    const abortErr = new DOMException('The user aborted a request.', 'AbortError');
    const res = normalizeError(abortErr, true);
    expect(res).toBe('Operation was cancelled.');
  });

  it('handles null, undefined, and empty objects with fallback', () => {
    expect(normalizeError(null, true)).toContain('unexpected error');
    expect(normalizeError(undefined, false)).toContain('មានបញ្ហាមិនរំពឹងទុក');
    expect(normalizeError({}, true, 'Custom fallback')).toBe('Custom fallback');
  });

  it('guarantees result is NEVER an object or array', () => {
    const testCases = [
      { a: 1, b: [2, 3] },
      [{ message: 'bad' }],
      { detail: { nested: { deep: true } } },
      new Set([1, 2]),
      () => {},
    ];
    for (const tc of testCases) {
      const out = normalizeError(tc, true);
      expect(typeof out).toBe('string');
      expect(out.length).toBeGreaterThan(0);
    }
  });
});
