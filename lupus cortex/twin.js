/* Lupus Cortex 3D Digital Twin — real-data visualization logic.
   Data: OpenAQ stations, ERA5 grid, WorldPop, DEM points, RF model predictions.
   No invented geometry: every 3D object is placed from a measured data point. */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const DATA_DIR = 'data/';
const CENTER = { lat: 23.8, lon: 90.275 };
const KM_PER_DEG_LAT = 110.9, KM_PER_DEG_LON = 101.4;
const TERRAIN_EXAG = 25; // vertical exaggeration, labelled in UI

// viridis-ish stops for the data colormap
const VIRIDIS = ['#440154','#482878','#3e4989','#31688e','#26828e','#1f9e89',
                 '#35b779','#6ece58','#b8de29','#fde725'].map(hex => {
  const n = parseInt(hex.slice(1), 16);
  return [(n>>16&255)/255, (n>>8&255)/255, (n&255)/255];
});
function cmap(t) {
  t = Math.min(1, Math.max(0, t));
  const x = t * (VIRIDIS.length - 1), i = Math.floor(x), f = x - i;
  const a = VIRIDIS[i], b = VIRIDIS[Math.min(i+1, VIRIDIS.length-1)];
  return new THREE.Color(a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f);
}

// Component/layer definitions (keys match twin JSON bundles)
const LAYERS = {
  pm25:     { kind:'station', label:'PM2.5', unit:'µg/m³', who:15, whoOrg:'WHO 2021 (24-h)' },
  pm10:     { kind:'station', label:'PM10', unit:'µg/m³', who:45, whoOrg:'WHO 2021 (24-h)' },
  o3:       { kind:'station', label:'O₃', unit:'µg/m³', who:100, whoOrg:'WHO 2021 (8-h)' },
  so2:      { kind:'station', label:'SO₂', unit:'µg/m³', who:40, whoOrg:'WHO 2021 (24-h)' },
  temp_mean:{ kind:'grid', label:'Air Temp (mean)', unit:'°C', who:28, whoOrg:'WMO reference', pred:true },
  humidity: { kind:'grid', label:'Rel. Humidity', unit:'%', who:60, whoOrg:'ASHRAE comfort', pred:true },
  precip:   { kind:'grid', label:'Precipitation', unit:'mm', who:null, pred:true },
  pressure: { kind:'grid', label:'Urban Pressure', unit:'index 0–100', who:null, pred:true },
  soil_moisture:{ kind:'grid', label:'Soil Moisture', unit:'m³/m³', who:null },
  flood:    { kind:'grid', label:'Flood Risk', unit:'index', who:null },
  drought:  { kind:'grid', label:'Drought Index', unit:'std. anomaly', who:null },
};

const state = {
  day: 0, layer: 'pm25', showPred: false,
  showStations: true, showGrid: true, showPop: false, showTerrain: true,
  playing: false, playTimer: null,
  selected: null, // {type:'station'|'grid', id}
};

let D = {};            // loaded JSON bundles
let scene, camera, renderer, controls, raycaster, pointer;
let terrainMesh, stationGroup, gridGroup, popGroup;
let stationMeshes = [], gridMeshes = [];
let idwFn = null;

const $ = id => document.getElementById(id);
const fmtDate = d => d;
function seasonOf(dateStr) {
  const m = +dateStr.slice(5,7);
  if (m>=11||m<=2) return 'Winter'; if (m>=3&&m<=5) return 'Pre-monsoon';
  if (m>=6&&m<=9) return 'Monsoon'; return 'Post-monsoon';
}

/* ---------- geo helpers ---------- */
function toXZ(lat, lon) {
  return { x: (lon - CENTER.lon) * KM_PER_DEG_LON, z: -(lat - CENTER.lat) * KM_PER_DEG_LAT };
}
function makeIDW(points) {
  // points: [{lat, lon, value}]
  return (lat, lon) => {
    let num = 0, den = 0;
    for (const p of points) {
      const dx = (lon - p.lon) * KM_PER_DEG_LON, dz = (lat - p.lat) * KM_PER_DEG_LAT;
      const d2 = dx*dx + dz*dz + 1e-6;
      if (d2 < 1e-9) return p.value;
      const w = 1 / d2;
      num += w * p.value; den += w;
    }
    return num / den;
  };
}
function terrainH(lat, lon) { return idwFn ? idwFn(lat, lon) * TERRAIN_EXAG / 1000 : 0; } // km

/* ---------- data loading ---------- */
async function loadData() {
  const files = ['twin_meta','twin_stations','twin_grid','twin_elevation','twin_population'];
  for (const f of files) {
    const res = await fetch(DATA_DIR + f + '.json');
    if (!res.ok) throw new Error('Cannot load ' + f + '.json (HTTP ' + res.status + ')');
    D[f.replace('twin_','')] = await res.json();
  }
  // colormap ranges per layer (p1–p99 over the year to resist outliers)
  D.ranges = {};
  for (const key of Object.keys(LAYERS)) {
    const L = LAYERS[key]; const vals = [];
    if (L.kind === 'station') for (const s of D.stations) {
      if (s[key]) for (const v of s[key]) if (v != null) vals.push(v);
    } else for (const g of D.grid) {
      const arr = g.daily[key]; if (arr) for (const v of arr) if (v != null) vals.push(v);
    }
    vals.sort((a,b)=>a-b);
    if (!vals.length) { D.ranges[key] = [0,1]; continue; }
    D.ranges[key] = [vals[Math.floor(vals.length*0.01)], vals[Math.floor(vals.length*0.99)] || vals[vals.length-1]];
    if (D.ranges[key][1] <= D.ranges[key][0]) D.ranges[key][1] = D.ranges[key][0] + 1e-6;
  }
}

/* ---------- scene building ---------- */
function buildTerrain() {
  const W = (91.31-89.24)*KM_PER_DEG_LON, Hh = (24.80-22.80)*KM_PER_DEG_LAT;
  const geo = new THREE.PlaneGeometry(W, Hh, 48, 48);
  geo.rotateX(-Math.PI/2);
  const pos = geo.attributes.position;
  for (let i=0;i<pos.count;i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    const lon = CENTER.lon + x/KM_PER_DEG_LON, lat = CENTER.lat - z/KM_PER_DEG_LAT;
    pos.setY(i, terrainH(lat, lon));
  }
  geo.computeVertexNormals();
  const mat = new THREE.MeshLambertMaterial({ color: 0x8a9a6b, transparent:true, opacity:0.85 });
  terrainMesh = new THREE.Mesh(geo, mat);
  terrainMesh.name = 'terrain';
  scene.add(terrainMesh);
  // bbox outline
  const box = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(W, 0.5, Hh)),
    new THREE.LineBasicMaterial({ color: 0x334155 }));
  box.position.y = -0.3; scene.add(box);
}

function buildStations() {
  stationGroup = new THREE.Group(); stationGroup.name = 'stations';
  for (const s of D.stations) {
    const { x, z } = toXZ(s.lat, s.lon);
    const base = terrainH(s.lat, s.lon);
    const grp = new THREE.Group();
    const dot = new THREE.Mesh(new THREE.SphereGeometry(1.6, 20, 14),
      new THREE.MeshLambertMaterial({ color: 0xffffff }));
    dot.position.set(x, base + 1.6, z);
    const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.7, 0.7, 1, 12),
      new THREE.MeshLambertMaterial({ color: 0x999999 }));
    bar.position.set(x, base + 0.5, z);
    grp.add(dot, bar);
    dot.userData = { type:'station', id:s.id, bar };
    bar.userData = { type:'station', id:s.id, bar };
    stationGroup.add(grp);
    stationMeshes.push({ id:s.id, dot, bar, lat:s.lat, lon:s.lon, base });
  }
  scene.add(stationGroup);
}

function buildGrid() {
  gridGroup = new THREE.Group(); gridGroup.name = 'grid';
  const geo = new THREE.CylinderGeometry(2.2, 2.2, 1, 16);
  for (const g of D.grid) {
    const { x, z } = toXZ(g.lat, g.lon);
    const base = terrainH(g.lat, g.lon);
    const m = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: 0x888888 }));
    m.position.set(x, base + 0.5, z);
    m.userData = { type:'grid', id:g.id };
    gridGroup.add(m);
    gridMeshes.push({ id:g.id, mesh:m, lat:g.lat, lon:g.lon, base });
  }
  scene.add(gridGroup);
}

function buildPopulation() {
  popGroup = new THREE.Group(); popGroup.name = 'population'; popGroup.visible = false;
  const geo = new THREE.BoxGeometry(3.2, 1, 3.2);
  let maxV = 0;
  for (const c of D.population) maxV = Math.max(maxV, c.value || 0);
  for (const c of D.population) {
    if (!c.value) continue;
    const { x, z } = toXZ(c.lat, c.lon);
    const base = terrainH(c.lat, c.lon);
    const h = 0.4 + 6 * Math.log10(1 + c.value) / Math.log10(1 + maxV);
    const m = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({
      color: cmap(Math.log10(1+c.value)/Math.log10(1+maxV)),
      transparent: true, opacity: 0.75 }));
    m.scale.y = h; m.position.set(x, base + h/2, z);
    m.userData = { type:'pop', value:c.value, lat:c.lat, lon:c.lon };
    popGroup.add(m);
  }
  scene.add(popGroup);
}

/* ---------- per-day update ---------- */
function layerValue(kind, id, key, day) {
  const usePred = state.showPred && LAYERS[key].pred;
  const k = usePred ? key + '_pred' : key;
  if (kind === 'station') {
    const s = D.stations.find(s => s.id === id);
    return s && s[key] ? s[key][day] : null;
  }
  const g = D.grid.find(g => g.id === id);
  return g && g.daily[k] ? g.daily[k][day] : null;
}

function updateDay() {
  const L = LAYERS[state.layer], [lo, hi] = D.ranges[state.layer];
  const date = D.meta.dates[state.day];
  $('dateLabel').textContent = date + ' · ' + seasonOf(date);
  $('daySlider').value = state.day;

  if (L.kind === 'station') {
    for (const sm of stationMeshes) {
      const v = layerValue('station', sm.id, state.layer, state.day);
      const vis = state.showStations;
      sm.dot.visible = vis; sm.bar.visible = vis;
      if (!vis) continue;
      if (v == null) { sm.bar.material.color.set(0x9ca3af); sm.bar.scale.y = 0.6; sm.bar.position.y = sm.base + 0.3; sm.dot.material.color.set(0x9ca3af); }
      else {
        const t = (v - lo) / (hi - lo);
        sm.bar.material.color.copy(cmap(t));
        const h = 1 + 14 * t;
        sm.bar.scale.y = h; sm.bar.position.y = sm.base + h/2;
        sm.dot.position.y = sm.base + h + 1.2;
        sm.dot.material.color.copy(cmap(t));
      }
    }
    for (const gm of gridMeshes) { gm.mesh.visible = false; }
  } else {
    for (const sm of stationMeshes) { sm.dot.visible = false; sm.bar.visible = false; }
    for (const gm of gridMeshes) {
      const v = layerValue('grid', gm.id, state.layer, state.day);
      gm.mesh.visible = state.showGrid;
      if (!state.showGrid) continue;
      if (v == null) { gm.mesh.material.color.set(0x9ca3af); gm.mesh.scale.y = 0.5; gm.mesh.position.y = gm.base + 0.25; }
      else {
        const t = (v - lo) / (hi - lo);
        gm.mesh.material.color.copy(cmap(t));
        const h = 0.6 + 9 * t;
        gm.mesh.scale.y = h; gm.mesh.position.y = gm.base + h/2;
      }
    }
  }
  // legend
  const fmt = v => (+v).toFixed(Math.abs(hi-lo) < 10 ? 2 : 1);
  $('legendMin').textContent = fmt(lo) + ' ' + L.unit;
  $('legendMax').textContent = fmt(hi) + ' ' + L.unit;
  $('legendLabel').textContent = L.label + (state.showPred && L.pred ? ' — RF predicted' : ' — observed');
  if (state.selected) showInfo(state.selected.type, state.selected.id);
}

/* ---------- info panel ---------- */
function whoStatus(key, v) {
  const L = LAYERS[key];
  if (v == null || L.who == null) return '';
  const ratio = v / L.who;
  const cls = ratio <= 1 ? 'ok' : ratio <= 2 ? 'warn' : 'bad';
  const txt = ratio <= 1 ? 'Within guideline' : ratio <= 2 ? 'Above guideline' : 'Well above guideline';
  return `<span class="badge ${cls}">${txt} (${ratio.toFixed(1)}× ${L.whoOrg})</span>`;
}

function showInfo(type, id) {
  state.selected = { type, id };
  const date = D.meta.dates[state.day], L = LAYERS[state.layer];
  let html = '';
  if (type === 'station') {
    const s = D.stations.find(s => s.id === id);
    const v = s[state.layer] ? s[state.layer][state.day] : null;
    const unitNote = s.unit_converted && s.unit_converted[state.layer]
      ? ' <span class="note">(converted to µg/m³)</span>' : '';
    html = `<h3>Station ${s.id.replace('openaq_station_','')}</h3>
      <div class="row"><span>Lat / Lon</span><b>${s.lat.toFixed(4)}, ${s.lon.toFixed(4)}</b></div>
      <div class="row"><span>Date</span><b>${date}</b></div>
      <div class="row"><span>${L.label}</span><b>${v == null ? 'no data' : v.toFixed(2) + ' ' + (s.units[state.layer]||L.unit)}${unitNote}</b></div>
      <div class="row">${whoStatus(state.layer, v)}</div>
      <div class="row"><span>Source</span><b>OpenAQ direct measurement</b></div>
      <div class="row"><span>Coverage 2025</span><b>${s[state.layer+'_n'] || 0} days</b></div>`;
  } else if (type === 'grid') {
    const g = D.grid.find(g => g.id === id);
    const v = g.daily[state.layer] ? g.daily[state.layer][state.day] : null;
    let predRow = '';
    if (L.pred) {
      const p = g.daily[state.layer + '_pred'][state.day];
      const err = (v != null && p != null) ? Math.abs(v - p) : null;
      predRow = `<div class="row"><span>RF predicted</span><b>${p.toFixed(2)} ${L.unit}</b></div>
        <div class="row"><span>|actual − predicted|</span><b>${err != null ? err.toFixed(2) : 'n/a'}</b></div>`;
    }
    html = `<h3>Grid ${g.id}</h3>
      <div class="row"><span>Lat / Lon</span><b>${g.lat.toFixed(2)}, ${g.lon.toFixed(2)}</b></div>
      <div class="row"><span>Date</span><b>${date}</b></div>
      <div class="row"><span>${L.label} (observed)</span><b>${v == null ? 'no data' : v.toFixed(2) + ' ' + L.unit}</b></div>
      ${predRow}
      <div class="row">${whoStatus(state.layer, v)}</div>
      <div class="row"><span>Source</span><b>${state.layer === 'pressure' ? 'Lupus Cortex composite' : 'ERA5 via Open-Meteo'}</b></div>`;
  } else if (type === 'pop') {
    html = `<h3>Population cell</h3>
      <div class="row"><span>Lat / Lon</span><b>${id.lat.toFixed(3)}, ${id.lon.toFixed(3)}</b></div>
      <div class="row"><span>Density</span><b>${id.value.toFixed(1)} persons / 100m pixel</b></div>
      <div class="row"><span>Source</span><b>WorldPop 2020 (static)</b></div>`;
  }
  $('infoBody').innerHTML = html;
  $('infoPanel').style.display = 'block';
}

/* ---------- picking ---------- */
function onClick(e) {
  const r = renderer.domElement.getBoundingClientRect();
  pointer.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  pointer.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const targets = [];
  if (state.showStations) for (const sm of stationMeshes) targets.push(sm.dot, sm.bar);
  if (state.showGrid && LAYERS[state.layer].kind === 'grid') for (const gm of gridMeshes) targets.push(gm.mesh);
  if (state.showPop) popGroup.children.forEach(m => targets.push(m));
  const hits = raycaster.intersectObjects(targets, false);
  if (hits.length) {
    const u = hits[0].object.userData;
    if (u.type === 'pop') showInfo('pop', u);
    else showInfo(u.type, u.id);
  }
}

/* ---------- init ---------- */
async function init() {
  try { await loadData(); }
  catch (err) {
    $('err').style.display = 'block';
    $('err').textContent = 'Data load failed: ' + err.message +
      '. Serve this folder over HTTP (e.g. python3 -m http.server) — browsers block fetch() on file://.';
    return;
  }
  idwFn = makeIDW(D.elevation);

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b1220);
  scene.fog = new THREE.Fog(0x0b1220, 400, 1400);
  camera = new THREE.PerspectiveCamera(50, innerWidth/innerHeight, 0.5, 5000);
  camera.position.set(60, 170, 300);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(innerWidth, innerHeight);
  $('scene').appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.06;
  controls.maxPolarAngle = Math.PI * 0.49;
  controls.target.set(0, 0, 0);

  scene.add(new THREE.HemisphereLight(0xdfeaff, 0x1a2418, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(200, 300, 120); scene.add(sun);

  raycaster = new THREE.Raycaster(); pointer = new THREE.Vector2();

  buildTerrain(); buildStations(); buildGrid(); buildPopulation();
  wireUI(); updateDay();

  renderer.domElement.addEventListener('click', onClick);
  addEventListener('resize', () => {
    camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });
  (function animate(){ requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); })();

  $('srcLine').textContent = 'Sources: ' + Object.values(D.meta.sources).slice(0,4).join(' · ');
}

function wireUI() {
  // layer selector (grouped)
  const sel = $('layerSel');
  const og1 = document.createElement('optgroup'); og1.label = 'Air quality (stations)';
  const og2 = document.createElement('optgroup'); og2.label = 'Grid (ERA5 / derived)';
  for (const [key, L] of Object.entries(LAYERS)) {
    const o = document.createElement('option'); o.value = key; o.textContent = L.label;
    (L.kind === 'station' ? og1 : og2).appendChild(o);
  }
  sel.append(og1, og2);
  sel.value = state.layer;
  sel.onchange = () => {
    state.layer = sel.value; state.showPred = false; $('predToggle').checked = false;
    $('predRow').style.display = LAYERS[state.layer].pred ? 'flex' : 'none';
    updateDay();
  };
  $('predRow').style.display = 'none';
  $('predToggle').onchange = e => { state.showPred = e.target.checked; updateDay(); };

  $('daySlider').max = 364;
  $('daySlider').oninput = e => { state.day = +e.target.value; updateDay(); };
  $('playBtn').onclick = () => {
    state.playing = !state.playing;
    $('playBtn').textContent = state.playing ? '⏸ Pause' : '▶ Play year';
    if (state.playing) state.playTimer = setInterval(() => {
      state.day = (state.day + 8) % 365; updateDay();
    }, 120);
    else clearInterval(state.playTimer);
  };
  const tgl = (id, key, fn) => { $(id).onchange = e => { state[key] = e.target.checked; fn(); }; };
  tgl('tglStations','showStations', () => updateDay());
  tgl('tglGrid','showGrid', () => updateDay());
  $('tglTerrain').onchange = e => { terrainMesh.visible = e.target.checked; };
  $('tglPop').onchange = e => { popGroup.visible = e.target.checked; };
  $('closeInfo').onclick = () => { $('infoPanel').style.display = 'none'; state.selected = null; };

  // caveats
  $('caveats').innerHTML = D.meta.caveats.map(c => `<span class="chip">⚠ ${c}</span>`).join('');
}

init();
