require('dotenv').config();

// Provider modules log several lines per stream request. The TUI pipes this
// output into reader threads, so the chatter costs latency and CPU for no
// benefit. Keep only this file's own startup/diagnostic messages; set
// TMDB_SERVER_VERBOSE=1 to see full provider logging again. Warnings and
// errors (console.warn / console.error) are always shown.
const SERVER_VERBOSE = /^(1|true|yes|on)$/i.test(process.env.TMDB_SERVER_VERBOSE || '');
const serverLog = console.log.bind(console);
if (!SERVER_VERBOSE) console.log = () => {};

const express = require('express');
const cors = require('cors');
const os = require('os');

// Reuse upstream TLS/TCP connections where possible. Providers make several
// requests per scrape, so keep-alive removes repeated handshakes. (Recent
// Node versions default to keep-alive already; this keeps older ones fast.)
const http = require('http');
const https = require('https');
http.globalAgent.keepAlive = true;
https.globalAgent.keepAlive = true;
const { config, saveConfigPatch, OVERRIDE_PATH } = require('./utils/config');
const { authenticate, issueSession, requireAuth, getSession, updatePassword } = require('./utils/auth');
const path = require('path');
const { listProviders, getProvider, getCookieStats } = require('./providers/registry');
const { createProxyRoutes, processStreamsForProxy } = require('./proxy/proxyServer');
const { resolveImdbId } = require('./utils/tmdb');
const { applyFilters } = require('./utils/streamFilters');

const app = express();
app.set('trust proxy', 1);

// Conditionally mount proxy routes early so downstream handlers can use them
if (config.enableProxy) {
  serverLog('[startup] enableProxy flag active: mounting proxy routes');
  createProxyRoutes(app);
} else {
  serverLog('[startup] enableProxy flag disabled: proxy routes not mounted');
}

// --- Simple In-Memory Rate Limiting for /auth/login ---
const loginAttempts = new Map(); // key: ip, value: { count, first, last, lockedUntil }
const MAX_ATTEMPTS_WINDOW = 5; // attempts allowed
const WINDOW_MS = 10 * 60 * 1000; // 10 minutes window
const BASE_LOCK_MS = 5 * 60 * 1000; // 5 minutes base lock

function getClientIp(req){
  return (req.headers['x-forwarded-for'] || req.connection.remoteAddress || '').split(',')[0].trim();
}

function recordLoginFailure(ip){
  const now = Date.now();
  let entry = loginAttempts.get(ip);
  if (!entry) {
    entry = { count:1, first: now, last: now, lockedUntil:0 };
    loginAttempts.set(ip, entry);
    return entry;
  }
  // Reset window if outside timeframe and not locked
  if (now - entry.first > WINDOW_MS && now > entry.lockedUntil) {
    entry.count = 1;
    entry.first = now;
  } else {
    entry.count++;
  }
  entry.last = now;
  if (entry.count > MAX_ATTEMPTS_WINDOW) {
    // Exponential backoff lock: base * 2^(count - limit)
    const over = entry.count - MAX_ATTEMPTS_WINDOW;
    const lockMs = BASE_LOCK_MS * Math.min(8, Math.pow(2, over-1));
    entry.lockedUntil = now + lockMs;
  }
  return entry;
}

function canAttempt(ip){
  const entry = loginAttempts.get(ip);
  if (!entry) return { allowed:true };
  const now = Date.now();
  if (entry.lockedUntil && now < entry.lockedUntil) {
    return { allowed:false, retryAfter: Math.ceil((entry.lockedUntil - now)/1000) };
  }
  if (now - entry.first > WINDOW_MS) {
    // Window passed; reset
    loginAttempts.delete(ip);
    return { allowed:true };
  }
  return { allowed:true };
}

function recordLoginSuccess(ip){
  // On success clear state to avoid lingering count
  loginAttempts.delete(ip);
}

// Guard against premature process.exit from imported legacy modules, but allow controlled restarts
const realProcessExit = process.exit.bind(process);
let allowControlledExit = false;
process.exit = function(code){
  if (allowControlledExit) return realProcessExit(code);
  console.warn('[diagnostic] Intercepted process.exit with code', code, new Error('exit trace').stack);
  // keep process alive for debugging
};
setImmediate(()=>serverLog('[diagnostic] post-start setImmediate fired'));
app.use(cors());
app.use(express.json());

// --- Auth Routes (login before static serving) ---
app.post('/auth/login', (req,res) => {
  const { username, password } = req.body || {};
  const ip = getClientIp(req);
  const attemptState = canAttempt(ip);
  if (!attemptState.allowed) {
    res.setHeader('Retry-After', String(attemptState.retryAfter));
    return res.status(429).json({ success:false, error:'TOO_MANY_ATTEMPTS', retryAfter: attemptState.retryAfter });
  }
  if (!username || !password) return res.status(400).json({ success:false, error:'MISSING_CREDENTIALS' });
  if (!authenticate(username, password)) {
    const entry = recordLoginFailure(ip);
    if (entry.lockedUntil && Date.now() < entry.lockedUntil) {
      const retryAfter = Math.ceil((entry.lockedUntil - Date.now())/1000);
      res.setHeader('Retry-After', String(retryAfter));
      return res.status(429).json({ success:false, error:'LOCKED', retryAfter });
    }
    return res.status(401).json({ success:false, error:'INVALID_CREDENTIALS', remaining: Math.max(0, MAX_ATTEMPTS_WINDOW - entry.count) });
  }
  recordLoginSuccess(ip);
  const token = issueSession(username);
  res.setHeader('Set-Cookie', `session=${token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=${12*60*60}`);
  res.json({ success:true, username });
});

app.post('/auth/logout', (req,res) => {
  res.setHeader('Set-Cookie', 'session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0');
  res.json({ success:true });
});

app.get('/auth/session', (req,res) => {
  const sess = getSession(req);
  if (!sess) return res.json({ authenticated:false });
  res.json({ authenticated:true, username: sess.u });
});

app.post('/auth/change-password', requireAuth, (req,res) => {
  const { oldPassword, newPassword } = req.body || {};
  if (!oldPassword || !newPassword) return res.status(400).json({ success:false, error:'MISSING_FIELDS' });
  const sess = req.session;
  if (!authenticate(sess.u, oldPassword)) return res.status(401).json({ success:false, error:'INVALID_OLD_PASSWORD' });
  if (newPassword.length < 8) return res.status(400).json({ success:false, error:'PASSWORD_TOO_SHORT' });
  if (!updatePassword(sess.u, newPassword)) return res.status(500).json({ success:false, error:'UPDATE_FAILED' });
  res.json({ success:true, message:'PASSWORD_UPDATED' });
});

// Protect config panel (HTML) explicitly before static middleware
app.get('/config.html', (req,res,next) => {
  const sess = getSession(req);
  if (!sess) return res.redirect(302, '/');
  res.setHeader('Cache-Control','no-store, must-revalidate');
  res.setHeader('Pragma','no-cache');
  res.setHeader('Expires','0');
  res.sendFile(path.join(process.cwd(),'public','config.html'));
});

// Explicit root handler for login page to ensure no-store
app.get('/', (req,res) => {
  res.setHeader('Cache-Control','no-store, must-revalidate');
  res.setHeader('Pragma','no-cache');
  res.setHeader('Expires','0');
  res.sendFile(path.join(process.cwd(),'public','index.html'));
});

// Diagnostics for unexpected exits
process.on('beforeExit', (code) => {
  console.log('[diagnostic] beforeExit code=', code);
});
process.on('exit', (code) => {
  console.log('[diagnostic] exit code=', code);
});
process.on('uncaughtException', (err) => {
  console.error('[diagnostic] uncaughtException', err);
});
process.on('unhandledRejection', (reason) => {
  console.error('[diagnostic] unhandledRejection', reason);
});
// Daily-style heartbeat is only useful when debugging the server itself.
let hbCount = 0;
if (SERVER_VERBOSE) {
  setInterval(()=>{
    hbCount++;
    if (hbCount % 6 === 0) { // every 60s if interval is 10s
      serverLog('[diagnostic] heartbeat 60s elapsed, process alive');
    }
  }, 10_000).unref();
}


// --- Metrics (in-memory) ---
const metrics = {
  startTime: Date.now(),
  requestsTotal: 0,
  streamRequests: 0,
  providerCalls: {},
  lastRequestAt: null,
  lastError: null,
  streamsReturned: 0,
  tmdbToImdbLookups: 0,
  streamCacheHits: 0
};

// --- Short-lived aggregate stream cache ---
// Navigating streams -> play -> back, or retrying playback, re-runs every
// provider scrape. Reuse the last successful aggregate response so repeated
// requests are effectively free. The key includes the config that affects the
// result so a settings change cannot serve a stale payload.
const STREAM_CACHE_TTL_MS = Math.max(
  1000,
  Number(process.env.TMDB_STREAM_CACHE_TTL_MS) || 2 * 60 * 1000
);
// After the fresh TTL, still serve the previous payload instantly while a
// refresh runs in the background (stale-while-revalidate).
const STREAM_CACHE_STALE_MS = Math.max(
  0,
  Number(process.env.TMDB_STREAM_CACHE_STALE_MS) || 3 * 60 * 1000
);
// Never make the caller wait for the slowest provider. Whatever has arrived
// by this deadline is returned (and cached); remaining providers keep filling
// the cache in the background. Set TMDB_AGGREGATE_DEADLINE_MS=0 to always
// wait for every provider.
const AGGREGATE_SOFT_DEADLINE_MS = process.env.TMDB_AGGREGATE_DEADLINE_MS === undefined
  ? 6000
  : Math.max(0, Number(process.env.TMDB_AGGREGATE_DEADLINE_MS) || 0);
// Playback asks with ?fast=1: waiting a few seconds for every provider is
// pointless when the first working link can start playing right away, so use
// a much shorter deadline there. The remaining providers still fill the cache
// for the next request / retry.
const FAST_DEADLINE_MS = process.env.TMDB_FAST_DEADLINE_MS === undefined
  ? 2000
  : Math.max(0, Number(process.env.TMDB_FAST_DEADLINE_MS) || 0);
const STREAM_CACHE_MAX = 128;
const streamCache = new Map();
// One scrape per cache key. Concurrent requests (e.g. opening the downloads
// screen and immediately pressing play) share the same provider calls instead
// of duplicating every upstream request.
const inflightScrapes = new Map();

function streamCacheSignature() {
  return JSON.stringify({
    providers: config.defaultProviders,
    proxy: !!config.enableProxy,
    minQualities: config.minQualities || null,
    excludeCodecs: config.excludeCodecs || null
  });
}

function streamCacheKey(type, tmdbId, season, episode) {
  return `${streamCacheSignature()}|${type}|${tmdbId}|${season}|${episode}`;
}

function getCachedStreams(key) {
  const entry = streamCache.get(key);
  if (!entry) return null;
  const age = Date.now() - entry.ts;
  if (age > STREAM_CACHE_TTL_MS + STREAM_CACHE_STALE_MS) {
    streamCache.delete(key);
    return null;
  }
  return { payload: entry.payload, stale: age > STREAM_CACHE_TTL_MS };
}

function setCachedStreams(key, payload) {
  if (streamCache.size >= STREAM_CACHE_MAX && !streamCache.has(key)) {
    // Evict only the oldest entry rather than clearing the whole cache.
    let oldestKey = null;
    let oldestTs = Infinity;
    for (const [k, v] of streamCache) {
      if (v.ts < oldestTs) { oldestTs = v.ts; oldestKey = k; }
    }
    if (oldestKey !== null) streamCache.delete(oldestKey);
  }
  streamCache.set(key, { ts: Date.now(), payload });
}

function aggregatePayload(state, partial) {
  const streams = [];
  for (const name of state.providers) {
    const result = state.results.get(name);
    if (Array.isArray(result)) streams.push(...result);
  }
  const filtered = applyFilters(streams, 'aggregate', config.minQualities, config.excludeCodecs);
  const payload = {
    success: true,
    tmdbId: state.tmdbId,
    imdbId: state.imdbId,
    count: filtered.length,
    providerTimings: { ...state.timings },
    streams: filtered
  };
  if (partial) {
    payload.partial = true;
    payload.pendingProviders = Array.from(state.pending);
  }
  return payload;
}

// Start (or reuse) a single scrape for a cache key. The returned state exposes
// a `promise` for the complete payload and per-provider results as they land,
// so callers can respond early without cancelling the remaining work.
function startScrape(type, tmdbId, season, episode, selectedProviders) {
  const key = streamCacheKey(type, tmdbId, season, episode);
  const existing = inflightScrapes.get(key);
  if (existing) return existing;

  const tmdbType = type === 'movie' ? 'movie' : 'tv';
  const state = {
    key,
    type,
    tmdbId,
    season,
    episode,
    providers: selectedProviders.slice(),
    results: new Map(),
    timings: {},
    pending: new Set(selectedProviders),
    imdbId: null,
    startedAt: Date.now(),
    finishedAt: null,
    promise: null
  };

  // IMDb is only response enrichment, so it must never delay the scrape.
  const imdbPromise = resolveImdbId(tmdbType, tmdbId)
    .then(id => {
      if (id) { metrics.tmdbToImdbLookups++; state.imdbId = id; }
      return id;
    })
    .catch(() => null);

  const providerPromises = selectedProviders.map(async name => {
    const prov = getProvider(name);
    if (!prov || !prov.enabled) {
      state.pending.delete(name);
      return;
    }
    metrics.providerCalls[name] = (metrics.providerCalls[name] || 0) + 1;
    const t0 = Date.now();
    try {
      console.log(`[api] invoking provider ${name} for tmdbId=${tmdbId}`);
      const r = await prov.fetch({ tmdbId, type, season, episode, imdbId: null, filters: {} });
      if (Array.isArray(r) && r.length) state.results.set(name, r);
      state.timings[name] = Date.now() - t0;
      console.log(`[api] provider ${name} returned ${Array.isArray(r) ? r.length : 0} streams`);
    } catch (e) {
      console.error(`[api] provider ${name} failed:`, e.message);
      state.timings[name] = null;
    } finally {
      state.pending.delete(name);
    }
  });

  state.promise = (async () => {
    await Promise.all(providerPromises);
    await imdbPromise;
    state.finishedAt = Date.now();
    const payload = aggregatePayload(state, false);
    // Only cache a successful response that actually has streams; an empty
    // result is often a transient provider hiccup and is worth retrying.
    if (payload.streams.length > 0) setCachedStreams(key, payload);
    return payload;
  })().catch(e => {
    metrics.lastError = e.message;
    return aggregatePayload(state, false);
  }).finally(() => {
    inflightScrapes.delete(key);
  });

  inflightScrapes.set(key, state);
  return state;
}

function waitForScrapeDeadline(state, timeoutMs) {
  if (state.finishedAt) return Promise.resolve('done');
  return new Promise(resolve => {
    let settled = false;
    const finish = (outcome) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(outcome);
    };
    const timer = setTimeout(() => finish('deadline'), Math.max(0, timeoutMs));
    state.promise.then(() => finish('done'), () => finish('done'));
  });
}

app.use((req,res,next)=>{ metrics.requestsTotal++; metrics.lastRequestAt = Date.now(); next(); });

// Config API
app.get('/api/config', (req,res) => {
  const fs = require('fs');
  let override = {};
  try { if (fs.existsSync(OVERRIDE_PATH)) override = JSON.parse(fs.readFileSync(OVERRIDE_PATH,'utf8')); } catch (e) {
    // ignore JSON parse or fs errors reading override; return base config
  }
  res.json({ success:true, merged: config, override, overridePath: OVERRIDE_PATH });
});
app.post('/api/config', (req,res) => {
  const patch = req.body || {};
  if (patch.port) {
    const p = Number(patch.port); if (!Number.isFinite(p) || p<=0 || p>65535) return res.status(400).json({ success:false, error:'INVALID_PORT'});
    patch.port = p;
  }
  if (patch.defaultProviders && !Array.isArray(patch.defaultProviders)) return res.status(400).json({ success:false, error:'DEFAULT_PROVIDERS_NOT_ARRAY'});
  const ok = saveConfigPatch(patch);
  res.json({ success: ok, merged: config });
});

// Restart endpoint (requires auth via session cookie on /config.html UI)
app.post('/api/restart', (req,res) => {
  const sess = getSession(req);
  if(!sess) return res.status(401).json({ success:false, error:'UNAUTHORIZED' });
  res.json({ success:true, message:'RESTARTING' });
  // Give the response a moment to flush
  setTimeout(()=>{
    try {
      const fs = require('fs');
      const restartMarker = require('path').join(process.cwd(), 'restart.trigger');
      fs.writeFileSync(restartMarker, String(Date.now()));
      console.warn('[control] wrote restart.trigger to notify nodemon');
    } catch (e) {
      console.warn('[control] failed to write restart marker:', e.message);
    }
    console.warn('[control] restarting process by exit(0)');
    // Let nodemon detect the file change and restart the app
    allowControlledExit = true;
    realProcessExit(0);
  }, 300);
});

// --- Basic informational endpoints ---
app.get('/api/health', (req, res) => {
  res.json({ ok: true, service: 'tmdb-embed-api', time: new Date().toISOString() });
});

// Metrics endpoint
app.get('/api/metrics', (req,res) => {
  res.json({
    uptimeSeconds: Math.round((Date.now()-metrics.startTime)/1000),
    requestsTotal: metrics.requestsTotal,
    streamRequests: metrics.streamRequests,
    providerCalls: metrics.providerCalls,
    streamsReturned: metrics.streamsReturned,
    streamCacheHits: metrics.streamCacheHits,
    tmdbToImdbLookups: metrics.tmdbToImdbLookups,
    lastRequestAt: metrics.lastRequestAt,
    memoryMB: Math.round(process.memoryUsage().rss/1024/1024),
    loadAvg: os.loadavg ? os.loadavg() : [],
    nodeVersion: process.version,
    configDefaults: {
      region: config.defaultRegion,
      providers: config.defaultProviders,
      minQualities: config.minQualities ? Object.keys(config.minQualities).length : 0,
      excludeCodecs: config.excludeCodecs ? Object.keys(config.excludeCodecs).filter(k=>config.excludeCodecs[k]).length : 0,
      febboxCookies: config.febboxCookies.length
    }
  });
});

// Consolidated status (metrics + providers + endpoints)
app.get('/api/status', (req,res) => {
  const endpoints = [
    'GET /api/health',
    'GET /api/metrics',
    'GET /api/status',
    'GET /api/providers',
    'GET /api/providers/:name',
    'GET /api/streams/:type/:tmdbId',
    'GET /api/streams/:provider/:type/:tmdbId',
    'POST /api/config',
    'GET /api/config'
  ];
  // Determine cookie requirement heuristically (currently Showbox / PStream)
  const cookieRequiredProviders = new Set(['showbox']);
  const providers = listProviders().map(p => {
    const cookieRequired = cookieRequiredProviders.has(p.name);
    const cookieOk = !cookieRequired || (config.febboxCookies && config.febboxCookies.length > 0);
    return { name: p.name, enabled: p.enabled, cookieRequired, cookieOk };
  });
  res.json({ success:true, providerCheckTmdbId: config.providerCheckTmdbId, metrics: {
    uptimeSeconds: Math.round((Date.now()-metrics.startTime)/1000),
    requestsTotal: metrics.requestsTotal,
    streamRequests: metrics.streamRequests,
    providerCalls: metrics.providerCalls,
    streamsReturned: metrics.streamsReturned,
    tmdbToImdbLookups: metrics.tmdbToImdbLookups,
    lastRequestAt: metrics.lastRequestAt,
    memoryMB: Math.round(process.memoryUsage().rss/1024/1024)
  }, endpoints, providers });
});

// Providers list
app.get('/api/providers', (req,res) => {
  res.json({ success: true, providers: listProviders() });
});

// Debug environment/config endpoint (do not expose publicly in production)
app.get('/api/debug/env', (req,res) => {
  const cookieStats = getCookieStats ? getCookieStats() : null;
  res.json({
    port: config.port,
    defaultProviders: config.defaultProviders,
    febboxCookieCount: config.febboxCookies.length,
    showboxCacheDir: process.env.SHOWBOX_CACHE_DIR || '(os tmp)',
    nodeVersion: process.version,
    cookieStats
  });
});

// Single provider info
app.get('/api/providers/:name', (req,res) => {
  const p = getProvider(req.params.name);
  if (!p) return res.status(404).json({ success:false, error:'PROVIDER_NOT_FOUND' });
  res.json({ success:true, provider:{ name: p.name, enabled: p.enabled } });
});

// Aggregate streams across all enabled providers
app.get('/api/streams/:type/:tmdbId', async (req,res) => {
  const { type, tmdbId } = req.params;
  if (!['movie','series'].includes(type)) return res.status(400).json({ success:false, error:'INVALID_TYPE' });
  const season = req.query.season ? Number(req.query.season) : null;
  const episode = req.query.episode ? Number(req.query.episode) : null;
  const cacheKey = streamCacheKey(type, tmdbId, season, episode);
  const selectedProviders = (config.defaultProviders.length ? config.defaultProviders : listProviders().map(p=>p.name));
  const requestDeadlineMs = String(req.query.fast || '') === '1'
    ? FAST_DEADLINE_MS
    : AGGREGATE_SOFT_DEADLINE_MS;

  const sendPayload = (payload, cached) => {
    let streams = payload.streams;
    if (config.enableProxy) {
      const serverUrl = `${req.protocol}://${req.get('host')}`;
      streams = processStreamsForProxy(streams, serverUrl);
      // Omit original headers when proxying to avoid leaking upstream requirements
      streams = streams.map(s => { if (s && typeof s === 'object') { const { headers, ...rest } = s; return rest; } return s; });
    }
    return res.json({ ...payload, streams, ...(cached ? { cached: true } : {}) });
  };

  const cached = getCachedStreams(cacheKey);
  if (cached) {
    metrics.streamCacheHits++;
    // Stale entries are returned immediately while a refresh fills the cache,
    // so going back to a title never waits for a fresh scrape.
    if (cached.stale && !inflightScrapes.has(cacheKey)) {
      startScrape(type, tmdbId, season, episode, selectedProviders);
    }
    return sendPayload(cached.payload, true);
  }

  try {
    metrics.streamRequests++;
    let state = inflightScrapes.get(cacheKey);
    const joinedInFlight = !!state;
    if (!state) {
      state = startScrape(type, tmdbId, season, episode, selectedProviders);
    }

    let payload;
    if (state.finishedAt) {
      payload = await state.promise;
    } else if (joinedInFlight && state.results.size > 0) {
      // Another request already started this scrape and has early results:
      // answer instantly instead of waiting the soft deadline a second time.
      payload = aggregatePayload(state, true);
    } else {
      const elapsed = Date.now() - state.startedAt;
      const remaining = requestDeadlineMs - elapsed;
      const outcome = requestDeadlineMs > 0
        ? await waitForScrapeDeadline(state, remaining)
        : 'done';
      if (outcome === 'done') {
        payload = await state.promise;
      } else if (state.results.size > 0) {
        // Return what is ready; the remaining providers keep filling the cache.
        payload = aggregatePayload(state, true);
      } else {
        // No provider has produced anything yet — waiting for a partial-empty
        // response would be useless, so fall back to the full (bounded) scrape.
        payload = await state.promise;
      }
    }

    metrics.streamsReturned += payload.streams.length;
    return sendPayload(payload, false);
  } catch (e) {
    metrics.lastError = e.message;
    res.status(500).json({ success:false, error:'INTERNAL_ERROR', message:e.message });
  }
});

// Provider-specific streams
app.get('/api/streams/:provider/:type/:tmdbId', async (req,res) => {
  const { provider, type, tmdbId } = req.params;
  if (!['movie','series'].includes(type)) return res.status(400).json({ success:false, error:'INVALID_TYPE' });
  const season = req.query.season ? Number(req.query.season) : null;
  const episode = req.query.episode ? Number(req.query.episode) : null;
  const prov = getProvider(provider);
  if (!prov) return res.status(404).json({ success:false, error:'PROVIDER_NOT_FOUND' });
  if (!prov.enabled) return res.status(503).json({ success:false, error:'PROVIDER_DISABLED' });
  try {
    metrics.streamRequests++;
    metrics.providerCalls[prov.name] = (metrics.providerCalls[prov.name]||0)+1;
    const tmdbType = type === 'movie' ? 'movie' : 'tv';
    // Overlap the response-only IMDb lookup with the provider fetch.
    const imdbPromise = resolveImdbId(tmdbType, tmdbId)
      .then(id => { if (id) metrics.tmdbToImdbLookups++; return id; })
      .catch(() => null);
    const t0 = Date.now();
    let streams = await prov.fetch({ tmdbId, type, season, episode, imdbId: null, filters:{} });
    const providerTimings = { [prov.name]: Date.now()-t0 };
    const imdbId = await imdbPromise;
    streams = applyFilters(streams, prov.name, config.minQualities, config.excludeCodecs);
    metrics.streamsReturned += streams.length;
    if (config.enableProxy) {
      const serverUrl = `${req.protocol}://${req.get('host')}`;
      streams = processStreamsForProxy(streams, serverUrl);
      streams = streams.map(s => { if (s && typeof s === 'object') { const { headers, ...rest } = s; return rest; } return s; });
    }
    res.json({ success:true, provider: prov.name, tmdbId, imdbId, count: streams.length, providerTimings, streams });
  } catch (e) {
    metrics.lastError = e.message;
    res.status(500).json({ success:false, error:'INTERNAL_ERROR', message:e.message });
  }
});

// Static assets are mounted last so API requests never pay for a filesystem
// lookup while Express searches for a matching file.
app.use(express.static(path.join(process.cwd(),'public')));

const PORT = config.port;
const HOST = process.env.BIND_HOST || '0.0.0.0';
const server = app.listen(PORT, HOST, () => {
  serverLog(`TMDB Embed REST API listening on http://${HOST}:${PORT}`);
  if (HOST !== 'localhost') {
    serverLog(`Local access (if running on your machine): http://localhost:${PORT}`);
  }
  serverLog('Endpoints:');
  serverLog('  GET  /api/health');
  serverLog('  GET  /api/metrics');
  serverLog('  GET  /api/providers');
  serverLog('  GET  /api/streams/:type/:id');
  serverLog('  POST /api/streams/:type/:id');
  if (!config.febboxCookies || config.febboxCookies.length === 0) {
    console.warn('[startup][warning] No FEBBOX_COOKIES configured. Showbox / PStream related streams may be unavailable. Set FEBBOX_COOKIES in your environment to enable these sources.');
  }
});

server.on('error', (err)=>{ console.error('[diagnostic] server error', err); });
