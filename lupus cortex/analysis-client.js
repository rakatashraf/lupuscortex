(function () {
  'use strict';

  const state = { status: 'idle', analysis: null, request: null, error: null };
  const configuredBase = String(window.LUPUS_CORTEX_API_BASE || '').trim();
  const apiBase = (
    configuredBase ||
    ((window.location.protocol === 'http:' || window.location.protocol === 'https:')
      ? window.location.origin
      : 'http://localhost:8000')
  ).replace(/\/$/, '');
  const REQUIRED_SCHEMA_VERSION = '1.0';

  function emit(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function ensureTokenDialogStyles() {
    if (document.getElementById('earthdata-token-dialog-styles')) return;
    const style = document.createElement('style');
    style.id = 'earthdata-token-dialog-styles';
    style.textContent = [
      '.earthdata-token-backdrop{position:fixed;inset:0;z-index:100000;background:rgba(3,7,18,.78);backdrop-filter:blur(10px);display:grid;place-items:center;padding:20px}',
      '.earthdata-token-card{width:min(560px,100%);background:#0b131f;border:1px solid rgba(97,231,199,.28);border-radius:20px;padding:24px;box-shadow:0 24px 80px rgba(0,0,0,.45);color:#eef5ff;font-family:inherit}',
      '.earthdata-token-card h3{margin:0 0 8px;font-size:20px}.earthdata-token-card p{margin:0 0 16px;color:#9db0c8;line-height:1.55;font-size:14px}',
      '.earthdata-token-card label{display:block;margin:12px 0 7px;font-size:12px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:#61e7c7}',
      '.earthdata-token-card input{width:100%;box-sizing:border-box;background:#07101b;color:#eef5ff;border:1px solid #29405a;border-radius:11px;padding:12px 13px;font:inherit;outline:none}',
      '.earthdata-token-card input:focus{border-color:#61e7c7;box-shadow:0 0 0 3px rgba(97,231,199,.10)}',
      '.earthdata-token-note{font-size:12px!important;color:#7f93aa!important;margin-top:10px!important}',
      '.earthdata-token-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}.earthdata-token-actions button{border-radius:10px;padding:10px 15px;font:inherit;font-weight:750;cursor:pointer}',
      '.earthdata-token-cancel{background:#111d2b;color:#c8d6e8;border:1px solid #2b3e56}.earthdata-token-submit{background:linear-gradient(135deg,#61e7c7,#54bce7);color:#061016;border:0}',
      '.earthdata-token-error{min-height:18px;color:#ff8d8d;font-size:12px;margin-top:7px}'
    ].join('');
    document.head.appendChild(style);
  }

  function promptForEarthdataToken() {
    ensureTokenDialogStyles();
    return new Promise(resolve => {
      const backdrop = document.createElement('div');
      backdrop.className = 'earthdata-token-backdrop';
      backdrop.innerHTML =
        '<form class="earthdata-token-card" autocomplete="off">' +
          '<h3>NASA Earthdata access required</h3>' +
          '<p>This analysis uses NASA Earth observation products. Enter your Earthdata Login user token for this request. The token is sent only to the Lupus Cortex backend and is not stored in browser storage or included in the model payload.</p>' +
          '<label for="earthdataTokenInput">Earthdata user token</label>' +
          '<input id="earthdataTokenInput" type="password" spellcheck="false" autocomplete="off" placeholder="Paste your Earthdata token" />' +
          '<div class="earthdata-token-error" aria-live="polite"></div>' +
          '<p class="earthdata-token-note">Do not include the word “Bearer”. You will be asked again for each new analysis request.</p>' +
          '<div class="earthdata-token-actions">' +
            '<button type="button" class="earthdata-token-cancel">Cancel</button>' +
            '<button type="submit" class="earthdata-token-submit">Continue analysis</button>' +
          '</div>' +
        '</form>';
      document.body.appendChild(backdrop);

      const form = backdrop.querySelector('form');
      const input = backdrop.querySelector('input');
      const error = backdrop.querySelector('.earthdata-token-error');

      const finish = value => {
        input.value = '';
        backdrop.remove();
        resolve(value);
      };

      backdrop.querySelector('.earthdata-token-cancel').addEventListener('click', () => finish(null));
      backdrop.addEventListener('click', event => {
        if (event.target === backdrop) finish(null);
      });
      form.addEventListener('submit', event => {
        event.preventDefault();
        const token = input.value.trim();
        if (!token) {
          error.textContent = 'Enter an Earthdata token to continue.';
          return;
        }
        if (/^Bearer\s+/i.test(token)) {
          error.textContent = 'Paste only the token itself, without “Bearer”.';
          return;
        }
        finish(token);
      });
      setTimeout(() => input.focus(), 50);
    });
  }

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

  async function request(location, options = {}) {
    const lat = Number(location?.lat);
    const lng = Number(location?.lng ?? location?.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      state.status = 'error';
      state.error = 'A valid latitude and longitude are required.';
      emit('cortex:analysis-error', { ...state });
      return null;
    }

    const earthdataToken = await promptForEarthdataToken();
    if (!earthdataToken) {
      state.status = 'idle';
      state.error = 'Analysis cancelled before NASA Earthdata authentication.';
      emit('cortex:analysis-error', { ...state });
      emit('cortex:analysis-state', { ...state });
      return null;
    }

    const scenario = options.scenario || null;
    state.status = 'collecting_evidence';
    state.error = null;
    state.request = {
      lat,
      lon: lng,
      name: location?.name || 'Selected location',
      requested_at: new Date().toISOString(),
      scenario
    };
    emit('cortex:analysis-request', { ...state.request });
    emit('cortex:analysis-state', { ...state });

    try {
      const endpoint = apiBase + (scenario ? '/api/scenario' : '/api/analyze');
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Earthdata-Token': earthdataToken
        },
        body: JSON.stringify(state.request)
      });
      if (!response.ok) throw new Error(await parseError(response));
      state.status = 'awaiting_model';
      emit('cortex:analysis-state', { ...state });
      return ingest(await response.json());
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
      throw new Error(
        'The model response must follow Lupus Cortex schema ' +
        REQUIRED_SCHEMA_VERSION + '.'
      );
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

    const response = await fetch(apiBase + '/api/collect', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Earthdata-Token': earthdataToken
      },
      body: JSON.stringify({
        lat,
        lon: lng,
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
    snapshot: () => ({ ...state }),
    apiBase
  };
  window.addEventListener('cortex:place-selected', event => request(event.detail));
}());
