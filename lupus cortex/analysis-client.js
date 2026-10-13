(function () {
  'use strict';

  const state = { status: 'idle', analysis: null, request: null, error: null, extraction: null };
  const configuredBase = String(window.LUPUS_CORTEX_API_BASE || '').trim();
  const apiBase = (
    configuredBase ||
    ((window.location.protocol === 'http:' || window.location.protocol === 'https:')
      ? window.location.origin
      : 'http://localhost:8000')
  ).replace(/\/$/, '');
  // Static hosting (GitHub Pages) has no backend: use client-side flow.
  const STATIC_HOST = !configuredBase && /github\.io$|github\.io\//.test(window.location.hostname);
  const REQUIRED_SCHEMA_VERSION = '1.0';

  function emit(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function ensureDialogStyles() {
    if (document.getElementById('lc-dialog-styles')) return;
    const style = document.createElement('style');
    style.id = 'lc-dialog-styles';
    style.textContent = [
      '.lc-backdrop{position:fixed;inset:0;z-index:100000;background:rgba(3,7,18,.78);backdrop-filter:blur(10px);display:grid;place-items:center;padding:20px;overflow-y:auto}',
      '.lc-card{width:min(600px,100%);background:#0b131f;border:1px solid rgba(97,231,199,.28);border-radius:20px;padding:24px;box-shadow:0 24px 80px rgba(0,0,0,.45);color:#eef5ff;font-family:inherit;margin:auto}',
      '.lc-card h3{margin:0 0 8px;font-size:20px}.lc-card p{margin:0 0 16px;color:#9db0c8;line-height:1.55;font-size:14px}',
      '.lc-card label{display:block;margin:12px 0 7px;font-size:12px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:#61e7c7}',
      '.lc-card input{width:100%;box-sizing:border-box;background:#07101b;color:#eef5ff;border:1px solid #29405a;border-radius:11px;padding:12px 13px;font:inherit;outline:none}',
      '.lc-card input:focus{border-color:#61e7c7;box-shadow:0 0 0 3px rgba(97,231,199,.10)}',
      '.lc-note{font-size:12px!important;color:#7f93aa!important;margin-top:10px!important}',
      '.lc-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}.lc-actions button{border-radius:10px;padding:10px 15px;font:inherit;font-weight:750;cursor:pointer}',
      '.lc-cancel{background:#111d2b;color:#c8d6e8;border:1px solid #2b3e56}.lc-submit{background:linear-gradient(135deg,#61e7c7,#54bce7);color:#061016;border:0}.lc-submit:disabled{opacity:.55;cursor:wait}',
      '.lc-error{min-height:18px;color:#ff8d8d;font-size:12px;margin-top:7px}',
      '.lc-ok{color:#61e7c7;font-size:13px;margin-top:8px}',
      '.lc-grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}',
      '.lc-grid4{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px}',
      '.lc-progress{background:#07101b;border-radius:8px;padding:12px;margin-top:12px;font-size:13px;color:#9db0c8}',
      '.lc-progress .bar{height:6px;background:#1e293b;border-radius:3px;margin-top:8px;overflow:hidden}',
      '.lc-progress .bar>div{height:100%;background:linear-gradient(90deg,#61e7c7,#54bce7);width:0%;transition:width .3s}'
    ].join('');
    document.head.appendChild(style);
  }

  /* ---------- Step 1: Earthdata token + real validation ---------- */
  async function validateEarthdataToken(token) {
    // Real validation against NASA CMR: 200 = valid, 401 = invalid.
    const res = await fetch(
      'https://cmr.earthdata.nasa.gov/search/collections.json?page_size=1',
      { headers: { 'Authorization': 'Bearer ' + token } }
    );
    if (res.status === 401) throw new Error('Token rejected by NASA Earthdata (401). Check the token and try again.');
    if (!res.ok) throw new Error('Earthdata validation request failed (HTTP ' + res.status + ').');
    return true;
  }

  function promptForEarthdataToken() {
    ensureDialogStyles();
    return new Promise(resolve => {
      const backdrop = document.createElement('div');
      backdrop.className = 'lc-backdrop';
      backdrop.innerHTML =
        '<form class="lc-card" autocomplete="off">' +
          '<h3>NASA Earthdata access required</h3>' +
          '<p>This analysis uses NASA Earth observation products. Enter your Earthdata Login user token. It is <b>validated live</b> against NASA Earthdata and never stored.</p>' +
          '<label for="lcTokenInput">Earthdata user token</label>' +
          '<input id="lcTokenInput" type="password" spellcheck="false" autocomplete="off" placeholder="Paste your Earthdata token" />' +
          '<div class="lc-error" aria-live="polite"></div>' +
          '<div class="lc-ok" aria-live="polite"></div>' +
          '<p class="lc-note">Do not include the word \u201cBearer\u201d.</p>' +
          '<div class="lc-actions">' +
            '<button type="button" class="lc-cancel">Cancel</button>' +
            '<button type="submit" class="lc-submit">Validate &amp; continue</button>' +
          '</div>' +
        '</form>';
      document.body.appendChild(backdrop);

      const form = backdrop.querySelector('form');
      const input = backdrop.querySelector('input');
      const error = backdrop.querySelector('.lc-error');
      const ok = backdrop.querySelector('.lc-ok');
      const submit = backdrop.querySelector('.lc-submit');

      const finish = value => { input.value = ''; backdrop.remove(); resolve(value); };
      backdrop.querySelector('.lc-cancel').addEventListener('click', () => finish(null));
      backdrop.addEventListener('click', e => { if (e.target === backdrop) finish(null); });
      form.addEventListener('submit', async e => {
        e.preventDefault();
        const token = input.value.trim();
        error.textContent = ''; ok.textContent = '';
        if (!token) { error.textContent = 'Enter an Earthdata token to continue.'; return; }
        if (/^Bearer\s+/i.test(token)) { error.textContent = 'Paste only the token itself, without \u201cBearer\u201d.'; return; }
        submit.disabled = true; submit.textContent = 'Validating\u2026';
        ok.textContent = 'Contacting NASA Earthdata to validate the token\u2026';
        try {
          await validateEarthdataToken(token);
          ok.textContent = '\u2705 Token validated by NASA Earthdata.';
          setTimeout(() => finish(token), 600);
        } catch (err) {
          error.textContent = err.message || 'Token validation failed.';
          submit.disabled = false; submit.textContent = 'Validate & continue';
        }
      });
      setTimeout(() => input.focus(), 50);
    });
  }

  /* ---------- Step 2: area selection + date range ---------- */
  function promptForAreaAndDates(location) {
    ensureDialogStyles();
    const lat = Number(location?.lat) || 23.81;
    const lng = Number(location?.lng ?? location?.lon) || 90.41;
    // Default ~0.5° box around the point
    const d = 0.25;
    // Date range extends to the present day
    const today = new Date().toISOString().slice(0, 10);
    return new Promise(resolve => {
      const backdrop = document.createElement('div');
      backdrop.className = 'lc-backdrop';
      backdrop.innerHTML =
        '<form class="lc-card" autocomplete="off">' +
          '<h3>Select area &amp; date range</h3>' +
          '<p>Choose the bounding box and time period for data extraction. Data is fetched live from open providers.</p>' +
          '<label>Bounding box (degrees)</label>' +
          '<div class="lc-grid4">' +
            '<div><input id="lcBboxS" type="number" step="0.01" value="' + (lat - d).toFixed(2) + '" title="South" /></div>' +
            '<div><input id="lcBboxW" type="number" step="0.01" value="' + (lng - d).toFixed(2) + '" title="West" /></div>' +
            '<div><input id="lcBboxN" type="number" step="0.01" value="' + (lat + d).toFixed(2) + '" title="North" /></div>' +
            '<div><input id="lcBboxE" type="number" step="0.01" value="' + (lng + d).toFixed(2) + '" title="East" /></div>' +
          '</div>' +
          '<p class="lc-note">Order: South, West, North, East.</p>' +
          '<label>Date range</label>' +
          '<div class="lc-grid2">' +
            '<div><input id="lcDateFrom" type="date" value="2025-01-01" min="2020-01-01" max="' + today + '" /></div>' +
            '<div><input id="lcDateTo" type="date" value="' + today + '" min="2020-01-01" max="' + today + '" /></div>' +
          '</div>' +
          '<div class="lc-error" aria-live="polite"></div>' +
          '<div class="lc-actions">' +
            '<button type="button" class="lc-cancel">Back</button>' +
            '<button type="submit" class="lc-submit">Extract data</button>' +
          '</div>' +
        '</form>';
      document.body.appendChild(backdrop);
      const form = backdrop.querySelector('form');
      const error = backdrop.querySelector('.lc-error');
      const finish = v => { backdrop.remove(); resolve(v); };
      backdrop.querySelector('.lc-cancel').addEventListener('click', () => finish(null));
      backdrop.addEventListener('click', e => { if (e.target === backdrop) finish(null); });
      form.addEventListener('submit', e => {
        e.preventDefault();
        const s = parseFloat(backdrop.querySelector('#lcBboxS').value);
        const w = parseFloat(backdrop.querySelector('#lcBboxW').value);
        const n = parseFloat(backdrop.querySelector('#lcBboxN').value);
        const ee = parseFloat(backdrop.querySelector('#lcBboxE').value);
        const from = backdrop.querySelector('#lcDateFrom').value;
        const to = backdrop.querySelector('#lcDateTo').value;
        if (![s, w, n, ee].every(Number.isFinite) || s >= n || w >= ee) {
          error.textContent = 'Enter a valid bounding box (south < north, west < east).'; return;
        }
        if (!from || !to || from > to) { error.textContent = 'Enter a valid date range.'; return; }
        finish({ bbox: [s, w, n, ee], dateFrom: from, dateTo: to });
      });
    });
  }

  /* ---------- Step 3: client-side data extraction ---------- */
  function showProgress(title) {
    ensureDialogStyles();
    const backdrop = document.createElement('div');
    backdrop.className = 'lc-backdrop';
    backdrop.innerHTML =
      '<div class="lc-card"><h3>' + title + '</h3>' +
      '<div class="lc-progress"><div class="lc-msg">Starting\u2026</div><div class="bar"><div></div></div></div>' +
      '<div class="lc-actions"><button type="button" class="lc-cancel">Close</button></div></div>';
    document.body.appendChild(backdrop);
    const msg = backdrop.querySelector('.lc-msg');
    const bar = backdrop.querySelector('.bar>div');
    backdrop.querySelector('.lc-cancel').addEventListener('click', () => backdrop.remove());
    let done = false;
    return {
      update(pct, text) { if (!done) { bar.style.width = pct + '%'; if (text) msg.textContent = text; } },
      close(okText) { done = true; if (okText) msg.textContent = okText; bar.style.width = '100%'; },
      element: backdrop
    };
  }

  async function fetchOpenMeteo(bbox, dateFrom, dateTo, onStep) {
    // Center point of bbox; Open-Meteo archive (no key, CORS enabled)
    const lat = ((bbox[0] + bbox[2]) / 2).toFixed(3);
    const lon = ((bbox[1] + bbox[3]) / 2).toFixed(3);
    const url = 'https://archive-api.open-meteo.com/v1/archive?latitude=' + lat +
      '&longitude=' + lon + '&start_date=' + dateFrom + '&end_date=' + dateTo +
      '&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean,relative_humidity_2m_mean,precipitation_sum,soil_moisture_0_to_7cm_mean' +
      '&timezone=auto';
    onStep('Fetching meteorology & soil moisture (Open-Meteo)\u2026');
    const res = await fetch(url);
    if (!res.ok) throw new Error('Open-Meteo request failed (HTTP ' + res.status + ').');
    const j = await res.json();
    const d = j.daily || {};
    const n = (d.time || []).length;
    return {
      source: 'Open-Meteo (ERA5)',
      days: n,
      center: [parseFloat(lat), parseFloat(lon)],
      variables: Object.keys(d).filter(k => k !== 'time'),
      sample: n ? {
        date: d.time[0],
        temp_mean_c: d.temperature_2m_mean?.[0] ?? null,
        humidity_pct: d.relative_humidity_2m_mean?.[0] ?? null,
        precip_mm: d.precipitation_sum?.[0] ?? null
      } : null,
      stats: n ? {
        temp_mean_avg_c: avg(d.temperature_2m_mean),
        precip_total_mm: sum(d.precipitation_sum),
        humidity_avg_pct: avg(d.relative_humidity_2m_mean)
      } : null
    };
  }

  async function fetchCmrCollections(token, bbox, onStep) {
    onStep('Discovering NASA collections for the area (Earthdata CMR)\u2026');
    const [s, w, n, e] = bbox;
    const url = 'https://cmr.earthdata.nasa.gov/search/collections.json?page_size=8&has_granules=true' +
      '&bounding_box=' + [w, s, e, n].join(',');
    const res = await fetch(url, { headers: { 'Authorization': 'Bearer ' + token } });
    if (!res.ok) throw new Error('CMR collection search failed (HTTP ' + res.status + ').');
    const j = await res.json();
    const entries = ((j.feed || {}).entry) || [];
    return {
      source: 'NASA Earthdata CMR',
      count: entries.length,
      collections: entries.map(c => ({
        id: c.id, title: c.title,
        updated: c.updated
      }))
    };
  }

  function avg(a) { const v = (a || []).filter(x => x != null); return v.length ? +(v.reduce((x, y) => x + y, 0) / v.length).toFixed(2) : null; }
  function sum(a) { const v = (a || []).filter(x => x != null); return v.length ? +v.reduce((x, y) => x + y, 0).toFixed(1) : null; }

  async function extractDataClientSide(token, location, area) {
    const prog = showProgress('Extracting data');
    const out = {
      extracted_at: new Date().toISOString(),
      bbox: area.bbox, date_from: area.dateFrom, date_to: area.dateTo,
      sources: {}
    };
    try {
      prog.update(10, 'Validating session\u2026');
      const meteo = await fetchOpenMeteo(area.bbox, area.dateFrom, area.dateTo,
        t => prog.update(35, t));
      out.sources.meteorology = meteo;
      prog.update(60);
      const cmr = await fetchCmrCollections(token, area.bbox, t => prog.update(80, t));
      out.sources.nasa_collections = cmr;
      prog.update(95, 'Done.');
      prog.close('\u2705 Extraction complete: ' + meteo.days + ' days of meteorology, ' +
        cmr.count + ' NASA collections found for the area.');
      state.extraction = out;
      emit('cortex:extraction-ready', out);
      return out;
    } catch (err) {
      prog.update(100, '\u274C ' + (err.message || String(err)));
      state.error = err.message || String(err);
      emit('cortex:analysis-error', { ...state });
      return null;
    }
  }

  /* ---------- main request flow ---------- */
  function valid(report) {
    if (!report || typeof report !== 'object' || report.schema_version !== REQUIRED_SCHEMA_VERSION) return false;
    const location = report.location || {};
    const lat = Number(location.lat);
    const lon = Number(location.lon ?? location.lng);
    if (!Number.isFinite(lat) || lat < -90 || lat > 90 || !Number.isFinite(lon) || lon < -180 || lon > 180) return false;
    if (!report.analysis_id || !report.model_version) return false;
    if (!report.time || typeof report.time !== 'object') return false;
    if (!report.data_quality || typeof report.data_quality !== 'object') return false;
    if (!report.components || typeof report.components !== 'object' || Array.isArray(report.components)) return false;
    if (!report.indices || typeof report.indices !== 'object' || Array.isArray(report.indices)) return false;
    if (!report.hwi || typeof report.hwi !== 'object') return false;
    if (!report.analysis || typeof report.analysis !== 'object') return false;
    return ['history', 'forecasts', 'warnings', 'recommendations', 'provenance']
      .every(key => Object.hasOwn(report, key));
  }

  async function parseError(response) {
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === 'string') return detail;
      if (detail && typeof detail === 'object') {
        return detail.message || detail.error || JSON.stringify(detail);
      }
      return body?.message || ('Request failed (' + response.status + ').');
    } catch (_) {
      return 'Request failed (' + response.status + ').';
    }
  }

  async function requestViaBackend(location, earthdataToken, options) {
    const scenario = options.scenario || null;
    const endpoint = apiBase + (scenario ? '/api/scenario' : '/api/analyze');
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Earthdata-Token': earthdataToken },
      body: JSON.stringify(state.request)
    });
    if (!response.ok) throw new Error(await parseError(response));
    state.status = 'awaiting_model';
    emit('cortex:analysis-state', { ...state });
    return ingest(await response.json());
  }

  async function request(location, options = {}) {
    const lat = Number(location?.lat);
    const lng = Number(location?.lng ?? location?.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      state.status = 'error';
      state.error = 'A valid latitude and longitude are required.';
      emit('cortex:analysis-error', { ...state });
      return null;
    }

    // Step 1: token + live validation
    const earthdataToken = await promptForEarthdataToken();
    if (!earthdataToken) {
      state.status = 'idle';
      state.error = 'Analysis cancelled before NASA Earthdata authentication.';
      emit('cortex:analysis-error', { ...state });
      emit('cortex:analysis-state', { ...state });
      return null;
    }

    state.status = 'collecting_evidence';
    state.error = null;
    state.request = {
      lat, lon: lng,
      name: location?.name || 'Selected location',
      requested_at: new Date().toISOString(),
      scenario: options.scenario || null
    };
    emit('cortex:analysis-request', { ...state.request });
    emit('cortex:analysis-state', { ...state });

    // Static hosting without a configured backend: client-side extraction flow.
    if (STATIC_HOST) {
      const area = await promptForAreaAndDates(location);
      if (!area) {
        state.status = 'idle';
        emit('cortex:analysis-state', { ...state });
        return null;
      }
      state.request.bbox = area.bbox;
      state.request.date_from = area.dateFrom;
      state.request.date_to = area.dateTo;
      const data = await extractDataClientSide(earthdataToken, location, area);
      if (data) {
        state.status = 'ready';
        state.analysis = {
          schema_version: '1.0',
          analysis_id: 'lc-extract-' + Date.now(),
          model_version: 'extraction-only',
          location: { lat, lon: lng, name: location?.name || 'Selected location' },
          time: { date_from: area.dateFrom, date_to: area.dateTo },
          extraction: data
        };
        emit('cortex:analysis-ready', state.analysis);
      }
      emit('cortex:analysis-state', { ...state });
      return state.analysis;
    }

    // Backend flow (local dev / deployed API)
    try {
      return await requestViaBackend(location, earthdataToken, options);
    } catch (error) {
      state.status = 'error';
      state.error = error?.message || String(error);
      emit('cortex:analysis-error', { ...state });
      emit('cortex:analysis-state', { ...state });
      return null;
    }
  }

  function ingest(report) {
    if (!valid(report)) {
      throw new Error('The model response must follow Lupus Cortex schema ' + REQUIRED_SCHEMA_VERSION + '.');
    }
    state.analysis = report;
    state.status = 'ready';
    state.error = null;
    emit('cortex:analysis-ready', report);
    emit('cortex:analysis-state', { ...state });
    return report;
  }

  async function collectOnly(location) {
    const lat = Number(location?.lat);
    const lng = Number(location?.lng ?? location?.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      throw new Error('A valid latitude and longitude are required.');
    }
    const earthdataToken = await promptForEarthdataToken();
    if (!earthdataToken) return null;
    if (STATIC_HOST) {
      const area = await promptForAreaAndDates(location);
      if (!area) return null;
      return extractDataClientSide(earthdataToken, location, area);
    }
    const response = await fetch(apiBase + '/api/collect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Earthdata-Token': earthdataToken },
      body: JSON.stringify({
        lat, lon: lng,
        name: location?.name || 'Selected location',
        requested_at: new Date().toISOString()
      })
    });
    if (!response.ok) throw new Error(await parseError(response));
    return response.json();
  }

  window.CortexAnalysis = {
    request,
    collectOnly,
    ingest,
    validateEarthdataToken,
    snapshot: () => ({ ...state }),
    apiBase,
    staticHost: STATIC_HOST
  };
  window.addEventListener('cortex:place-selected', event => request(event.detail));
}());
