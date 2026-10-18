// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import App from './App';

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

describe('Khmer dubbing studio', () => {
  it('renders the Khmer upload workflow and recent jobs', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => [] }));
    render(<App />);
    expect(screen.getByText('កម្មវិធីបកសំឡេងវីដេអូជាភាសាខ្មែរ')).toBeTruthy();
    expect(screen.getByText('ភាសាដើម')).toBeTruthy();
    expect(screen.getByText('ប្រវត្តិការងារ')).toBeTruthy();
  });
  it('switches to English', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => [] }));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));
    expect(screen.getByText('Khmer Video Dubber')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Choose or drop a video' })).toBeTruthy();
  });
  it('rejects a non-video file with an accessible error', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => [] }));
    render(<App />);
    fireEvent.change(screen.getByLabelText('Choose video'), { target: { files: [new File(['x'], 'notes.txt', { type: 'text/plain' })] } });
    expect(screen.getByRole('alert').textContent).toContain('Choose an MP4');
  });
  it('surfaces TTS_UNAVAILABLE job failures with an accessible error', async () => {
    localStorage.setItem('activeJob', 'job1');
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url).includes('/jobs/job1')) {
        return Promise.resolve({ json: async () => ({
          id: 'job1', status: 'failed', stage: 'failed', progress: 100,
          error: { code: 'TTS_UNAVAILABLE', message_en: 'TTS_UNAVAILABLE: Edge -> Azure -> MMS failed', message_km: 'មិនអាចបង្កើតសំឡេងខ្មែរបានទេ។' },
        }) });
      }
      return Promise.resolve({ json: async () => [] });
    }));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('[TTS_UNAVAILABLE]');
    expect(alert.textContent).toContain('Edge -> Azure -> MMS failed');
  });
  it('switches the job failure message to Khmer in Khmer mode', async () => {
    localStorage.setItem('activeJob', 'job1');
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url).includes('/jobs/job1')) {
        return Promise.resolve({ json: async () => ({
          id: 'job1', status: 'failed', stage: 'failed', progress: 100,
          error: { code: 'TTS_UNAVAILABLE', message_en: 'TTS_UNAVAILABLE: failed', message_km: 'មិនអាចបង្កើតសំឡេងខ្មែរបានទេ។' },
        }) });
      }
      return Promise.resolve({ json: async () => [] });
    }));
    render(<App />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('មិនអាចបង្កើតសំឡេងខ្មែរបានទេ។');
  });
});
