import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import {
  ArrowDown, ArrowDownToLine, ArrowRight, AudioLines, Check, CheckCircle2,
  ChevronDown, Clipboard, Clock3, Headphones, Link2, LoaderCircle, LockKeyhole,
  Music2, RotateCcw, ShieldCheck, Sparkles, TriangleAlert, X,
} from 'lucide-react';
import { API_BASE, ApiError, createJob, downloadUrl, getHealth, getJob, isVideoUrl, waitForServer } from './api';
import type { Bitrate, Job, Limits, Stage } from './api';

const STORAGE_KEY = 'sounddrop.current-job';
const terminal = (stage: Stage) => stage === 'ready' || stage === 'failed';
const qualities: { value: Bitrate; label: string; caption: string }[] = [
  { value: 128, label: 'Standard', caption: 'Smaller file' },
  { value: 192, label: 'High', caption: 'A little of both' },
  { value: 320, label: 'Best', caption: 'Higher bitrate' },
];
const stageLabels: Record<Stage, string> = {
  queued: 'Waiting for your turn', inspecting: 'Finding your audio',
  downloading: 'Downloading audio', converting: 'Creating your MP3',
  ready: 'Your audio is ready', failed: 'Something interrupted the conversion',
};

function readSaved(): { id: string; url: string; bitrate: Bitrate } | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? 'null');
    return saved && typeof saved.id === 'string' && /^[A-Za-z0-9_-]{43}$/.test(saved.id)
      && typeof saved.url === 'string' && [128, 192, 320].includes(saved.bitrate) ? saved : null;
  } catch { return null; }
}

function Waveform({ active = false }: { active?: boolean }) {
  const heights = [12, 22, 16, 34, 26, 46, 32, 58, 72, 48, 82, 61, 92, 67, 46, 76, 54, 85, 63, 40, 58, 31, 46, 24, 34, 17, 23, 12];
  return <div className={`waveform ${active ? 'waveform-active' : ''}`} aria-hidden="true">
    {heights.map((height, index) => <span key={index} style={{ height, animationDelay: `${index * 60}ms` }} />)}
  </div>;
}

function timespan(seconds: number) {
  const unit = (count: number, name: string) => `${count} ${name}${count === 1 ? '' : 's'}`;
  return seconds >= 3600 && seconds % 3600 === 0 ? unit(seconds / 3600, 'hour') : unit(Math.round(seconds / 60), 'minute');
}

function duration(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}

export default function App() {
  const [saved] = useState(readSaved);
  const [url, setUrl] = useState(saved?.url ?? '');
  const [bitrate, setBitrate] = useState<Bitrate>(saved?.bitrate ?? 192);
  const [jobId, setJobId] = useState<string | null>(saved?.id ?? null);
  const [job, setJob] = useState<Job | null>(null);
  const [phase, setPhase] = useState<'idle' | 'connecting' | 'submitting'>('idle');
  const [error, setError] = useState('');
  const [inputError, setInputError] = useState('');
  const [clipboardMessage, setClipboardMessage] = useState('');
  const [pollAttempt, setPollAttempt] = useState(0);
  const [limits, setLimits] = useState<Limits | null>(null);
  const controller = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const locked = !!jobId || phase !== 'idle';
  const active = phase !== 'idle' || (!!jobId && (!job || !terminal(job.stage)));

  useEffect(() => () => controller.current?.abort(), []);

  useEffect(() => {
    if (!API_BASE) return;
    // Limits are only used for copy; while the server sleeps, the copy omits them.
    const abort = new AbortController();
    getHealth(abort.signal).then(health => { if (health.limits) setLimits(health.limits); }).catch(() => {});
    return () => abort.abort();
  }, []);

  useEffect(() => {
    if (!jobId || !API_BASE) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const poll = async () => {
      try {
        const current = await getJob(jobId, abort.signal);
        if (abort.signal.aborted) return;
        setJob(current);
        setError('');
        failures = 0;
        if (!terminal(current.stage)) timer = setTimeout(poll, 2000);
      } catch (cause) {
        if (abort.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 404) {
          setError(cause.message);
          setJob(null);
          return;
        }
        failures++;
        if (failures < 3) timer = setTimeout(poll, 2000);
        else setError('Connection interrupted. Your conversion may still be running. Reconnect to check its status.');
      }
    };
    void poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [jobId, pollAttempt]);

  useEffect(() => {
    if (job?.stage !== 'ready' || !job.expires_at) return;
    const timer = setTimeout(() => {
      setJob(null);
      setError('This download has expired. Start a new conversion to create it again.');
    }, Math.max(0, Date.parse(job.expires_at) - Date.now()));
    return () => clearTimeout(timer);
  }, [job]);

  function reset() {
    controller.current?.abort();
    setJobId(null); setJob(null); setError(''); setInputError(''); setPhase('idle');
    try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Storage can be disabled. */ }
    setTimeout(() => input.current?.focus(), 0);
  }

  function cancel() {
    controller.current?.abort();
    setPhase('idle');
    setTimeout(() => input.current?.focus(), 0);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked || !API_BASE) return;
    if (!isVideoUrl(url)) {
      setInputError('Enter a YouTube video link, such as https://www.youtube.com/watch?v=…');
      input.current?.focus();
      return;
    }
    setError(''); setInputError(''); setPhase('connecting');
    const abort = new AbortController();
    controller.current = abort;
    try {
      const health = await waitForServer(abort.signal);
      if (health.limits) setLimits(health.limits);
      setPhase('submitting');
      const current = await createJob(url.trim(), bitrate, abort.signal);
      if (abort.signal.aborted) return;
      setJob(current); setJobId(current.id);
      try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ id: current.id, url: url.trim(), bitrate })); }
      catch { /* Conversion also works without session storage. */ }
    } catch (cause) {
      if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not start conversion. Please try again.');
    } finally {
      if (!abort.signal.aborted) setPhase('idle');
    }
  }

  async function paste() {
    try {
      setUrl(await navigator.clipboard.readText()); setInputError(''); setClipboardMessage('Link pasted.');
    } catch {
      setClipboardMessage('Use Ctrl+V or ⌘V to paste your link.'); input.current?.focus();
    }
  }

  const statusTitle = phase === 'connecting' ? 'Connecting to conversion server'
    : phase === 'submitting' ? 'Starting your conversion'
      : job ? stageLabels[job.stage] : jobId ? 'Checking your conversion' : 'A little less video. A lot more audio.';

  return <>
    <a className="skip-link" href="#converter">Skip to converter</a>
    <header className="site-header">
      <a className="brand" href="#" aria-label="Sounddrop home"><span className="brand-mark"><AudioLines size={23} strokeWidth={2.3} /></span>sounddrop<span className="brand-dot">.</span></a>
      <nav aria-label="Main navigation"><a href="#how-it-works">How it works</a><a href="#faq">FAQs</a><span className="nav-tag"><span />No sign-up needed</span></nav>
    </header>

    <main>
      <section className="hero" aria-labelledby="hero-title">
        <div className="eyebrow"><span><Music2 size={13} /></span> YOUR SOUND, SIMPLIFIED</div>
        <h1 id="hero-title">Your video.<br /><span>Just the audio.</span></h1>
        <p className="hero-copy">Turn a YouTube link into an MP3.<br className="mobile-break" /> Keep the part you came to hear.</p>
        <div className="hero-features"><span><Check size={14} />No account</span><span><Check size={14} />Your choice of quality</span><span><Check size={14} />Ready to download</span></div>
      </section>

      <section id="converter" className="converter" aria-labelledby="converter-title">
        <div className="converter-main">
          <div className="card-heading"><span className="small-icon"><Link2 size={19} /></span><div><h2 id="converter-title">Drop a link. Pick your sound.</h2><p>One video, one MP3. That's all it takes.</p></div></div>
          {!API_BASE && <div className="notice" role="status"><TriangleAlert size={18} /><span>The converter is not connected yet. Please check back soon.</span></div>}
          <form onSubmit={submit} noValidate>
            <label className="field-label" htmlFor="video-url">YouTube video link</label>
            <div className={`url-field ${inputError ? 'url-field-invalid' : ''}`}>
              <Link2 size={19} aria-hidden="true" />
              <input id="video-url" ref={input} value={url} onChange={e => { setUrl(e.target.value); setInputError(''); }} placeholder="Paste your YouTube link here" type="url" autoComplete="off" spellCheck={false} disabled={locked} aria-invalid={!!inputError} aria-describedby={inputError ? 'url-error' : 'url-hint'} />
              {url && !locked ? <button type="button" className="clear-button" aria-label="Clear link" onClick={() => { setUrl(''); input.current?.focus(); }}><X size={16} /></button> : null}
              <button type="button" className="paste-button" disabled={locked} onClick={paste}><Clipboard size={14} />Paste</button>
            </div>
            {inputError ? <p id="url-error" className="field-error" role="alert">{inputError}</p> : <p id="url-hint" className="field-hint">YouTube videos and Shorts{limits && ` · up to ${Math.floor(limits.max_duration_seconds / 60)} minutes`}</p>}
            <span className="sr-only" role="status">{clipboardMessage}</span>

            <fieldset className="quality-field" disabled={locked}><legend>Audio quality <span>MP3</span></legend>
              <div className="qualities">{qualities.map(quality => <label key={quality.value} className={`quality ${bitrate === quality.value ? 'quality-selected' : ''}`}>
                <input type="radio" name="bitrate" value={quality.value} checked={bitrate === quality.value} onChange={() => setBitrate(quality.value)} />
                <span className="quality-top">{quality.label}<span className="radio-dot">{bitrate === quality.value && <Check size={10} strokeWidth={3} />}</span></span>
                <span className="quality-number">{quality.value}<small>kbps</small></span><span className="quality-caption">{quality.caption}</span>
              </label>)}</div>
            </fieldset>

            <button className="convert-button" type="submit" disabled={locked || !API_BASE}>
              {active ? <LoaderCircle className="spin" size={18} /> : <AudioLines size={19} />}
              {phase === 'connecting' ? 'Connecting…' : phase === 'submitting' ? 'Submitting…' : active ? 'Conversion in progress' : 'Convert to MP3'}
              {!active && <ArrowRight size={18} />}
            </button>
            <p className="form-footnote"><LockKeyhole size={12} />Temporary files. No account or saved history.</p>
          </form>
          {error && <div className="error-banner" role="alert"><TriangleAlert size={19} /><div><p>{error}</p>{jobId && <div className="error-actions"><button type="button" onClick={() => { setError(''); setPollAttempt(x => x + 1); }}>Reconnect</button><button type="button" onClick={reset}>Start over</button></div>}</div></div>}
        </div>

        <div className={`audio-panel ${job?.stage === 'ready' ? 'audio-panel-ready' : ''}`}>
          <div className="panel-label"><span className={active ? 'status-dot status-dot-active' : 'status-dot'} />{job?.stage === 'ready' ? 'READY WHEN YOU ARE' : active ? 'MAKING IT HAPPEN' : 'MADE FOR LISTENING'}</div>
          <div className="audio-art"><div className="art-disc"><Headphones size={32} strokeWidth={1.6} /></div><Waveform active={active && !error} /></div>
          <div className="status-content" role="status" aria-live="polite" aria-atomic="true">
            {job?.stage === 'ready' && <CheckCircle2 className="ready-icon" size={25} />}
            <h3>{error && jobId ? 'Let’s get you reconnected' : statusTitle}</h3>
            {job?.title && <p className="video-title">{job.title}</p>}
            {job?.duration ? <p className="audio-meta">{duration(job.duration)} <span>·</span> MP3 <span>·</span> {job.bitrate} kbps</p> : null}
            {job?.stage === 'queued' && <p className="panel-copy">Queue position {job.queue_position ?? '…'}. Your audio is up soon.</p>}
            {job?.stage === 'failed' && <p className="panel-copy panel-error">{job.error?.message ?? 'Please try again in a moment.'}</p>}
            {phase === 'connecting' && <p className="panel-copy">The server may be waking up.<br />This can take about a minute.</p>}
            {!jobId && phase === 'idle' && <p className="panel-copy">For your next walk, deep-focus session,<br />or long way home.</p>}
          </div>
          {phase === 'connecting' && <button className="reset-button" type="button" onClick={cancel}><X size={13} />Cancel</button>}
          {job?.stage === 'downloading' && <div className="progress-section">
            {job.progress !== null ? <><div className="progress-label"><span>Downloading</span><span>{Math.round(job.progress)}%</span></div><progress max={100} value={job.progress} aria-label="Audio download progress" /></> : <p className="panel-copy">Downloading audio…</p>}
          </div>}
          {job?.stage === 'converting' && <div className="converting-label"><LoaderCircle className="spin" size={14} />Encoding your audio…</div>}
          {job?.stage === 'ready' && !error && <div className="download-actions"><a href={downloadUrl(job.id)} className="download-button" referrerPolicy="no-referrer"><ArrowDownToLine size={18} />Download MP3</a>{job.expires_at && <p>Available until {new Date(job.expires_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</p>}</div>}
          {job && terminal(job.stage) && <button className="reset-button" type="button" onClick={reset}><RotateCcw size={13} />{job.stage === 'failed' ? 'Try another conversion' : 'Convert another video'}</button>}
          {!jobId && phase === 'idle' && <div className="format-chips"><span>YOUTUBE</span><ArrowRight size={14} /><span className="mp3-chip"><Music2 size={12} />MP3</span></div>}
        </div>
      </section>

      <div className="trust-row"><span><ShieldCheck size={16} />No sign-up required</span><span><Headphones size={16} />128–320 kbps audio</span><span><Clock3 size={16} />Files automatically deleted</span></div>

      <section id="how-it-works" className="how-section" aria-labelledby="how-title">
        <div className="section-heading"><p className="section-kicker">FROM LINK TO LISTEN</p><h2 id="how-title">Three steps. All yours.</h2><p>A simple way to take the audio with you.</p></div>
        <div className="steps">
          <article className="step"><span className="step-number">01</span><div className="step-icon"><Link2 size={23} /></div><h3>Copy your link</h3><p>Find a YouTube video and copy its link. A Short works, too.</p></article>
          <article className="step"><span className="step-number">02</span><div className="step-icon"><AudioLines size={23} /></div><h3>Make it your MP3</h3><p>Choose your audio quality. We'll take care of the conversion.</p></article>
          <article className="step"><span className="step-number">03</span><div className="step-icon"><ArrowDownToLine size={23} /></div><h3>Download. Press play.</h3><p>Save your MP3 and listen with your favorite audio player.</p></article>
        </div>
      </section>

      <section id="faq" className="faq-section" aria-labelledby="faq-title">
        <div className="faq-intro"><span className="section-kicker">GOOD TO KNOW</span><h2 id="faq-title">A few small<br />sound checks.</h2><p>The details, without the noise.</p><span className="faq-decoration" aria-hidden="true"><Sparkles size={26} /></span></div>
        <div className="faq-list">{[
          ['Which audio quality should I choose?', '192 kbps is a good balance of file size and bitrate. Choose 128 kbps for a smaller file or 320 kbps for a higher bitrate. A higher output bitrate cannot improve the quality of the original audio.'],
          ['Can I convert a playlist or a long video?', `Sounddrop converts one video at a time${limits ? `, up to ${Math.floor(limits.max_duration_seconds / 60)} minutes long` : ''}. YouTube Shorts work too. Playlists and live streams are not supported.`],
          ['How long is my download available?', `Files are automatically deleted ${limits ? `after ${timespan(limits.file_ttl_seconds)}` : 'after a short time'}. This prototype uses temporary storage, so a server restart may remove them sooner. If a download is gone, start a new conversion.`],
          ['Why is the converter taking a moment to connect?', 'The conversion server rests when it is idle and can take about a minute to wake up. Some videos may also be unavailable or restricted by YouTube. We will show you what happened.'],
        ].map(([question, answer]) => <details key={question}><summary>{question}<ChevronDown size={17} /></summary><p>{answer}</p></details>)}</div>
      </section>
      <div className="closing-note"><Music2 size={16} /><span>Less watching. More listening.</span><a href="#converter" aria-label="Back to converter"><ArrowDown size={16} /></a></div>
    </main>
    <footer className="site-footer"><a className="brand footer-brand" href="#"><AudioLines size={20} />sounddrop.</a><p>Convert audio you own or have permission to download.</p><span>Built for the listening part.</span></footer>
  </>;
}
