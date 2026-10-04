export type Bitrate = 128 | 192 | 320;
export type Stage = 'queued' | 'inspecting' | 'downloading' | 'converting' | 'ready' | 'failed';
export interface Job {
  id: string;
  stage: Stage;
  bitrate: Bitrate;
  progress: number | null;
  title: string | null;
  duration: number | null;
  queue_position: number | null;
  created_at: string;
  expires_at: string | null;
  error: { code: string; message: string } | null;
}
export interface Limits {
  max_duration_seconds: number;
  file_ttl_seconds: number;
}
interface Health {
  status: string;
  limits?: Limits;
}

const configuredUrl = import.meta.env.VITE_SOUNDDROP_API_URL?.trim().replace(/\/$/, '') ?? '';
export const API_BASE = (() => {
  if (!configuredUrl) return '';
  try {
    const parsed = new URL(configuredUrl);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password
      || parsed.search || parsed.hash || parsed.pathname !== '/') return '';
    if (import.meta.env.PROD && parsed.protocol !== 'https:') return '';
    return parsed.origin;
  } catch { return ''; }
})();

export class ApiError extends Error {
  constructor(message: string, public status = 0, public code = 'network_error') {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, timeout = 12000): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    signal: init.signal
      ? AbortSignal.any([init.signal, AbortSignal.timeout(timeout)])
      : AbortSignal.timeout(timeout),
    credentials: 'omit',
    cache: 'no-store',
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      body?.detail?.message ?? (response.status === 422
        ? 'Check the video link and audio quality, then try again.'
        : 'The conversion server is unavailable. Please try again shortly.'),
      response.status,
      body?.detail?.code ?? 'server_error',
    );
  }
  if (!body) throw new ApiError('The server returned an unexpected response. Please try again.');
  return body as T;
}

export const getHealth = (signal: AbortSignal) => request<Health>('/api/health', { signal }, 8000);

export async function waitForServer(signal: AbortSignal): Promise<Health> {
  for (let attempt = 0; attempt < 6; attempt++) {
    signal.throwIfAborted();
    try {
      const health = await getHealth(signal);
      if (health.status === 'ready') return health;
    } catch (error) {
      if (signal.aborted) throw error;
    }
    if (attempt < 5) {
      await new Promise<void>((resolve, reject) => {
        const stop = () => { clearTimeout(timer); reject(signal.reason); };
        const timer = setTimeout(() => {
          signal.removeEventListener('abort', stop);
          resolve();
        }, 5000);
        signal.addEventListener('abort', stop, { once: true });
      });
    }
  }
  throw new ApiError('The conversion server is still unavailable. Please try again in a minute.');
}

export async function createJob(url: string, bitrate: Bitrate, signal: AbortSignal): Promise<Job> {
  try {
    // A submission is issued exactly once. A failed response may still have created a job.
    return await request<Job>('/api/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, bitrate }), signal,
    }, 20000);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      'We could not confirm your submission. It may still be processing. '
      + 'Wait a moment before trying again; we have not submitted it twice.',
      0, 'submission_uncertain',
    );
  }
}

export const getJob = (id: string, signal: AbortSignal) =>
  request<Job>(`/api/jobs/${encodeURIComponent(id)}`, { signal });

export const downloadUrl = (id: string) => `${API_BASE}/api/jobs/${encodeURIComponent(id)}/download`;

export function isVideoUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password
      || (url.port && !['80', '443'].includes(url.port))) return false;
    const path = url.pathname.replace(/^\/|\/$/g, '').split('/');
    let id: string | null = null;
    if (['youtu.be', 'www.youtu.be'].includes(url.hostname) && path.length === 1) id = path[0];
    if (['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'].includes(url.hostname)) {
      if (url.pathname === '/watch' && url.searchParams.getAll('v').length === 1) id = url.searchParams.get('v');
      if (path.length === 2 && ['shorts', 'embed'].includes(path[0])) id = path[1];
    }
    return !!id && /^[a-zA-Z0-9_-]{11}$/.test(id);
  } catch { return false; }
}
