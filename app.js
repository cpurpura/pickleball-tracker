// Pickleball Drill Tracker — all app logic. Plain JavaScript, no build step.
// Data lives on the device in IndexedDB; export to CSV/JSON to move it to a PC.

// ---------- Helpers ----------
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
const pct = (made, attempts) => (attempts ? Math.round((made / attempts) * 100) : null);
const pad = (n) => String(n).padStart(2, '0');
const clock = (sec) => `${Math.floor(sec / 60)}:${pad(Math.floor(sec % 60))}`;
const localDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localTime = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const fmtDate = (iso) => new Date(iso).toLocaleString(undefined,
  { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const starText = (n) => (n ? '★'.repeat(n) : '');
// Only allow real web links, never javascript: or data: URLs
const safeUrl = (u) => {
  try { const x = new URL(String(u ?? '').trim()); return /^https?:$/.test(x.protocol) ? x.href : null; }
  catch { return null; }
};
const hostLabel = (url) => `${new URL(url).hostname.replace(/^(www|m)\./, '')} video`;
const youtubeSearch = (name) =>
  `https://www.youtube.com/results?search_query=${encodeURIComponent(`pickleball ${name} drill`)}`;

// ---------- Storage (IndexedDB) ----------
let dbPromise;
function openDb() {
  dbPromise ??= new Promise((resolve, reject) => {
    const req = indexedDB.open('pickleball-drills', 1);
    req.onupgradeneeded = () => {
      const d = req.result;
      d.createObjectStore('plans', { keyPath: 'id' });
      d.createObjectStore('sessions', { keyPath: 'id' });
      d.createObjectStore('meta', { keyPath: 'key' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}
async function withStore(name, mode, fn) {
  const d = await openDb();
  return new Promise((resolve, reject) => {
    const tx = d.transaction(name, mode);
    const req = fn(tx.objectStore(name));
    tx.oncomplete = () => resolve(req?.result);
    tx.onerror = () => reject(tx.error);
  });
}
const db = {
  all: (s) => withStore(s, 'readonly', (st) => st.getAll()),
  get: (s, key) => withStore(s, 'readonly', (st) => st.get(key)),
  put: (s, val) => withStore(s, 'readwrite', (st) => st.put(val)),
  delete: (s, key) => withStore(s, 'readwrite', (st) => st.delete(key)),
};
const getMeta = async (key) => (await db.get('meta', key))?.value;
const setMeta = (key, value) => db.put('meta', { key, value });

// ---------- State ----------
const state = {
  tab: 'plans',
  plans: [],
  sessions: [],      // newest first
  active: null,      // in-progress session (persisted in meta so it survives closing the app)
  openPlan: null,
  openSession: null,
  lastExport: null,
  persisted: false,
};

async function loadAll() {
  state.plans = (await db.all('plans')).sort((a, b) => a.name.localeCompare(b.name));
  state.sessions = (await db.all('sessions')).sort((a, b) => b.startedAt.localeCompare(a.startedAt));
}
const planById = (id) => state.plans.find((p) => p.id === id);
const currentResult = () => state.active?.results[state.active.current];
const pastPartners = () => [...new Set(state.sessions.map((s) => s.partner).filter(Boolean))]; // most recent first

// Per-player fields on a drill result: "me" uses hits/rating/notes, "partner" the partner* versions
const hitsKey = (player) => (player === 'partner' ? 'partnerHits' : 'hits');
const ratingKey = (player) => (player === 'partner' ? 'partnerRating' : 'rating');
const notesKey = (player) => (player === 'partner' ? 'partnerNotes' : 'notes');
const playerOf = () => (state.active?.partner ? state.active.player : 'me');

// Fill in fields added after a session was started on an older version of the app
function upgradeActive(a) {
  if (!a) return null;
  a.partner ??= '';
  a.player ??= 'me';
  for (const r of a.results) {
    r.partnerHits ??= [];
    r.partnerRating ??= 0;
    r.partnerNotes ??= '';
    r.videos ??= [];
  }
  return a;
}

const saveActive = () => setMeta('active', state.active);
let saveTimer;
const saveActiveSoon = () => { clearTimeout(saveTimer); saveTimer = setTimeout(saveActive, 400); };

// ---------- Plan import (JSON or CSV) ----------
const normKey = (k) => String(k).toLowerCase().replace(/[^a-z0-9]/g, '');
const normKeys = (o) => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [normKey(k), v]));
const pick = (o, keys) => {
  for (const k of keys) if (o[k] !== undefined && o[k] !== null && o[k] !== '') return o[k];
};
const str = (v) => (Array.isArray(v) ? v.join(' ') : String(v ?? '')).trim();
const num = (v) => { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; };

const PLAN_KEYS = {
  name: ['name', 'planname', 'plan', 'title'],
  description: ['description', 'plandescription', 'summary', 'focus', 'goal'],
};
const DRILL_KEYS = {
  name: ['name', 'drill', 'drillname', 'title'],
  category: ['category', 'focus', 'skill', 'type'],
  durationMin: ['durationmin', 'durationminutes', 'duration', 'minutes', 'mins', 'time'],
  reps: ['reps', 'repetitions', 'targetreps', 'count', 'balls'],
  target: ['target', 'goal', 'successcriteria', 'success'],
  instructions: ['instructions', 'description', 'details', 'howto', 'setup', 'cues', 'notes'],
  videos: ['videos', 'video', 'videourl', 'videourls', 'videolinks', 'links', 'link'],
};

// Accepts a URL, a list of URLs (array, or a "|"-/space-separated string), or [{title, url}]
function normVideos(v) {
  if (!v) return [];
  const list = Array.isArray(v) ? v : typeof v === 'object' ? [v] : String(v).split(/[\s|]+/);
  return list.map((item) => {
    if (typeof item !== 'object' || item === null) return { title: '', url: safeUrl(item) };
    const o = normKeys(item);
    return { title: str(pick(o, ['title', 'name', 'label'])), url: safeUrl(pick(o, ['url', 'link', 'href'])) };
  }).filter((x) => x.url);
}

function normDrill(raw) {
  const d = normKeys(raw);
  return {
    name: str(pick(d, DRILL_KEYS.name)),
    category: str(pick(d, DRILL_KEYS.category)),
    durationMin: num(pick(d, DRILL_KEYS.durationMin)),
    reps: num(pick(d, DRILL_KEYS.reps)),
    target: str(pick(d, DRILL_KEYS.target)),
    instructions: str(pick(d, DRILL_KEYS.instructions)),
    videos: normVideos(pick(d, DRILL_KEYS.videos)),
  };
}

function normPlan(raw, fallbackName) {
  const p = normKeys(raw);
  const drills = (p.drills || []).map(normDrill).filter((d) => d.name);
  if (!drills.length) throw new Error('no drills found');
  return {
    id: raw.id || uid(),
    name: str(pick(p, PLAN_KEYS.name)) || fallbackName,
    description: str(pick(p, PLAN_KEYS.description)),
    drills,
    importedAt: new Date().toISOString(),
  };
}

function parseCSV(text) {
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((f) => f.trim()));
}

function csvPlans(text, fallbackName) {
  const [header, ...rows] = parseCSV(text);
  if (!header || !rows.length) throw new Error('empty file');
  const keys = header.map(normKey);
  const groups = new Map();
  for (const row of rows) {
    const obj = Object.fromEntries(keys.map((k, i) => [k, (row[i] ?? '').trim()]));
    const planName = pick(obj, ['plan', 'planname']) || fallbackName;
    if (!groups.has(planName)) groups.set(planName, { name: planName, description: obj.plandescription || '', drills: [] });
    groups.get(planName).drills.push(obj);
  }
  return [...groups.values()].map((p) => normPlan(p, fallbackName));
}

function parseImport(text, filename) {
  text = text.replace(/^﻿/, '').trim();
  const fallbackName = filename.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ');
  if (!/^[[{]/.test(text)) return { plans: csvPlans(text, fallbackName), sessions: [] };

  const data = JSON.parse(text);
  if (data?.type === 'pickleball-drill-backup') {
    return { plans: (data.plans || []).map((p) => normPlan(p, fallbackName)), sessions: data.sessions || [] };
  }
  let rawPlans;
  if (Array.isArray(data)) {
    // Either a list of plans, or a bare list of drills
    rawPlans = data.every((x) => x && (x.drills || x.Drills)) ? data : [{ name: fallbackName, drills: data }];
  } else if (Array.isArray(data.plans)) rawPlans = data.plans;
  else rawPlans = [data];
  return { plans: rawPlans.map((p) => normPlan(p, fallbackName)), sessions: [] };
}

async function importText(text, filename) {
  const { plans, sessions } = parseImport(text, filename);
  for (const p of plans) {
    // Re-importing a plan with the same name replaces it (past sessions are unaffected)
    const existing = state.plans.find((x) => x.id === p.id || x.name.toLowerCase() === p.name.toLowerCase());
    if (existing) {
      p.id = existing.id;
      // Keep video links you added in the app for drills that are still in the plan
      for (const d of p.drills) {
        const old = existing.drills.find((o) => o.name.toLowerCase() === d.name.toLowerCase());
        for (const v of old?.videos ?? []) if (!d.videos.some((x) => x.url === v.url)) d.videos.push(v);
      }
    }
    await db.put('plans', p);
  }
  for (const s of sessions) await db.put('sessions', s);
  await loadAll();
  const parts = [];
  if (plans.length) parts.push(`${plans.length} plan${plans.length > 1 ? 's' : ''}`);
  if (sessions.length) parts.push(`${sessions.length} session${sessions.length > 1 ? 's' : ''}`);
  toast(`Imported ${parts.join(' and ')}`);
  if (plans.length) { state.tab = 'plans'; state.openPlan = plans[plans.length - 1].id; }
  render();
}

// ---------- Export ----------
const CSV_COLS = ['session_date', 'start_time', 'plan', 'drill_no', 'drill', 'category', 'target',
  'planned_min', 'actual_min', 'attempts', 'made', 'success_pct', 'drill_rating', 'drill_notes',
  'partner', 'partner_attempts', 'partner_made', 'partner_success_pct', 'partner_rating', 'partner_notes',
  'session_rating', 'session_notes', 'session_id'];

function sessionsToCSV(sessions) {
  const cell = (v) => { const s = String(v ?? ''); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const rows = [CSV_COLS];
  for (const s of [...sessions].reverse()) {
    const d = new Date(s.startedAt);
    s.results.forEach((r, i) => rows.push([
      localDate(d), localTime(d), s.planName, i + 1, r.name, r.category, r.target,
      r.durationMin || '', (r.elapsedSec / 60).toFixed(1), r.attempts, r.made,
      pct(r.made, r.attempts) ?? '', r.rating || '', r.notes,
      s.partner || '',
      s.partner ? r.partnerAttempts ?? 0 : '',
      s.partner ? r.partnerMade ?? 0 : '',
      s.partner ? pct(r.partnerMade, r.partnerAttempts) ?? '' : '',
      r.partnerRating || '', r.partnerNotes || '',
      s.rating || '', s.notes, s.id,
    ]));
  }
  // BOM so Excel opens it as UTF-8
  return '﻿' + rows.map((r) => r.map(cell).join(',')).join('\r\n');
}

const backupJSON = () => JSON.stringify({
  type: 'pickleball-drill-backup', version: 1, exportedAt: new Date().toISOString(),
  plans: state.plans, sessions: state.sessions,
}, null, 2);

function download(file) {
  const url = URL.createObjectURL(file);
  const a = Object.assign(document.createElement('a'), { href: url, download: file.name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function exportFile(kind, mode) {
  if (kind === 'csv' && !state.sessions.length) return toast('No sessions to export yet', true);
  const stamp = localDate(new Date());
  const file = kind === 'csv'
    ? new File([sessionsToCSV(state.sessions)], `pickleball-sessions-${stamp}.csv`, { type: 'text/csv' })
    : new File([backupJSON()], `pickleball-backup-${stamp}.json`, { type: 'application/json' });

  if (mode === 'share' && navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: file.name });
    } catch (err) {
      if (err.name === 'AbortError') return;
      download(file);
    }
  } else {
    if (mode === 'share') toast('Sharing this file type isn’t supported here, so it was downloaded instead');
    download(file);
  }
  state.lastExport = new Date().toISOString();
  await setMeta('lastExport', state.lastExport);
  render();
}

// ---------- Session flow ----------
async function startSession(planId) {
  if (state.active && !confirm('A session is already in progress. Discard it and start a new one?')) return;
  const p = planById(planId);
  state.active = {
    id: uid(), planId: p.id, planName: p.name, startedAt: new Date().toISOString(),
    current: 0, timerStartedAt: null, rating: 0, notes: '', partner: '', player: 'me',
    results: p.drills.map((d) => ({
      ...d, videos: [...(d.videos || [])], hits: [], rating: 0, notes: '',
      partnerHits: [], partnerRating: 0, partnerNotes: '', elapsedSec: 0, done: false,
    })),
  };
  state.editPartner = false;
  await saveActive();
  requestPersistence();
  state.tab = 'session';
  render();
  scrollTo(0, 0);
}

function goTo(i) {
  const a = state.active;
  if (!a || i < 0 || i > a.results.length) return; // index == length is the summary page
  pauseTimer();
  a.current = i;
  saveActive();
  render();
  scrollTo(0, 0);
}

async function finishSession() {
  const a = state.active;
  pauseTimer();
  const { current, timerStartedAt, player, ...rest } = a;
  const session = {
    ...rest,
    endedAt: new Date().toISOString(),
    results: a.results.map((r) => ({
      ...r,
      attempts: r.hits.length, made: r.hits.filter(Boolean).length,
      partnerAttempts: r.partnerHits.length, partnerMade: r.partnerHits.filter(Boolean).length,
    })),
  };
  await db.put('sessions', session);
  state.active = null;
  await setMeta('active', null);
  await loadAll();
  state.tab = 'history';
  state.openSession = session.id;
  render();
  scrollTo(0, 0);
  toast('Session saved');
}

// ---------- Timer, alarm, screen wake lock ----------
let audioCtx;
let alarmed = false;

function elapsed(r) {
  const a = state.active;
  const running = a.timerStartedAt && r === currentResult();
  return r.elapsedSec + (running ? (Date.now() - a.timerStartedAt) / 1000 : 0);
}

function clockState(r) {
  const total = r.durationMin * 60;
  const e = elapsed(r);
  if (!total) return { text: clock(e), over: false, left: Infinity };
  const left = total - e;
  return { text: left >= 0 ? clock(Math.ceil(left)) : '+' + clock(-left), over: left < 0, left };
}

function pauseTimer() {
  const a = state.active;
  const r = currentResult();
  if (a?.timerStartedAt && r) r.elapsedSec = elapsed(r);
  if (a) a.timerStartedAt = null;
}

function toggleTimer() {
  const a = state.active;
  audioCtx ??= new (window.AudioContext || window.webkitAudioContext)(); // must be created from a tap
  if (a.timerStartedAt) pauseTimer();
  else { a.timerStartedAt = Date.now(); alarmed = clockState(currentResult()).left <= 0; }
  saveActive();
  render();
}

function resetTimer() {
  state.active.timerStartedAt = null;
  currentResult().elapsedSec = 0;
  alarmed = false;
  saveActive();
  render();
}

function alarm() {
  navigator.vibrate?.([400, 200, 400, 200, 400]);
  if (!audioCtx) return;
  [0, 0.35, 0.7].forEach((t) => {
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.frequency.value = 880;
    o.connect(g).connect(audioCtx.destination);
    const s = audioCtx.currentTime + t;
    g.gain.setValueAtTime(0.3, s);
    g.gain.exponentialRampToValueAtTime(0.001, s + 0.25);
    o.start(s);
    o.stop(s + 0.25);
  });
}

function tick() {
  const r = currentResult();
  if (!r) return;
  const c = clockState(r);
  if (state.active.timerStartedAt && c.left <= 0 && !alarmed) { alarmed = true; alarm(); }
  const el = document.getElementById('clock');
  if (el) { el.textContent = c.text; el.classList.toggle('over', c.over); }
}

let wakeLock = null;
async function updateWakeLock() {
  const want = state.active && state.tab === 'session' && document.visibilityState === 'visible';
  try {
    if (want && !wakeLock && 'wakeLock' in navigator) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } else if (!want && wakeLock) {
      await wakeLock.release();
      wakeLock = null;
    }
  } catch { wakeLock = null; }
}

async function requestPersistence() {
  // Asks the browser not to clear this app's data when storage runs low
  if (navigator.storage?.persist) state.persisted = await navigator.storage.persist();
}

// ---------- Views ----------
const stars = (value, scope) => `<div class="stars" role="group" aria-label="Rating">${[1, 2, 3, 4, 5].map((n) =>
  `<button class="star ${n <= value ? 'on' : ''}" data-action="rate" data-scope="${scope}" data-v="${n}" aria-label="${n} of 5">★</button>`).join('')}</div>`;

function videoLinks(d, planId, i, removable = false) {
  const saved = (d.videos || []).map((v, vi) => `<li>
    <a href="${esc(v.url)}" target="_blank" rel="noopener noreferrer">▶ ${esc(v.title || hostLabel(v.url))}</a>
    ${removable ? `<button class="link danger" data-action="remove-video" data-plan="${esc(planId)}" data-i="${i}" data-v="${vi}" aria-label="Remove video">✕</button>` : ''}
  </li>`).join('');
  return `<ul class="video-list">${saved}
    <li><a href="${esc(youtubeSearch(d.name))}" target="_blank" rel="noopener noreferrer">🔎 Search YouTube for this drill</a></li>
    <li><button class="link" data-action="add-video" data-plan="${esc(planId)}" data-i="${i}">＋ Add video link</button></li>
  </ul>`;
}

function partnerBar(a) {
  if (state.editPartner) {
    return `<div class="card partner-bar">
      <label for="partnerName">Drilling with</label>
      <div class="row tight">
        <input id="partnerName" list="partnerList" value="${esc(a.partner)}" placeholder="Partner’s name (blank = just you)" autocomplete="off" enterkeyhint="done">
        <button class="btn primary" data-action="save-partner">Save</button>
      </div>
      <datalist id="partnerList">${pastPartners().map((n) => `<option value="${esc(n)}"></option>`).join('')}</datalist>
    </div>`;
  }
  return a.partner
    ? `<p class="partner-line">👥 With <b>${esc(a.partner)}</b> · <button class="link" data-action="edit-partner">Change</button></p>`
    : `<p class="partner-line"><button class="link" data-action="edit-partner">＋ Log a partner’s results too</button></p>`;
}

const hitCell = (hits) => {
  const m = hits.filter(Boolean).length;
  return hits.length ? `${m}/${hits.length} <span class="muted">${pct(m, hits.length)}%</span>` : '–';
};

function drillMeta(d) {
  const bits = [d.category, d.durationMin && `${d.durationMin} min`, d.reps && `${d.reps} reps`].filter(Boolean);
  return bits.length ? `<span class="muted small"> · ${bits.map(esc).join(' · ')}</span>` : '';
}

const views = {
  plans() {
    const list = state.plans.map((p) => {
      const mins = p.drills.reduce((t, d) => t + (d.durationMin || 0), 0);
      const open = state.openPlan === p.id;
      return `<article class="card">
        <button class="card-head" data-action="toggle-plan" data-id="${p.id}" aria-expanded="${open}">
          <div><h3>${esc(p.name)}</h3><p class="muted small">${p.drills.length} drill${p.drills.length === 1 ? '' : 's'}${mins ? ` · ${mins} min` : ''}</p></div>
          <span class="chev">${open ? '▾' : '▸'}</span>
        </button>
        ${open ? `<div class="card-body">
          ${p.description ? `<p>${esc(p.description)}</p>` : ''}
          <ol class="drill-list">${p.drills.map((d, i) => `<li>
            <strong>${esc(d.name)}</strong>${drillMeta(d)}
            ${d.target ? `<p class="small"><b>Target:</b> ${esc(d.target)}</p>` : ''}
            ${d.instructions ? `<p class="muted small">${esc(d.instructions)}</p>` : ''}
            ${videoLinks(d, p.id, i, true)}
          </li>`).join('')}</ol>
          <div class="row">
            <button class="btn primary" data-action="start" data-id="${p.id}">▶ Start session</button>
            <button class="btn ghost danger" data-action="delete-plan" data-id="${p.id}">Delete</button>
          </div>
        </div>` : ''}
      </article>`;
    }).join('');

    return `<div class="toolbar">
        <h2>Drill plans</h2>
        <button class="btn primary" data-action="import">＋ Import</button>
      </div>
      ${list || `<div class="empty">
        <p>No plans yet.</p>
        <p class="muted small">Ask Claude for a drill plan (see the <b>Data</b> tab for a ready-made prompt), save it as a <code>.json</code> file, then tap <b>Import</b>.</p>
        <button class="btn" data-action="load-sample">Load a sample plan</button>
      </div>`}`;
  },

  session() {
    const a = state.active;
    if (!a) {
      return `<div class="empty">
        <h2>No session in progress</h2>
        <p class="muted">Choose a plan to start.</p>
        ${state.plans.map((p) => `<button class="btn block" data-action="start" data-id="${p.id}">▶ ${esc(p.name)}</button>`).join('')
          || '<button class="btn primary" data-action="tab" data-tab="plans">Go to Plans</button>'}
      </div>`;
    }
    const chips = `<div class="chips">${a.results.map((r, i) =>
      `<button class="chip ${i === a.current ? 'on' : ''} ${r.done ? 'done' : ''}" data-action="goto" data-i="${i}" aria-label="Drill ${i + 1}">${i + 1}</button>`).join('')}
      <button class="chip ${a.current === a.results.length ? 'on' : ''}" data-action="goto" data-i="${a.results.length}" aria-label="Summary">✓</button></div>`;
    const head = `<div class="progress"><strong>${esc(a.planName)}</strong>
      <span class="muted small">${a.current < a.results.length ? `Drill ${a.current + 1} of ${a.results.length}` : 'Summary'}</span></div>`;

    if (a.current === a.results.length) return head + chips + summaryView(a);

    const r = currentResult();
    const player = playerOf();
    const who = player === 'partner' ? a.partner : 'you';
    const hits = r[hitsKey(player)];
    const made = hits.filter(Boolean).length;
    const att = hits.length;
    const c = clockState(r);
    const isLast = a.current === a.results.length - 1;
    return `${head}${chips}${partnerBar(a)}
      <article class="card drill">
        ${r.category ? `<p class="eyebrow">${esc(r.category)}</p>` : ''}
        <h2>${esc(r.name)}</h2>
        ${r.target ? `<p><b>Target:</b> ${esc(r.target)}</p>` : ''}
        ${r.instructions ? `<p class="muted">${esc(r.instructions)}</p>` : ''}
        <details class="videos"><summary>🎬 Videos${r.videos.length ? ` (${r.videos.length})` : ''}</summary>
          ${videoLinks(r, a.planId, a.current)}
        </details>
      </article>

      <div class="card timer">
        <div id="clock" class="clock ${c.over ? 'over' : ''}">${c.text}</div>
        <p class="muted small">${r.durationMin ? `${r.durationMin} min drill` : 'Stopwatch'}</p>
        <div class="row center">
          <button class="btn primary" data-action="timer-toggle">${a.timerStartedAt ? '⏸ Pause' : r.elapsedSec ? '▶ Resume' : '▶ Start'}</button>
          <button class="btn ghost" data-action="timer-reset">Reset</button>
        </div>
      </div>

      <div class="card counter">
        ${a.partner ? `<div class="seg" role="group" aria-label="Logging for">
          ${['me', 'partner'].map((p) => `<button class="${p === player ? 'on' : ''}" data-action="player" data-v="${p}" aria-pressed="${p === player}">
            ${p === 'me' ? 'You' : esc(a.partner)}<span class="small">${(() => {
              const h = r[hitsKey(p)];
              const m = h.filter(Boolean).length;
              return h.length ? `${m}/${h.length} · ${pct(m, h.length)}%` : 'no shots yet';
            })()}</span>
          </button>`).join('')}
        </div>` : ''}
        <div class="score"><span class="big-num">${made}</span> / ${att}
          ${att ? `<span class="muted">· ${pct(made, att)}%</span>` : ''}
          ${r.reps ? `<span class="muted small">· goal ${r.reps}</span>` : ''}</div>
        <div class="row">
          <button class="btn hit made" data-action="hit" data-v="1">✓ Made</button>
          <button class="btn hit miss" data-action="hit" data-v="0">✗ Miss</button>
        </div>
        <button class="btn ghost small" data-action="undo" ${att ? '' : 'disabled'}>↶ Undo last</button>
      </div>

      <div class="card">
        <label>How did it go${a.partner ? ` for ${esc(who)}` : ''}?</label>
        ${stars(r[ratingKey(player)], 'drill')}
        <label for="drillNotes">Notes${a.partner ? ` · ${esc(who)}` : ''}</label>
        <textarea id="drillNotes" data-field="drill-notes" rows="3" placeholder="What worked, what to fix…">${esc(r[notesKey(player)])}</textarea>
      </div>

      <div class="row nav">
        <button class="btn" data-action="prev" ${a.current === 0 ? 'disabled' : ''}>← Prev</button>
        <button class="btn primary" data-action="next">${isLast ? 'Wrap up ✓' : 'Next →'}</button>
      </div>`;
  },

  history() {
    if (!state.sessions.length) {
      return `<div class="empty"><h2>No sessions yet</h2><p class="muted">Finished sessions will show up here.</p></div>`;
    }
    const partners = pastPartners();
    const who = partners.includes(state.statsPlayer) ? state.statsPlayer : 'me';
    const picker = partners.length ? `<select data-field="stats-player" aria-label="Show progress for">
        ${[['me', 'You'], ...partners.map((n) => [n, n])].map(([v, label]) =>
          `<option value="${esc(v)}" ${v === who ? 'selected' : ''}>${esc(label)}</option>`).join('')}
      </select>` : '';
    const stats = drillStats(who);
    const statsTable = stats.length || partners.length ? `<section class="card">
        <div class="toolbar"><h3>Drill progress</h3>${picker}</div>
        ${stats.length ? '' : '<p class="muted small">No made/miss results logged yet.</p>'}
        <table class="table">
          <thead><tr><th>Drill</th><th>Sessions</th><th>Overall</th><th>Trend</th></tr></thead>
          <tbody>${stats.map((s) => `<tr>
            <td>${esc(s.name)}</td><td>${s.sessions}</td><td>${pct(s.made, s.attempts)}%</td>
            <td><span class="spark" title="${s.trend.join('%, ')}%">${s.trend.slice(-8).map((v) => `<i style="height:${Math.max(v, 4)}%"></i>`).join('')}</span></td>
          </tr>`).join('')}</tbody>
        </table>
      </section>` : '';
    return `<div class="toolbar"><h2>History</h2></div>${statsTable}${state.sessions.map(sessionCard).join('')}`;
  },

  data() {
    const newCount = state.lastExport
      ? state.sessions.filter((s) => s.endedAt > state.lastExport).length
      : state.sessions.length;
    return `<div class="toolbar"><h2>Your data</h2></div>
      <section class="card">
        <h3>Export to your PC</h3>
        <p class="muted small">${state.sessions.length} sessions saved on this phone.
          ${state.lastExport ? `Last export ${fmtDate(state.lastExport)} · ${newCount} new since then.` : 'Not exported yet.'}</p>
        <p><b>Results spreadsheet (CSV)</b><br><span class="muted small">One row per drill, opens in Excel.</span></p>
        <div class="row">
          <button class="btn primary" data-action="export" data-kind="csv" data-mode="share">Share…</button>
          <button class="btn" data-action="export" data-kind="csv" data-mode="download">Download</button>
        </div>
        <p><b>Full backup (JSON)</b><br><span class="muted small">All plans and sessions. Import it to restore or move to another device.</span></p>
        <div class="row">
          <button class="btn primary" data-action="export" data-kind="json" data-mode="share">Share…</button>
          <button class="btn" data-action="export" data-kind="json" data-mode="download">Download</button>
        </div>
        <p class="muted small">Tip: use <b>Share…</b> to send the file to Gmail, Google Drive or Quick Share. <b>Download</b> saves it to your phone’s Downloads folder.</p>
      </section>

      <section class="card">
        <h3>Import</h3>
        <p class="muted small">Drill plans (.json or .csv) or a backup file.</p>
        <button class="btn" data-action="import">Choose file…</button>
      </section>

      <section class="card">
        <h3>Get plans from Claude</h3>
        <p class="muted small">Copy this prompt, fill in the brackets, and save Claude’s reply as a <code>.json</code> file.</p>
        <pre class="prompt">${esc(CLAUDE_PROMPT)}</pre>
        <button class="btn" data-action="copy-prompt">Copy prompt</button>
      </section>

      <section class="card">
        <h3>Storage</h3>
        <p class="muted small">${state.persisted
          ? '✅ Protected storage is on: the browser won’t clear your data automatically.'
          : '⚠️ Storage isn’t protected yet. Install the app to your home screen (Chrome menu → <b>Add to Home screen</b>) and export backups regularly.'}</p>
      </section>`;
  },
};

function summaryView(a) {
  const rows = a.results.map((r, i) => `<tr class="clickable" data-action="goto" data-i="${i}">
      <td>${esc(r.name)}</td>
      <td>${hitCell(r.hits)} <span class="gold">${starText(r.rating)}</span></td>
      ${a.partner ? `<td>${hitCell(r.partnerHits)} <span class="gold">${starText(r.partnerRating)}</span></td>` : ''}
    </tr>`).join('');
  return `<section class="card">
      <h2>Session summary</h2>
      <table class="table"><thead><tr><th>Drill</th><th>You</th>${a.partner ? `<th>${esc(a.partner)}</th>` : ''}</tr></thead><tbody>${rows}</tbody></table>
    </section>
    <section class="card">
      <label>Overall session</label>
      ${stars(a.rating, 'session')}
      <label for="sessionNotes">Session notes</label>
      <textarea id="sessionNotes" data-field="session-notes" rows="4" placeholder="What went well? What to work on next time?">${esc(a.notes)}</textarea>
    </section>
    <div class="row nav">
      <button class="btn" data-action="prev">← Back</button>
      <button class="btn primary" data-action="finish">Save session</button>
    </div>
    <button class="btn ghost danger block" data-action="discard">Discard session</button>`;
}

function sessionCard(s) {
  const attempts = s.results.reduce((t, r) => t + r.attempts, 0);
  const made = s.results.reduce((t, r) => t + r.made, 0);
  const mins = Math.round((Date.parse(s.endedAt) - Date.parse(s.startedAt)) / 60000);
  const open = state.openSession === s.id;
  return `<article class="card">
    <button class="card-head" data-action="toggle-session" data-id="${s.id}" aria-expanded="${open}">
      <div><h3>${esc(s.planName)}${s.partner ? ` <span class="muted small">· with ${esc(s.partner)}</span>` : ''}</h3>
        <p class="muted small">${fmtDate(s.startedAt)} · ${mins} min${attempts ? ` · ${pct(made, attempts)}% (${made}/${attempts})` : ''}
        <span class="gold">${starText(s.rating)}</span></p></div>
      <span class="chev">${open ? '▾' : '▸'}</span>
    </button>
    ${open ? `<div class="card-body">
      ${s.notes ? `<p class="note">${esc(s.notes)}</p>` : ''}
      <ul class="result-list">${s.results.map((r) => {
        const line = (label, made, attempts, rating, notes) => `
          <div class="rl-head"><span class="small">${label}</span>
            <span class="small">${attempts ? `${made}/${attempts} · ${pct(made, attempts)}%` : ''} <span class="gold">${starText(rating)}</span></span></div>
          ${notes ? `<p class="muted small note">${esc(notes)}</p>` : ''}`;
        return `<li><strong>${esc(r.name)}</strong>
          ${line(s.partner ? 'You' : '', r.made, r.attempts, r.rating, r.notes)}
          ${s.partner ? line(esc(s.partner), r.partnerMade ?? 0, r.partnerAttempts ?? 0, r.partnerRating, r.partnerNotes) : ''}
        </li>`;
      }).join('')}</ul>
      <button class="btn ghost danger" data-action="delete-session" data-id="${s.id}">Delete session</button>
    </div>` : ''}
  </article>`;
}

// who: 'me', or a partner's name
function drillStats(who) {
  const map = new Map();
  for (const s of [...state.sessions].reverse()) { // oldest first, so trends read left→right
    if (who !== 'me' && s.partner !== who) continue;
    for (const r of s.results) {
      const made = who === 'me' ? r.made : r.partnerMade ?? 0;
      const attempts = who === 'me' ? r.attempts : r.partnerAttempts ?? 0;
      if (!attempts) continue;
      const e = map.get(r.name) ?? { name: r.name, sessions: 0, made: 0, attempts: 0, trend: [] };
      e.sessions++;
      e.made += made;
      e.attempts += attempts;
      e.trend.push(pct(made, attempts));
      map.set(r.name, e);
    }
  }
  return [...map.values()].sort((a, b) => b.sessions - a.sessions);
}

function render() {
  document.querySelectorAll('.tabbar button').forEach((b) => {
    b.classList.toggle('on', b.dataset.tab === state.tab);
    b.classList.toggle('live', b.dataset.tab === 'session' && !!state.active);
  });
  $('#view').innerHTML = views[state.tab]();
  updateWakeLock();
}

// ---------- Events ----------
const actions = {
  tab: (el) => { state.tab = el.dataset.tab; render(); scrollTo(0, 0); },
  import: () => $('#fileInput').click(),
  'load-sample': async () => {
    const res = await fetch('sample-plan.json');
    await importText(await res.text(), 'sample-plan.json');
  },
  'toggle-plan': (el) => { state.openPlan = state.openPlan === el.dataset.id ? null : el.dataset.id; render(); },
  'delete-plan': async (el) => {
    const p = planById(el.dataset.id);
    if (!confirm(`Delete plan "${p.name}"? Past sessions are kept.`)) return;
    await db.delete('plans', p.id);
    await loadAll();
    render();
  },
  start: (el) => startSession(el.dataset.id),
  goto: (el) => goTo(+el.dataset.i),
  prev: () => goTo(state.active.current - 1),
  next: () => { currentResult().done = true; goTo(state.active.current + 1); },
  'timer-toggle': toggleTimer,
  'timer-reset': resetTimer,
  hit: (el) => {
    currentResult()[hitsKey(playerOf())].push(el.dataset.v === '1');
    navigator.vibrate?.(30);
    saveActive();
    render();
  },
  undo: () => { currentResult()[hitsKey(playerOf())].pop(); saveActive(); render(); },
  rate: (el) => {
    const [target, key] = el.dataset.scope === 'session'
      ? [state.active, 'rating']
      : [currentResult(), ratingKey(playerOf())];
    const v = +el.dataset.v;
    target[key] = target[key] === v ? 0 : v; // tap the same star again to clear
    saveActive();
    render();
  },
  player: (el) => { state.active.player = el.dataset.v; saveActive(); render(); },
  'edit-partner': () => { state.editPartner = true; render(); $('#partnerName')?.focus(); },
  'save-partner': () => {
    const a = state.active;
    a.partner = $('#partnerName').value.trim();
    a.player = a.partner ? 'partner' : 'me';
    state.editPartner = false;
    saveActive();
    render();
  },
  'add-video': async (el) => {
    const input = prompt('Paste a video link (YouTube, Instagram, etc.):');
    if (input === null) return;
    const url = safeUrl(input);
    if (!url) return toast('That isn’t a valid web link (it should start with https://)', true);
    const title = (prompt('Title for this video (optional):') ?? '').trim();
    const video = { title, url };
    const i = +el.dataset.i;
    const p = planById(el.dataset.plan);
    if (p?.drills[i]) {
      p.drills[i].videos = [...(p.drills[i].videos || []), video];
      await db.put('plans', p);
    }
    const a = state.active;
    if (a?.planId === el.dataset.plan && a.results[i]) {
      a.results[i].videos = [...(a.results[i].videos || []), video];
      await saveActive();
    }
    toast('Video link saved');
    render();
  },
  'remove-video': async (el) => {
    const p = planById(el.dataset.plan);
    const d = p?.drills[+el.dataset.i];
    if (!d || !confirm('Remove this video link?')) return;
    d.videos.splice(+el.dataset.v, 1);
    await db.put('plans', p);
    render();
  },
  finish: finishSession,
  discard: async () => {
    if (!confirm('Discard this session? Nothing from it will be saved.')) return;
    state.active = null;
    await setMeta('active', null);
    render();
  },
  'toggle-session': (el) => { state.openSession = state.openSession === el.dataset.id ? null : el.dataset.id; render(); },
  'delete-session': async (el) => {
    if (!confirm('Delete this session permanently?')) return;
    await db.delete('sessions', el.dataset.id);
    await loadAll();
    render();
  },
  export: (el) => exportFile(el.dataset.kind, el.dataset.mode),
  'copy-prompt': async () => {
    try { await navigator.clipboard.writeText(CLAUDE_PROMPT); toast('Prompt copied'); }
    catch { toast('Couldn’t copy. Select the text and copy it manually.', true); }
  },
};

function bindEvents() {
  document.addEventListener('click', async (e) => {
    const el = e.target.closest('[data-action]');
    if (!el || el.disabled || !actions[el.dataset.action]) return;
    try { await actions[el.dataset.action](el); }
    catch (err) { console.error(err); toast(err.message || 'Something went wrong', true); }
  });
  document.addEventListener('input', (e) => {
    const field = e.target.dataset.field;
    if (!field || !state.active) return;
    if (field === 'drill-notes') currentResult()[notesKey(playerOf())] = e.target.value;
    if (field === 'session-notes') state.active.notes = e.target.value;
    saveActiveSoon();
  });
  document.addEventListener('change', (e) => {
    if (e.target.dataset.field === 'stats-player') { state.statsPlayer = e.target.value; render(); }
  });
  document.addEventListener('keydown', (e) => {
    if (e.target.id === 'partnerName' && e.key === 'Enter') { e.preventDefault(); actions['save-partner'](); }
  });
  document.addEventListener('visibilitychange', () => {
    if (state.active) saveActive();
    updateWakeLock();
  });
  $('#fileInput').addEventListener('change', async (e) => {
    for (const f of e.target.files) {
      try { await importText(await f.text(), f.name); }
      catch (err) { toast(`Couldn’t import ${f.name}: ${err.message}`, true); }
    }
    e.target.value = '';
  });
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('error', isError);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

const CLAUDE_PROMPT = `Create a pickleball drill plan for me.
About me: [skill level, e.g. 3.5] · Focus: [e.g. third-shot drops and resets] · Length: [e.g. 60 min] · Setup: [solo / partner / ball machine]

Return ONLY valid JSON (no extra text) in exactly this format so I can import it into my drill tracker:
{
  "name": "Plan name",
  "description": "1–2 sentences on the session focus",
  "drills": [
    {
      "name": "Drill name",
      "category": "Warm-up | Dinking | Drops | Drives | Volleys | Resets | Serves | Returns | Lobs | Overheads | Footwork",
      "durationMin": 10,
      "reps": 50,
      "target": "Measurable success goal, e.g. 8 of 10 land in the kitchen",
      "instructions": "Setup, how to run it, and key coaching cues",
      "videos": [{ "title": "Video title", "url": "https://..." }]
    }
  ]
}
Only include "videos" with URLs you are certain exist; otherwise use an empty list (the app adds a YouTube search link for every drill).
For several plans at once, wrap them as {"plans": [ ... ]}.`;

// ---------- Start ----------
async function init() {
  bindEvents();
  await loadAll();
  state.active = upgradeActive((await getMeta('active')) ?? null);
  state.lastExport = (await getMeta('lastExport')) ?? null;
  state.persisted = (await navigator.storage?.persisted?.()) ?? false;
  if (state.active) state.tab = 'session';
  render();
  setInterval(tick, 250);
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
}

init();
