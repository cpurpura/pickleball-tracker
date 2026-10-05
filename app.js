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
    const req = indexedDB.open('pickleball-drills', 2);
    req.onupgradeneeded = () => {
      const d = req.result;
      // v1: plans, sessions, meta · v2: library (individual drills)
      for (const name of ['plans', 'sessions', 'meta', 'library']) {
        if (!d.objectStoreNames.contains(name)) d.createObjectStore(name, { keyPath: name === 'meta' ? 'key' : 'id' });
      }
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
  library: [],       // individual drills, usable outside of a plan
  libSelected: new Set(), // library drill ids ticked for a custom session, in tick order
  plansView: 'plans',     // Plans tab: 'plans' or 'drills' (library)
  editDrill: null,        // library drill id being edited, or 'new'
  importTarget: 'plans',
  active: null,      // in-progress session (persisted in meta so it survives closing the app)
  openPlan: null,
  openSession: null,
  lastExport: null,
  persisted: false,
};

async function loadAll() {
  state.plans = (await db.all('plans')).sort((a, b) => a.name.localeCompare(b.name));
  state.sessions = (await db.all('sessions')).sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  state.library = (await db.all('library')).sort((a, b) => a.name.localeCompare(b.name));
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
  if (a.week === undefined) a.week = 1; // null = not part of a program (library session)
  a.programKey ??= a.planId;
  a.programWeeks ??= 0;
  a.rotateMin ??= 0;
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
    // Multi-week program info (shared by all session plans from the same program file)
    program: raw.program ?? (num(pick(p, ['rolerotationminutes', 'rotationmin'])) || num(pick(p, ['durationweeks', 'weeks']))
      ? { id: raw.id, weeks: num(pick(p, ['durationweeks', 'weeks'])), rotationMin: num(pick(p, ['rolerotationminutes', 'rotationmin'])) }
      : undefined),
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

const humanize = (s) => {
  const t = str(s).replace(/_/g, ' ');
  return t.charAt(0).toUpperCase() + t.slice(1);
};

// Plan-level notes for a multi-session program: schedule, progression, regression rule
function programNotes(data) {
  const m = data.plan || {};
  const lines = [];
  const schedule = [
    m.duration_weeks && `${m.duration_weeks} weeks`,
    m.sessions_per_week && `${m.sessions_per_week}× per week`,
    m.session_duration_minutes && `${m.session_duration_minutes} min sessions`,
    m.role_rotation_minutes && `rotate roles every ${m.role_rotation_minutes} min`,
  ].filter(Boolean);
  if (schedule.length) lines.push(schedule.join(' · '));
  if (m.notes || m.description) lines.push(str(m.notes || m.description));
  for (const p of data.progression || []) {
    const weeks = Array.isArray(p.weeks) ? p.weeks.join('–') : p.weeks;
    lines.push(`Weeks ${weeks}: ${str(p.description)}`);
  }
  const rr = data.regression_rule;
  if (rr?.action) lines.push(`If a block scores below ${rr.threshold}: ${str(rr.action)}`);
  return lines.join('\n\n');
}

// { plan: {...}, scoring: {...}, sessions: [{ label, blocks: [...] }] } → one app plan per session
function programPlans(data, fallbackName) {
  const meta = data.plan || {};
  const programName = str(meta.name || data.name) || fallbackName;
  const perSet = num(data.scoring?.attempts_per_set) || 10;
  const faults = data.fault_checklist || {};
  const description = programNotes(data);

  return data.sessions.map((s, si) => {
    const blocks = [...(s.blocks || s.drills)].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    return normPlan({
      id: meta.id && s.id ? `${meta.id}-${s.id}` : undefined,
      name: `${programName} — ${str(s.label || s.name) || `Session ${si + 1}`}`,
      description,
      program: {
        id: meta.id || programName,
        name: programName,
        weeks: num(meta.duration_weeks),
        rotationMin: num(meta.role_rotation_minutes),
      },
      drills: blocks.map((b) => {
        const tags = b.tags || [];
        const unit = b.goal_unit ? humanize(String(b.goal_unit).replace(/_?out_of_\d+$/, '')).toLowerCase() : 'makes';
        const faultList = Object.entries(faults)
          .filter(([k]) => tags.includes(k))
          .flatMap(([, list]) => list);
        return {
          name: b.name,
          category: b.category || (tags.includes('warmup') ? 'Warm-up' : humanize(tags[0])),
          durationMin: b.duration_minutes ?? b.durationMin,
          target: b.goal != null ? `${b.goal}/${perSet} ${unit}` : b.target || (b.scored === false ? 'Unscored' : ''),
          instructions: [
            b.description || b.instructions,
            b.coaching_point && `Coaching point: ${b.coaching_point}`,
            faultList.length && `Common faults:\n${faultList.map((f) => `• ${f}`).join('\n')}`,
          ].filter(Boolean).join('\n\n'),
          videos: b.videos,
        };
      }),
    }, fallbackName);
  });
}

function parseImport(text, filename) {
  text = text.replace(/^﻿/, '').trim();
  const fallbackName = filename.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ');
  if (!/^[[{]/.test(text)) return { plans: csvPlans(text, fallbackName), sessions: [], library: [] };

  const data = JSON.parse(text);
  if (data?.type === 'pickleball-drill-backup') {
    return {
      plans: (data.plans || []).map((p) => normPlan(p, fallbackName)),
      sessions: data.sessions || [],
      library: (data.library || []).map(normDrill).filter((d) => d.name),
    };
  }
  if (Array.isArray(data?.sessions) && data.sessions.some((s) => Array.isArray(s?.blocks || s?.drills))) {
    return { plans: programPlans(data, fallbackName), sessions: [], library: [] };
  }
  let rawPlans;
  if (Array.isArray(data)) {
    // Either a list of plans, or a bare list of drills
    rawPlans = data.every((x) => x && (x.drills || x.Drills)) ? data : [{ name: fallbackName, drills: data }];
  } else if (Array.isArray(data.plans)) rawPlans = data.plans;
  else if (!data.drills && !data.Drills) rawPlans = [{ name: fallbackName, drills: [data] }]; // a single drill
  else rawPlans = [data];
  return { plans: rawPlans.map((p) => normPlan(p, fallbackName)), sessions: [], library: [] };
}

const mergeVideos = (a = [], b = []) => [...a, ...b.filter((v) => !a.some((x) => x.url === v.url))];

// Add drills to the library; a drill with the same name as an existing one updates it
async function addToLibrary(drills) {
  const byName = new Map(state.library.map((d) => [d.name.toLowerCase(), d]));
  let added = 0;
  let updated = 0;
  for (const d of drills) {
    const key = d.name.toLowerCase();
    const existing = byName.get(key); // also catches repeats within this batch
    const { id, addedAt, libraryId, ...fields } = d;
    const item = {
      ...fields,
      id: existing?.id || uid(),
      videos: mergeVideos(existing?.videos, d.videos),
      addedAt: existing?.addedAt || new Date().toISOString(),
    };
    await db.put('library', item);
    byName.set(key, item);
    if (existing) updated++; else added++;
  }
  await loadAll();
  return { added, updated };
}

async function importText(text, filename) {
  const parsed = parseImport(text, filename);
  if (state.importTarget === 'library') {
    // Pull every drill out of the file (plan, drill list, or single drill) into the library
    const { added, updated } = await addToLibrary([...parsed.plans.flatMap((p) => p.drills), ...parsed.library]);
    toast(`Drill library: ${added} added${updated ? `, ${updated} updated` : ''}`);
    state.tab = 'plans';
    state.plansView = 'drills';
    render();
    return;
  }
  const { plans, sessions } = parsed;
  if (parsed.library.length) await addToLibrary(parsed.library);
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
  if (parsed.library.length) parts.push(`${parsed.library.length} library drill${parsed.library.length > 1 ? 's' : ''}`);
  toast(`Imported ${parts.join(' and ')}`);
  if (plans.length) { state.tab = 'plans'; state.openPlan = plans[plans.length - 1].id; }
  render();
}

// ---------- Export ----------
const CSV_COLS = ['session_date', 'start_time', 'week', 'plan', 'drill_no', 'drill', 'category', 'target',
  'planned_min', 'actual_min', 'attempts', 'made', 'success_pct', 'drill_rating', 'drill_notes',
  'partner', 'partner_attempts', 'partner_made', 'partner_success_pct', 'partner_rating', 'partner_notes',
  'session_rating', 'session_notes', 'session_id'];

function sessionsToCSV(sessions) {
  const cell = (v) => { const s = String(v ?? ''); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const rows = [CSV_COLS];
  for (const s of [...sessions].reverse()) {
    const d = new Date(s.startedAt);
    s.results.forEach((r, i) => rows.push([
      localDate(d), localTime(d), s.week ?? '', s.planName, i + 1, r.name, r.category, r.target,
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
  plans: state.plans, sessions: state.sessions, library: state.library,
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
const DAY_MS = 86_400_000;
const startOfDay = (t) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };

// Program week for a new session: counted from your most recent session in the same program
// (Session A and B plans from one program share a key), so a corrected week carries forward.
function suggestWeek(programKey, maxWeeks) {
  const last = state.sessions.find((s) => (s.programKey || s.planId) === programKey && s.week);
  if (!last) return 1;
  const programStart = startOfDay(last.startedAt) - (last.week - 1) * 7 * DAY_MS;
  const week = Math.floor(Math.round((startOfDay(Date.now()) - programStart) / DAY_MS) / 7) + 1;
  return maxWeeks ? Math.min(week, maxWeeks) : week;
}

function startSession(planId) {
  const p = planById(planId);
  return beginSession({ planId: p.id, planName: p.name, drills: p.drills, program: p.program, programKey: p.program?.id || p.id });
}

// Run drills from the library, in the order given
function startLibrarySession(ids) {
  const drills = ids.map((id) => state.library.find((d) => d.id === id)).filter(Boolean)
    .map(({ id, addedAt, ...d }) => ({ ...d, libraryId: id }));
  if (!drills.length) return;
  const planName = drills.length === 1 ? drills[0].name : `Custom session · ${drills.length} drills`;
  return beginSession({ planId: 'library', planName, drills, program: null, programKey: null });
}

async function beginSession({ planId, planName, drills, program, programKey }) {
  if (state.active && !confirm('A session is already in progress. Discard it and start a new one?')) return;
  const programWeeks = program?.weeks || 0;
  stopAlarm();
  state.active = {
    id: uid(), planId, planName, startedAt: new Date().toISOString(),
    current: 0, timerStartedAt: null, rating: 0, notes: '', partner: '', player: 'me',
    programKey, programWeeks, week: programKey ? suggestWeek(programKey, programWeeks) : null,
    rotateMin: program?.rotationMin || 0,
    results: drills.map((d) => ({
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
let lastElapsed = null; // drill seconds at the previous tick; cues fire when a threshold is crossed
let alarmTimer = null; // interval that repeats the time-up alarm until stopped
let alarmStarted = 0;
const ALARM_MAX_MS = 60_000;

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
  lastElapsed = null;
  stopAlarm();
}

function toggleTimer() {
  const a = state.active;
  audioCtx ??= new (window.AudioContext || window.webkitAudioContext)(); // must be created from a tap
  audioCtx.resume?.();
  if (a.timerStartedAt) pauseTimer();
  else { a.timerStartedAt = Date.now(); lastElapsed = null; }
  saveActive();
  render();
}

function resetTimer() {
  state.active.timerStartedAt = null;
  currentResult().elapsedSec = 0;
  lastElapsed = null;
  stopAlarm();
  saveActive();
  render();
}

// ----- Sounds (Web Audio, so nothing to download and it works offline) -----
function tone(freq, at, dur, { vol = 0.3, type = 'sine' } = {}) {
  if (!audioCtx) return;
  if (audioCtx.state === 'suspended') audioCtx.resume();
  const o = audioCtx.createOscillator();
  const g = audioCtx.createGain();
  o.type = type;
  o.frequency.value = freq;
  o.connect(g).connect(audioCtx.destination);
  const s = audioCtx.currentTime + at;
  g.gain.setValueAtTime(0.0001, s);
  g.gain.exponentialRampToValueAtTime(vol, s + 0.01);
  g.gain.setValueAtTime(vol, s + dur - 0.03);
  g.gain.exponentialRampToValueAtTime(0.0001, s + dur);
  o.start(s);
  o.stop(s + dur + 0.02);
}

function oneMinuteWarning() {
  tone(660, 0, 0.15, { vol: 0.5 });
  tone(660, 0.22, 0.15, { vol: 0.5 });
  navigator.vibrate?.([150, 100, 150]);
}

function countdownTick() {
  tone(880, 0, 0.1, { vol: 0.45 });
  navigator.vibrate?.(80);
}

// One burst of the time-up alarm: loud, alternating square-wave beeps
function alarmBurst() {
  [0, 0.2, 0.4, 0.6].forEach((t, i) => tone(i % 2 ? 784 : 1047, t, 0.17, { vol: 0.6, type: 'square' }));
  navigator.vibrate?.([300, 100, 300]);
}

function startAlarm() {
  stopAlarm();
  alarmStarted = Date.now();
  alarmBurst();
  alarmTimer = setInterval(() => {
    if (Date.now() - alarmStarted > ALARM_MAX_MS) return stopAlarm();
    alarmBurst();
  }, 1500);
  $('#alarmBar').hidden = false;
}

function stopAlarm() {
  clearInterval(alarmTimer);
  alarmTimer = null;
  navigator.vibrate?.(0);
  const bar = $('#alarmBar');
  if (bar) bar.hidden = true;
}

// Rising three-note chime: switch hitter/feeder roles
function switchRoles() {
  tone(523, 0, 0.18, { vol: 0.5 });
  tone(659, 0.2, 0.18, { vol: 0.5 });
  tone(784, 0.4, 0.35, { vol: 0.5 });
  navigator.vibrate?.([100, 80, 100, 80, 250]);
  const a = state.active;
  if (a.partner) {
    // Flip who taps count for, since the other player is now hitting
    a.player = a.player === 'me' ? 'partner' : 'me';
    saveActive();
    render();
    toast(`🔁 Switch roles · now logging for ${a.player === 'me' ? 'you' : a.partner}`);
  } else {
    toast('🔁 Switch roles');
  }
}

// Fire each cue once, when the drill time crosses its threshold while the timer runs
function timerCues(r) {
  const a = state.active;
  if (!a.timerStartedAt) { lastElapsed = null; return; }
  const e = elapsed(r);
  const prev = lastElapsed;
  lastElapsed = e;
  if (prev === null) return;
  const total = r.durationMin * 60;
  if (total) {
    const crossed = (leftAt) => total - prev > leftAt && total - e <= leftAt;
    if (crossed(0)) return startAlarm();
    if ([1, 2, 3].some(crossed)) return countdownTick();
    if (total > 90 && crossed(60)) return oneMinuteWarning();
  }
  const rot = a.rotateMin * 60;
  const nearEnd = total && total - e < 10; // the time-up alarm covers the end of the drill
  if (rot > 0 && Math.floor(e / rot) > Math.floor(prev / rot) && !nearEnd) switchRoles();
}

function tick() {
  const r = currentResult();
  if (!r) return;
  const c = clockState(r);
  timerCues(r);
  const el = document.getElementById('clock');
  if (el) { el.textContent = c.text; el.classList.toggle('over', c.over); }
  const info = document.getElementById('rotateInfo');
  if (info) {
    const rot = state.active.rotateMin * 60;
    info.textContent = rot && state.active.timerStartedAt
      ? `Next switch in ${clock(Math.ceil(rot - (elapsed(r) % rot)))}` : '';
  }
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

// Plans shipped with the app (cached for offline use by sw.js); name must match the imported plan name's start
const BUILT_IN_PLANS = [
  { file: 'plans/third-shot-drilling-plan.json', name: 'Third Shot Drops & Drives', about: '4-week program · Session A (drops) and Session B (drives and shot selection)' },
  { file: 'sample-plan.json', name: 'Soft Game Fundamentals', about: 'Single 60-minute session: dinks, drops and resets' },
];

// ---------- Views ----------
const stars = (value, scope) => `<div class="stars" role="group" aria-label="Rating">${[1, 2, 3, 4, 5].map((n) =>
  `<button class="star ${n <= value ? 'on' : ''}" data-action="rate" data-scope="${scope}" data-v="${n}" aria-label="${n} of 5">★</button>`).join('')}</div>`;

// src says where a drill's videos are saved: { plan, i } for a plan drill, { drill } for a library drill
const srcAttrs = (src) => (src.drill ? `data-drill="${esc(src.drill)}"` : `data-plan="${esc(src.plan)}" data-i="${src.i}"`);

function videoLinks(d, src, removable = false) {
  const saved = (d.videos || []).map((v, vi) => `<li>
    <a href="${esc(v.url)}" target="_blank" rel="noopener noreferrer">▶ ${esc(v.title || hostLabel(v.url))}</a>
    ${removable ? `<button class="link danger" data-action="remove-video" ${srcAttrs(src)} data-v="${vi}" aria-label="Remove video">✕</button>` : ''}
  </li>`).join('');
  return `<ul class="video-list">${saved}
    <li><a href="${esc(youtubeSearch(d.name))}" target="_blank" rel="noopener noreferrer">🔎 Search YouTube for this drill</a></li>
    <li><button class="link" data-action="add-video" ${srcAttrs(src)}>＋ Add video link</button></li>
  </ul>`;
}

function videoSource(el) {
  if (el.dataset.drill) {
    const d = state.library.find((x) => x.id === el.dataset.drill);
    return d && { drill: d, save: () => db.put('library', d) };
  }
  const p = planById(el.dataset.plan);
  const d = p?.drills[+el.dataset.i];
  return d && { drill: d, save: () => db.put('plans', p) };
}

const inLibrary = (name) => state.library.some((d) => d.name.toLowerCase() === name.toLowerCase());

function drillForm(d) {
  const categories = [...new Set([...state.library, ...state.plans.flatMap((p) => p.drills)]
    .map((x) => x.category).filter(Boolean))].sort();
  return `<section class="card form">
    <h3>${d.id ? 'Edit drill' : 'New drill'}</h3>
    <label for="df-name">Name *</label>
    <input id="df-name" value="${esc(d.name)}" placeholder="e.g. Cross-court dinks" autocomplete="off">
    <label for="df-category">Category</label>
    <input id="df-category" list="catList" value="${esc(d.category)}" placeholder="e.g. Dinking" autocomplete="off">
    <datalist id="catList">${categories.map((c) => `<option value="${esc(c)}"></option>`).join('')}</datalist>
    <div class="grid2">
      <div><label for="df-duration">Minutes</label>
        <input id="df-duration" type="number" inputmode="decimal" min="0" step="0.5" value="${d.durationMin || ''}" placeholder="blank = stopwatch"></div>
      <div><label for="df-reps">Rep goal</label>
        <input id="df-reps" type="number" inputmode="numeric" min="0" value="${d.reps || ''}"></div>
    </div>
    <label for="df-target">Target</label>
    <input id="df-target" value="${esc(d.target)}" placeholder="e.g. 7 of 10 land in the kitchen" autocomplete="off">
    <label for="df-instructions">Instructions</label>
    <textarea id="df-instructions" rows="4" placeholder="Setup, how to run it, coaching cues">${esc(d.instructions)}</textarea>
    ${d.id ? '' : `<label for="df-video">Video link <span class="muted small">(optional)</span></label>
      <input id="df-video" type="url" inputmode="url" placeholder="https://…" autocomplete="off">`}
    <div class="row">
      <button class="btn primary" data-action="save-drill">Save drill</button>
      <button class="btn" data-action="cancel-drill">Cancel</button>
    </div>
  </section>`;
}

function libraryView() {
  if (state.editDrill) {
    const d = state.editDrill === 'new' ? {} : state.library.find((x) => x.id === state.editDrill) || {};
    return drillForm(d);
  }
  const selected = [...state.libSelected].filter((id) => state.library.some((d) => d.id === id));
  const filter = (state.libFilter || '').toLowerCase();
  const items = state.library.map((d) => {
    const open = state.openDrill === d.id;
    const order = selected.indexOf(d.id);
    const search = `${d.name} ${d.category}`.toLowerCase();
    return `<article class="card lib-item" data-search="${esc(search)}" ${filter && !search.includes(filter) ? 'hidden' : ''}>
      <div class="lib-row">
        <label class="pick ${order >= 0 ? 'on' : ''}" aria-label="Select ${esc(d.name)}">
          <input type="checkbox" data-field="lib-select" value="${esc(d.id)}" ${order >= 0 ? 'checked' : ''}>
          <span>${order >= 0 ? order + 1 : ''}</span>
        </label>
        <button class="card-head" data-action="toggle-drill" data-id="${esc(d.id)}" aria-expanded="${open}">
          <div><h3>${esc(d.name)}</h3><p class="muted small">${[d.category, d.durationMin && `${d.durationMin} min`, d.reps && `${d.reps} reps`].filter(Boolean).map(esc).join(' · ') || 'Drill'}</p></div>
          <span class="chev">${open ? '▾' : '▸'}</span>
        </button>
      </div>
      ${open ? `<div class="card-body">
        ${d.target ? `<p class="small"><b>Target:</b> ${esc(d.target)}</p>` : ''}
        ${d.instructions ? `<p class="muted small note">${esc(d.instructions)}</p>` : ''}
        ${videoLinks(d, { drill: d.id }, true)}
        <div class="row">
          <button class="btn primary" data-action="start-drill" data-id="${esc(d.id)}">▶ Start</button>
          <button class="btn" data-action="edit-drill" data-id="${esc(d.id)}">Edit</button>
          <button class="btn ghost danger" data-action="delete-drill" data-id="${esc(d.id)}">Delete</button>
        </div>
      </div>` : ''}
    </article>`;
  }).join('');

  return `<div class="toolbar">
      <h2>Drill library</h2>
      <div class="row tight">
        <button class="btn" data-action="import" data-target="library">Import</button>
        <button class="btn primary" data-action="new-drill">＋ New</button>
      </div>
    </div>
    ${state.library.length ? `
      ${state.library.length > 5 ? `<input class="filter" data-field="lib-filter" type="search" placeholder="Filter by name or category" value="${esc(state.libFilter || '')}" aria-label="Filter drills">` : ''}
      <p class="muted small">Tick drills to run several together. They run in the order you tick them.</p>
      ${items}` : `<div class="empty">
        <p>No drills in your library yet.</p>
        <p class="muted small">Add a drill yourself, import a file of drills (JSON or CSV, or any plan file), or open a plan and tap <b>＋ Add to drill library</b> on a drill.</p>
        <button class="btn primary" data-action="new-drill">＋ New drill</button>
      </div>`}
    ${selected.length ? `<div class="select-bar">
      <button class="btn primary" data-action="start-selected">▶ Start ${selected.length} drill${selected.length > 1 ? 's' : ''}</button>
      <button class="btn" data-action="clear-selected">Clear</button>
    </div>` : ''}`;
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
    const sub = `<div class="seg subtabs" role="tablist">
      <button role="tab" class="${state.plansView !== 'drills' ? 'on' : ''}" data-action="plans-view" data-v="plans" aria-selected="${state.plansView !== 'drills'}">Plans</button>
      <button role="tab" class="${state.plansView === 'drills' ? 'on' : ''}" data-action="plans-view" data-v="drills" aria-selected="${state.plansView === 'drills'}">Drill library${state.library.length ? ` (${state.library.length})` : ''}</button>
    </div>`;
    if (state.plansView === 'drills') return sub + libraryView();

    const list = state.plans.map((p) => {
      const mins = p.drills.reduce((t, d) => t + (d.durationMin || 0), 0);
      const open = state.openPlan === p.id;
      return `<article class="card">
        <button class="card-head" data-action="toggle-plan" data-id="${p.id}" aria-expanded="${open}">
          <div><h3>${esc(p.name)}</h3><p class="muted small">${p.drills.length} drill${p.drills.length === 1 ? '' : 's'}${mins ? ` · ${mins} min` : ''}${p.program?.weeks ? ` · ${p.program.weeks}-week program` : ''}</p></div>
          <span class="chev">${open ? '▾' : '▸'}</span>
        </button>
        ${open ? `<div class="card-body">
          ${p.description ? `<p class="note">${esc(p.description)}</p>` : ''}
          <ol class="drill-list">${p.drills.map((d, i) => `<li>
            <strong>${esc(d.name)}</strong>${drillMeta(d)}
            ${d.target ? `<p class="small"><b>Target:</b> ${esc(d.target)}</p>` : ''}
            ${d.instructions ? `<p class="muted small note">${esc(d.instructions)}</p>` : ''}
            ${videoLinks(d, { plan: p.id, i }, true)}
            ${inLibrary(d.name)
              ? '<p class="muted small">✓ In drill library</p>'
              : `<button class="link small" data-action="save-to-library" data-plan="${esc(p.id)}" data-i="${i}">＋ Add to drill library</button>`}
          </li>`).join('')}</ol>
          <div class="row">
            <button class="btn primary" data-action="start" data-id="${p.id}">▶ Start session</button>
            <button class="btn ghost danger" data-action="delete-plan" data-id="${p.id}">Delete</button>
          </div>
        </div>` : ''}
      </article>`;
    }).join('');

    return `${sub}<div class="toolbar">
        <h2>Drill plans</h2>
        <button class="btn primary" data-action="import" data-target="plans">＋ Import</button>
      </div>
      ${list || `<div class="empty">
        <p>No plans yet.</p>
        <p class="muted small">Add a built-in plan below, or ask Claude for a drill plan (see the <b>Data</b> tab for a ready-made prompt), save it as a <code>.json</code> file, then tap <b>Import</b>.</p>
      </div>`}
      <section class="card">
        <h3>Built-in plans</h3>
        ${BUILT_IN_PLANS.map((b) => {
          const loaded = state.plans.some((p) => p.name.startsWith(b.name));
          return `<div class="builtin-row">
            <div><b>${esc(b.name)}</b><p class="muted small">${esc(b.about)}</p></div>
            <button class="btn ${loaded ? '' : 'primary'}" data-action="load-builtin" data-file="${esc(b.file)}">${loaded ? 'Reload' : '＋ Add'}</button>
          </div>`;
        }).join('')}
      </section>`;
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
      <span class="muted small">${a.current < a.results.length ? `Drill ${a.current + 1} of ${a.results.length}` : 'Summary'}</span></div>
      ${a.week ? `<p class="partner-line week-line">📅 Week <b>${a.week}</b>${a.programWeeks ? ` of ${a.programWeeks}` : ''}
        <button class="link step" data-action="week" data-d="-1" aria-label="Previous week" ${a.week <= 1 ? 'disabled' : ''}>−</button>
        <button class="link step" data-action="week" data-d="1" aria-label="Next week">＋</button></p>` : ''}`;

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
        ${r.instructions ? `<p class="muted note">${esc(r.instructions)}</p>` : ''}
        <details class="videos"><summary>🎬 Videos${r.videos.length ? ` (${r.videos.length})` : ''}</summary>
          ${videoLinks(r, r.libraryId ? { drill: r.libraryId } : { plan: a.planId, i: a.current })}
        </details>
      </article>

      <div class="card timer">
        <div id="clock" class="clock ${c.over ? 'over' : ''}">${c.text}</div>
        <p class="muted small">${r.durationMin ? `${r.durationMin} min drill` : 'Stopwatch'}</p>
        <div class="row center">
          <button class="btn primary" data-action="timer-toggle">${a.timerStartedAt ? '⏸ Pause' : r.elapsedSec ? '▶ Resume' : '▶ Start'}</button>
          <button class="btn ghost" data-action="timer-reset">Reset</button>
        </div>
        <div class="rotate">
          <label for="rotateSel">🔁 Switch roles</label>
          <select id="rotateSel" data-field="rotate">${[...new Set([0, 1, 2, 3, 4, 5, 6, 8, 10, a.rotateMin])].sort((x, y) => x - y)
            .map((m) => `<option value="${m}" ${m === a.rotateMin ? 'selected' : ''}>${m ? `every ${m} min` : 'off'}</option>`).join('')}</select>
        </div>
        <p id="rotateInfo" class="muted small"></p>
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
        <p class="muted small">${s.week ? `Week ${s.week} · ` : ''}${fmtDate(s.startedAt)} · ${mins} min${attempts ? ` · ${pct(made, attempts)}% (${made}/${attempts})` : ''}
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
  import: (el) => { state.importTarget = el.dataset.target || 'plans'; $('#fileInput').click(); },
  'load-builtin': async (el) => {
    const file = el.dataset.file;
    if (!BUILT_IN_PLANS.some((b) => b.file === file)) return;
    const res = await fetch(file);
    if (!res.ok) throw new Error('Couldn’t load that plan. Check your connection and try again.');
    state.importTarget = 'plans';
    // Reloading replaces the plan, keeping any video links you added
    await importText(await res.text(), file.split('/').pop());
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
  'stop-alarm': stopAlarm,
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
  week: (el) => {
    const a = state.active;
    a.week = Math.max(1, a.week + +el.dataset.d);
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
    const src = videoSource(el);
    if (src) {
      src.drill.videos = [...(src.drill.videos || []), video];
      await src.save();
    }
    // Show it in the running session too, if that session includes this drill
    const a = state.active;
    if (a) {
      a.results.forEach((r, ri) => {
        const same = el.dataset.drill
          ? r.libraryId === el.dataset.drill
          : a.planId === el.dataset.plan && ri === +el.dataset.i;
        if (same) r.videos = [...(r.videos || []), video];
      });
      await saveActive();
    }
    toast('Video link saved');
    render();
  },
  'remove-video': async (el) => {
    const src = videoSource(el);
    if (!src || !confirm('Remove this video link?')) return;
    src.drill.videos.splice(+el.dataset.v, 1);
    await src.save();
    render();
  },
  'plans-view': (el) => { state.plansView = el.dataset.v; state.editDrill = null; render(); scrollTo(0, 0); },
  'new-drill': () => { state.editDrill = 'new'; render(); scrollTo(0, 0); $('#df-name')?.focus(); },
  'edit-drill': (el) => { state.editDrill = el.dataset.id; render(); scrollTo(0, 0); },
  'cancel-drill': () => { state.editDrill = null; render(); },
  'save-drill': async () => {
    const val = (sel) => $(sel)?.value.trim() ?? '';
    const name = val('#df-name');
    if (!name) return toast('Give the drill a name', true);
    const editing = state.editDrill === 'new' ? null : state.library.find((d) => d.id === state.editDrill);
    if (state.library.some((d) => d.name.toLowerCase() === name.toLowerCase() && d.id !== editing?.id)) {
      return toast('A drill with that name is already in your library', true);
    }
    let videos = editing?.videos || [];
    const videoInput = val('#df-video');
    if (videoInput) {
      const url = safeUrl(videoInput);
      if (!url) return toast('The video link should start with https://', true);
      videos = [{ title: '', url }];
    }
    const drill = {
      id: editing?.id || uid(),
      name,
      category: val('#df-category'),
      durationMin: num(val('#df-duration')),
      reps: num(val('#df-reps')),
      target: val('#df-target'),
      instructions: val('#df-instructions'),
      videos,
      addedAt: editing?.addedAt || new Date().toISOString(),
    };
    await db.put('library', drill);
    await loadAll();
    state.editDrill = null;
    state.openDrill = drill.id;
    render();
    toast('Drill saved');
  },
  'delete-drill': async (el) => {
    const d = state.library.find((x) => x.id === el.dataset.id);
    if (!d || !confirm(`Delete "${d.name}" from your library? Past sessions are kept.`)) return;
    await db.delete('library', d.id);
    state.libSelected.delete(d.id);
    await loadAll();
    render();
  },
  'toggle-drill': (el) => { state.openDrill = state.openDrill === el.dataset.id ? null : el.dataset.id; render(); },
  'start-drill': (el) => startLibrarySession([el.dataset.id]),
  'start-selected': async () => {
    await startLibrarySession([...state.libSelected]);
    if (state.active?.planId === 'library') state.libSelected.clear();
  },
  'clear-selected': () => { state.libSelected.clear(); render(); },
  'save-to-library': async (el) => {
    const d = planById(el.dataset.plan)?.drills[+el.dataset.i];
    if (!d) return;
    await addToLibrary([d]);
    toast(`Added “${d.name}” to your drill library`);
    render();
  },
  finish: finishSession,
  discard: async () => {
    if (!confirm('Discard this session? Nothing from it will be saved.')) return;
    stopAlarm();
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
    if (field === 'lib-filter') {
      // Filter in place so the search box keeps focus while typing
      state.libFilter = e.target.value;
      const q = state.libFilter.toLowerCase();
      document.querySelectorAll('.lib-item').forEach((el) => { el.hidden = !el.dataset.search.includes(q); });
      return;
    }
    if (!field || !state.active) return;
    if (field === 'drill-notes') currentResult()[notesKey(playerOf())] = e.target.value;
    if (field === 'session-notes') state.active.notes = e.target.value;
    saveActiveSoon();
  });
  document.addEventListener('change', (e) => {
    if (e.target.dataset.field === 'stats-player') { state.statsPlayer = e.target.value; render(); }
    if (e.target.dataset.field === 'lib-select') {
      // Re-adding moves a drill to the end, so the run order follows the order you tick
      if (e.target.checked) state.libSelected.add(e.target.value);
      else state.libSelected.delete(e.target.value);
      render();
    }
    if (e.target.dataset.field === 'rotate' && state.active) {
      state.active.rotateMin = +e.target.value;
      saveActive();
      render();
    }
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
