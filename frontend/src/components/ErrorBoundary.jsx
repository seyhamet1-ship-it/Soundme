import { Component } from 'react';

/**
 * Sanitizes error messages by stripping any sensitive authorization tokens,
 * query parameters, or bearer secrets so they never leak into UI or logs.
 */
function sanitizeErrorMessage(raw) {
  if (!raw) return 'Unknown error';
  return String(raw)
    .replace(/Bearer\s+[A-Za-z0-9_\-\.]+/gi, 'Bearer [REDACTED]')
    .replace(/[?&](token|access_token|key|secret)=[A-Za-z0-9_\-\.]+/gi, '$1=[REDACTED]')
    .replace(/("token"|"secret"|"key")\s*:\s*"[^"]+"/gi, '$1: "[REDACTED]"');
}

/**
 * React Error Boundary for Khmer Video Dubber
 *
 * Catches any uncaught render/lifecycle exception in the wrapped subtree and
 * displays a visible recovery UI instead of a blank (black) screen.
 *
 * React 18/19 behaviour: once the root tree throws without a boundary, the
 * entire DOM is unmounted and nothing is shown. This boundary prevents that.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, info: null };
    this._handleReset = this._handleReset.bind(this);
    this._lastResetTime = 0;
    this._retryCount = 0;
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    this.setState({ info });
    // Surface sanitized diagnostics so Electron devtools / log files capture the trace without leaking secrets
    const sanitizedMsg = sanitizeErrorMessage(error?.message || error);
    console.error('[ErrorBoundary] Uncaught render error:', sanitizedMsg, info?.componentStack || '');
  }

  _handleReset() {
    const now = Date.now();
    if (this._lastResetTime && (now - this._lastResetTime < 3000)) {
      this._retryCount = (this._retryCount || 0) + 1;
    } else {
      this._retryCount = 1;
    }
    this._lastResetTime = now;
    this.setState({ hasError: false, error: null, info: null });
  }

  render() {
    if (!this.state.hasError) return this.props.children;

    const rawMsg = this.state.error
      ? (this.state.error.message || this.state.error)
      : 'Unknown error';
    const msg = sanitizeErrorMessage(rawMsg);

    return (
      <div
        role="alert"
        aria-live="assertive"
        style={{
          position: 'fixed',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#020617',
          color: '#f8fafc',
          fontFamily: 'system-ui, sans-serif',
          padding: '2rem',
          textAlign: 'center',
          zIndex: 9999,
        }}
      >
        <div style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>⚠️</div>
        <h2 style={{ margin: '0 0 0.75rem', fontSize: '1.4rem', color: '#f87171' }}>
          Something went wrong
        </h2>
        <p style={{ margin: '0 0 0.5rem', color: '#94a3b8', fontSize: '0.95rem', maxWidth: '520px' }}>
          The interface encountered an unexpected error. Your upload or job may still be
          processing in the background.
        </p>
        <p style={{ margin: '0 0 1.5rem', color: '#64748b', fontSize: '0.8rem', fontFamily: 'monospace', maxWidth: '600px', wordBreak: 'break-word' }}>
          {msg}
        </p>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', justifyContent: 'center' }}>
          <button
            onClick={this._handleReset}
            style={{
              padding: '0.6rem 1.4rem',
              borderRadius: '8px',
              border: 'none',
              background: '#0284c7',
              color: '#fff',
              cursor: 'pointer',
              fontSize: '0.95rem',
              fontWeight: 600,
            }}
          >
            Try Again
          </button>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '0.6rem 1.4rem',
              borderRadius: '8px',
              border: '1px solid #334155',
              background: 'transparent',
              color: '#94a3b8',
              cursor: 'pointer',
              fontSize: '0.95rem',
            }}
          >
            Reload App
          </button>
        </div>
        <p style={{ marginTop: '2rem', color: '#475569', fontSize: '0.78rem' }}>
          ការងារដែលកំពុងដំណើរការនៅតែបន្ត · Jobs still in progress on the server
        </p>
      </div>
    );
  }
}
