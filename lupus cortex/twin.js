/* Lupus Cortex 3D Digital Twin Studio — full 3D city modeling environment.
   Real data: OpenAQ stations, ERA5 grid, WorldPop, Copernicus DEM, RF predictions.
   Buildings procedurally modeled from population density (labeled as modeled).
   Tools: select, measure, add/delete buildings, camera presets, sun control. */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const DATA_DIR = 'data/';
const CENTER = { lat: 23.8, lon: 90.275 };
const KM_PER_DEG_LAT = 110.9, KM_PER_DEG_LON = 101.4;

// viridis colormap
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

const LAYERS = {
  pm25:     { kind:'station', label:'PM2.5', unit:'µg/m³', who:15, whoOrg:'WHO 2021 (24-h)' },
  pm10:     { kind:'station', label:'PM10', unit:'µg/m³', who:45, whoOrg:'WHO 2021 (24-h)' },
  o3:       { kind:'station', label:'O₃', unit:'µg/m³', who:100, whoOrg:'WHO 2021 (8-h)' },
  so2:      { kind:'station', label:'SO₂', unit:'µg/m³', who:40, whoOrg:'WHO 2021 (24-h)' },
  no2:      { kind:'station', label:'NO₂', unit:'ppb', who:25, whoOrg:'WHO 2021 (24-h)' },
  co:       { kind:'station', label:'CO', unit:'ppb', who:4000, whoOrg:'WHO 2021 (24-h)' },
  temp_mean:{ kind:'grid', label:'Air Temp', unit:'°C', pred:true },
  humidity: { kind:'grid', label:'Humidity', unit:'%', pred:true },
  precip:   { kind:'grid', label:'Precipitation', unit:'mm', pred:true },
  ndvi:     { kind:'grid', label:'NDVI', unit:'index', pred:false },
  lst:      { kind:'grid', label:'Land Surface Temp', unit:'°C', pred:false },
  flood:    { kind:'grid', label:'Flood Risk', unit:'index' },
  nightlights: { kind:'grid', label:'Night Lights', unit:'nW/cm²/sr' },
};

const state = {
  day: 0, layer: 'pm25', tool: 'select',
  showBuildings: true, showTerrain: true, showStations: true, showGrid: true, showPop: true,
  playing: false, playTimer: null, vertExag: 25,
  selected: null, measurePoints: [],
};
let D = {};
let scene, camera, renderer, controls, raycaster, pointer;
let terrainMesh, stationGroup, gridGroup, popGroup, buildingGroup, measureGroup;
let sun, hemi;
let buildingMeshes = [], stationMeshes = [], gridMeshes = [];
let dates = [];

const $ = id => document.getElementById(id);
function latLonToXZ(lat, lon) {
  return [ (lon - CENTER.lon) * KM_PER_DEG_LON, -(lat - CENTER.lat) * KM_PER_DEG_LAT ];
}
function xzToLatLon(x, z) {
  return [ CENTER.lat - z / KM_PER_DEG_LAT, CENTER.lon + x / KM_PER_DEG_LON ];
}

/* ---------- data loading ---------- */
function setLoad(pct, msg) {
  $('loadBar').style.width = pct + '%';
  if (msg) $('loadMsg').textContent = msg;
}
async function loadData() {
  const files = ['twin_meta','twin_stations','twin_elevation','twin_population'];
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    setLoad(5 + i * 15, 'Loading ' + f + '…');
    const res = await fetch(DATA_DIR + f + '.json');
    if (!res.ok) throw new Error('Cannot load ' + f + '.json (HTTP ' + res.status + ')');
    D[f.replace('twin_','')] = await res.json();
  }
  D.grid = [];
  for (let p = 1; p <= 4; p++) {
    setLoad(65 + p * 5, 'Loading grid part ' + p + '/4…');
    const res = await fetch(DATA_DIR + 'twin_grid_p' + p + '.json');
    if (!res.ok) throw new Error('Cannot load twin_grid_p' + p + '.json');
    D.grid.push(...await res.json());
  }
  // dates from first grid point
  if (D.grid[0]?.daily) {
    const k = Object.keys(D.grid[0].daily)[0];
    dates = D.grid[0].daily[k]?.map((_, i) => D.grid[0].dates?.[i] || ('day ' + i)) || [];
  }
  if (!dates.length) dates = Array.from({length: 365}, (_, i) => '2025-' + String(Math.floor(i/30)+1).padStart(2,'0') + '-' + String(i%30+1).padStart(2,'0'));
  setLoad(95, 'Building scene…');
}

/* ---------- scene setup ---------- */
function initScene() {
  const vp = $('viewport');
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0f1a);
  scene.fog = new THREE.Fog(0x0a0f1a, 150, 600);

  camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 0.1, 2000);
  camera.position.set(60, 80, 120);

  renderer = new THREE.WebGLRenderer({ antialias:true, preserveDrawingBuffer:true });
  renderer.setSize(innerWidth, innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  vp.appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.maxPolarAngle = Math.PI * 0.495;

  // lights
  hemi = new THREE.HemisphereLight(0xbdd7ff, 0x1a2332, 0.55);
  scene.add(hemi);
  sun = new THREE.DirectionalLight(0xfff2dd, 1.6);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.left = -150; sun.shadow.camera.right = 150;
  sun.shadow.camera.top = 150; sun.shadow.camera.bottom = -150;
  sun.shadow.camera.far = 500;
  scene.add(sun);
  updateSun(12);

  // groups
  terrainMesh = null;
  stationGroup = new THREE.Group(); gridGroup = new THREE.Group();
  popGroup = new THREE.Group(); buildingGroup = new THREE.Group();
  measureGroup = new THREE.Group();
  scene.add(stationGroup, gridGroup, popGroup, buildingGroup, measureGroup);

  raycaster = new THREE.Raycaster();
  pointer = new THREE.Vector2();

  renderer.domElement.addEventListener('pointerdown', onPointerDown);
  renderer.domElement.addEventListener('pointermove', onPointerMove);
  window.addEventListener('resize', () => {
    camera.aspect = innerWidth/innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });
  window.addEventListener('keydown', onKey);
}

function updateSun(hour) {
  const ang = ((hour - 6) / 12) * Math.PI; // 6h=east horizon, 18h=west
  const r = 250;
  sun.position.set(Math.cos(ang) * r, Math.max(20, Math.sin(ang) * r), 80);
  const night = hour < 5.5 || hour > 18.5;
  scene.background.setHex(night ? 0x05070d : 0x0a0f1a);
  hemi.intensity = night ? 0.25 : 0.55;
  sun.intensity = night ? 0.15 : 1.6;
}

/* ---------- terrain from DEM ---------- */
function buildTerrain() {
  const elev = D.elevation || [];
  if (!elev.length) return;
  // grid extent from elevation points
  let minLat=99,maxLat=-99,minLon=999,maxLon=-999;
  for (const p of elev) {
    minLat=Math.min(minLat,p.lat); maxLat=Math.max(maxLat,p.lat);
    minLon=Math.min(minLon,p.lon); maxLon=Math.max(maxLon,p.lon);
  }
  const NX=48, NZ=48;
  const geo = new THREE.PlaneGeometry(
    (maxLon-minLon)*KM_PER_DEG_LON, (maxLat-minLat)*KM_PER_DEG_LAT, NX, NZ);
  geo.rotateX(-Math.PI/2);
  const pos = geo.attributes.position;
  const colors = new Float32Array(pos.count * 3);
  // IDW from DEM points
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    const [lat, lon] = xzToLatLon(x, z);
    let num=0, den=0;
    for (const p of elev) {
      const d2 = (p.lat-lat)**2 + (p.lon-lon)**2 + 1e-9;
      const w = 1/d2;
      num += p.elevation_m * w; den += w;
    }
    const h = den ? num/den : 5;
    pos.setY(i, h * state.vertExag / 100 * 10);
    const c = cmap(Math.min(1, h/50));
    colors[i*3]=c.r; colors[i*3+1]=c.g; colors[i*3+2]=c.b;
  }
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({ vertexColors:true, roughness:0.95, metalness:0 });
  terrainMesh = new THREE.Mesh(geo, mat);
  terrainMesh.receiveShadow = true;
  terrainMesh.userData.isTerrain = true;
  const [cx, cz] = latLonToXZ((minLat+maxLat)/2, (minLon+maxLon)/2);
  terrainMesh.position.set(cx, 0, cz);
  scene.add(terrainMesh);
  terrainMesh.visible = state.showTerrain;
}

function terrainHeightAt(lat, lon) {
  const elev = D.elevation || [];
  let num=0, den=0;
  for (const p of elev) {
    const d2 = (p.lat-lat)**2 + (p.lon-lon)**2 + 1e-9;
    const w = 1/d2;
    num += p.elevation_m * w; den += w;
  }
  return den ? num/den : 5;
}

/* ---------- buildings (procedural from population) ---------- */
function buildBuildings() {
  const pop = D.population || [];
  buildingMeshes = [];
  const boxGeo = new THREE.BoxGeometry(1, 1, 1);
  // seeded random for stable layout
  let seed = 42;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;

  for (const p of pop) {
    const density = p.population || 0;
    if (density < 50) continue;
    const n = Math.min(14, Math.floor(density / 4000) + 1);
    const [bx, bz] = latLonToXZ(p.lat, p.lon);
    const baseH = terrainHeightAt(p.lat, p.lon) * state.vertExag / 100 * 10;
    for (let i = 0; i < n; i++) {
      const ox = (rnd()-0.5) * 3.5, oz = (rnd()-0.5) * 3.5;
      const w = 0.4 + rnd()*0.9, dpt = 0.4 + rnd()*0.9;
      // taller in dense areas
      const h = Math.min(28, 2 + rnd() * rnd() * (density/2500));
      const hue = 0.55 + rnd()*0.08;
      const mat = new THREE.MeshStandardMaterial({
        color: new THREE.Color().setHSL(hue, 0.25, 0.32 + rnd()*0.14),
        roughness: 0.85
      });
      const m = new THREE.Mesh(boxGeo, mat);
      m.scale.set(w, h, dpt);
      m.position.set(bx+ox, baseH + h/2, bz+oz);
      m.castShadow = true; m.receiveShadow = true;
      m.userData = { type:'building', lat:p.lat+ox/KM_PER_DEG_LAT, lon:p.lon+ox/KM_PER_DEG_LON,
                     height_m:+h.toFixed(1), pop:Math.round(density), modeled:true };
      buildingGroup.add(m);
      buildingMeshes.push(m);
    }
  }
  buildingGroup.visible = state.showBuildings;
  updateStats();
}

/* ---------- data overlays ---------- */
function layerValue(layerKey, day) {
  const L = LAYERS[layerKey];
  return L; // accessor used below
}
function getStationValue(s, key, day) {
  const arr = s[key];
  if (!arr) return null;
  return arr[Math.min(day, arr.length-1)] ?? null;
}
function getGridValue(g, key, day) {
  const arr = g.daily?.[key];
  if (!arr) return null;
  return arr[Math.min(day, arr.length-1)] ?? null;
}
function computeRanges() {
  D.ranges = {};
  for (const key of Object.keys(LAYERS)) {
    const L = LAYERS[key], vals = [];
    const push = v => { if (v != null && Number.isFinite(+v)) vals.push(+v); };
    if (L.kind === 'station') for (const s of D.stations||[]) push(getStationValue(s, key, state.day));
    else for (const g of D.grid||[]) push(getGridValue(g, key, state.day));
    // use all days for stable range
    vals.length = 0;
    if (L.kind === 'station') for (const s of D.stations||[]) for (let d=0; d<365; d+=7) push(getStationValue(s, key, d));
    else for (const g of D.grid||[]) for (let d=0; d<365; d+=7) push(getGridValue(g, key, d));
    vals.sort((a,b)=>a-b);
    D.ranges[key] = vals.length ? [vals[Math.floor(vals.length*0.02)], vals[Math.floor(vals.length*0.98)] ?? vals[vals.length-1]] : [0,1];
  }
}
function normVal(key, v) {
  const [lo, hi] = D.ranges[key] || [0,1];
  if (hi <= lo) return 0.5;
  return Math.min(1, Math.max(0, (v-lo)/(hi-lo)));
}

function buildStations() {
  stationMeshes = [];
  const geo = new THREE.CylinderGeometry(0.35, 0.5, 1, 10);
  for (const s of D.stations || []) {
    const [x, z] = latLonToXZ(s.lat, s.lon);
    const h = terrainHeightAt(s.lat, s.lon) * state.vertExag/100*10;
    const mat = new THREE.MeshStandardMaterial({ color:0x61e7c7, roughness:0.5 });
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, h + 6, z);
    m.scale.y = 12;
    m.castShadow = true;
    m.userData = { type:'station', id:s.id || s.station_id, lat:s.lat, lon:s.lon, name:s.name || s.station_id };
    stationGroup.add(m);
    stationMeshes.push(m);
    // label sprite
    const cv = document.createElement('canvas'); cv.width=256; cv.height=64;
    const cx2 = cv.getContext('2d');
    cx2.fillStyle='rgba(10,15,26,.85)'; cx2.fillRect(0,0,256,64);
    cx2.fillStyle='#61e7c7'; cx2.font='bold 26px sans-serif'; cx2.textAlign='center';
    cx2.fillText(String(s.name || s.station_id || '').slice(0,16), 128, 42);
    const tex = new THREE.CanvasTexture(cv);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map:tex, transparent:true }));
    sp.scale.set(10, 2.5, 1);
    sp.position.set(x, h + 14, z);
    sp.userData.isLabel = true;
    stationGroup.add(sp);
  }
  stationGroup.visible = state.showStations;
}

function buildGrid() {
  gridMeshes = [];
  const geo = new THREE.BoxGeometry(2.2, 0.6, 2.2);
  for (const g of D.grid || []) {
    const [x, z] = latLonToXZ(g.lat, g.lon);
    const h = terrainHeightAt(g.lat, g.lon) * state.vertExag/100*10;
    const mat = new THREE.MeshStandardMaterial({ color:0x3388ff, roughness:0.6, transparent:true, opacity:0.85 });
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, h + 0.3, z);
    m.userData = { type:'grid', lat:g.lat, lon:g.lon, id:g.id };
    gridGroup.add(m);
    gridMeshes.push(m);
  }
  gridGroup.visible = state.showGrid;
}

function buildPopulation() {
  const geo = new THREE.CylinderGeometry(0.5, 0.5, 1, 8);
  let maxPop = 1;
  for (const p of D.population || []) maxPop = Math.max(maxPop, p.population||0);
  for (const p of D.population || []) {
    const [x, z] = latLonToXZ(p.lat, p.lon);
    const h = terrainHeightAt(p.lat, p.lon) * state.vertExag/100*10;
    const t = (p.population||0)/maxPop;
    const mat = new THREE.MeshStandardMaterial({ color:cmap(t), roughness:0.6 });
    const m = new THREE.Mesh(geo, mat);
    const bh = 1 + t*18;
    m.scale.y = bh;
    m.position.set(x, h + bh/2, z);
    m.userData = { type:'population', lat:p.lat, lon:p.lon, pop:p.population };
    popGroup.add(m);
  }
  popGroup.visible = state.showPop;
}

function refreshLayerColors() {
  const key = state.layer, L = LAYERS[key];
  if (!L) return;
  if (L.kind === 'station') {
    for (let i = 0; i < stationMeshes.length; i++) {
      const m = stationMeshes[i], s = D.stations[i];
      const v = getStationValue(s, key, state.day);
      m.material.color.copy(v == null ? new THREE.Color(0x555566) : cmap(normVal(key, v)));
      m.userData.value = v;
    }
  } else {
    for (let i = 0; i < gridMeshes.length; i++) {
      const m = gridMeshes[i], g = D.grid[i];
      const v = getGridValue(g, key, state.day);
      m.material.color.copy(v == null ? new THREE.Color(0x222233) : cmap(normVal(key, v)));
      m.userData.value = v;
    }
  }
}

/* ---------- UI: layers ---------- */
function buildLayerList() {
  const el = $('layerList');
  el.innerHTML = '';
  for (const [key, L] of Object.entries(LAYERS)) {
    const div = document.createElement('div');
    div.className = 'layer-item' + (key === state.layer ? ' active' : '');
    div.innerHTML = '<span class="layer-swatch" style="background:' +
      '#'+cmap(0.65).getHexString() + '"></span><span>' + L.label +
      ' <span style="color:var(--muted);font-size:11px">' + L.unit + '</span></span>';
    div.onclick = () => {
      state.layer = key;
      document.querySelectorAll('#layerList .layer-item').forEach(e => e.classList.remove('active'));
      div.classList.add('active');
      refreshLayerColors();
      updateStats();
    };
    el.appendChild(div);
  }
}

/* ---------- selection & properties ---------- */
function showProps(obj) {
  const panel = $('propsPanel'), body = $('propsBody'), actions = $('propsActions');
  const u = obj.userData;
  $('propsTitle').textContent = u.type === 'building' ? '🏢 Building' :
    u.type === 'station' ? '📡 Air Station' : u.type === 'grid' ? '🔲 Grid Cell' : 'Selection';
  let rows = '';
  const row = (k,v) => '<div class="prop-row"><span class="k">'+k+'</span><span class="v">'+v+'</span></div>';
  if (u.type === 'building') {
    rows += row('Height', u.height_m + ' m') + row('Population zone', u.pop?.toLocaleString?.() || '—') +
            row('Lat', (+u.lat).toFixed(4)) + row('Lon', (+u.lon).toFixed(4)) +
            row('Source', u.modeled ? 'Modeled (WorldPop)' : 'OSM');
    actions.innerHTML = '<button class="btn ghost" id="actDel">Delete</button>';
    $('actDel').onclick = () => { deleteSelected(); };
  } else if (u.type === 'station') {
    const s = D.stations.find(x => (x.id||x.station_id) == u.id);
    rows += row('Name', u.name || u.id) + row('Lat', (+u.lat).toFixed(4)) + row('Lon', (+u.lon).toFixed(4));
    const L = LAYERS[state.layer];
    if (L?.kind === 'station') {
      const v = getStationValue(s, state.layer, state.day);
      rows += row(L.label, (v == null ? 'no data' : (+v).toFixed(2) + ' ' + L.unit));
      if (L.who) rows += row('WHO guideline', L.who + ' ' + L.unit + ' (' + L.whoOrg + ')') +
        row('Status', v == null ? '—' : v <= L.who ? '✅ within guideline' : '⚠️ exceeds guideline');
    }
    actions.innerHTML = '';
  } else if (u.type === 'grid') {
    rows += row('Lat', (+u.lat).toFixed(4)) + row('Lon', (+u.lon).toFixed(4)) + row('Date', dates[state.day] || '');
    const L = LAYERS[state.layer];
    if (L?.kind === 'grid') {
      const g = D.grid.find(x => x.id == u.id);
      const v = getGridValue(g, state.layer, state.day);
      rows += row(L.label, (v == null ? 'no data' : (+v).toFixed(3) + ' ' + L.unit));
    }
    actions.innerHTML = '';
  } else if (u.type === 'population') {
    rows += row('Population', (u.pop||0).toLocaleString()) + row('Lat', (+u.lat).toFixed(4)) + row('Lon', (+u.lon).toFixed(4));
    actions.innerHTML = '';
  }
  body.innerHTML = rows;
  panel.classList.add('show');
}
function hideProps() { $('propsPanel').classList.remove('show'); state.selected = null; }

function setSelected(obj) {
  if (state.selected?.material?.emissive) state.selected.material.emissive.setHex(0x000000);
  state.selected = obj;
  if (obj) {
    if (obj.material.emissive) obj.material.emissive.setHex(0x334422);
    showProps(obj);
  } else hideProps();
}
function deleteSelected() {
  if (state.selected?.userData.type === 'building') {
    buildingGroup.remove(state.selected);
    buildingMeshes = buildingMeshes.filter(m => m !== state.selected);
    hideProps(); updateStats();
  }
}

/* ---------- measure tool ---------- */
function addMeasurePoint(pt) {
  state.measurePoints.push(pt);
  const geo = new THREE.SphereGeometry(0.5, 12, 12);
  const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color:0xff5555 }));
  m.position.copy(pt);
  measureGroup.add(m);
  if (state.measurePoints.length >= 2) {
    const pts = state.measurePoints;
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color:0xff5555 }));
    measureGroup.add(line);
    // distance in km (x,z plane)
    let dist = 0;
    for (let i = 1; i < pts.length; i++)
      dist += Math.hypot(pts[i].x-pts[i-1].x, pts[i].z-pts[i-1].z);
    showMeasureResult(dist);
  }
}
function showMeasureResult(km) {
  const panel = $('propsPanel');
  $('propsTitle').textContent = '📏 Measurement';
  $('propsBody').innerHTML = '<div class="prop-row"><span class="k">Distance</span><span class="v">' +
    km.toFixed(2) + ' km</span></div>';
  $('propsActions').innerHTML = '<button class="btn ghost" id="actClearM">Clear</button>';
  $('actClearM').onclick = clearMeasure;
  panel.classList.add('show');
}
function clearMeasure() {
  measureGroup.clear();
  state.measurePoints = [];
  hideProps();
}

/* ---------- add building ---------- */
function addBuildingAt(pt) {
  const [lat, lon] = xzToLatLon(pt.x, pt.z);
  const h = 4 + Math.random() * 14;
  const baseH = terrainHeightAt(lat, lon) * state.vertExag/100*10;
  const mat = new THREE.MeshStandardMaterial({ color:new THREE.Color().setHSL(0.6, 0.3, 0.45), roughness:0.85 });
  const m = new THREE.Mesh(new THREE.BoxGeometry(1.2, 1, 1.2), mat);
  m.scale.y = h;
  m.position.set(pt.x, baseH + h/2, pt.z);
  m.castShadow = true; m.receiveShadow = true;
  m.userData = { type:'building', lat, lon, height_m:+h.toFixed(1), pop:0, modeled:false, userAdded:true };
  buildingGroup.add(m);
  buildingMeshes.push(m);
  setSelected(m);
  updateStats();
}

/* ---------- pointer ---------- */
function pickAt(clientX, clientY) {
  pointer.x = (clientX/innerWidth)*2 - 1;
  pointer.y = -(clientY/innerHeight)*2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const targets = [...buildingMeshes, ...stationMeshes, ...gridMeshes].filter(m => m.parent.visible);
  const hits = raycaster.intersectObjects(targets, false);
  return hits[0] || null;
}
function groundPoint(clientX, clientY) {
  pointer.x = (clientX/innerWidth)*2 - 1;
  pointer.y = -(clientY/innerHeight)*2 + 1;
  raycaster.setFromCamera(pointer, camera);
  if (terrainMesh) {
    const hit = raycaster.intersectObject(terrainMesh, false)[0];
    if (hit) return hit.point;
  }
  const plane = new THREE.Plane(new THREE.Vector3(0,1,0), 0);
  const pt = new THREE.Vector3();
  raycaster.ray.intersectPlane(plane, pt);
  return pt;
}
function onPointerDown(e) {
  if (e.button !== 0) return;
  if (state.tool === 'measure') {
    const pt = groundPoint(e.clientX, e.clientY);
    if (pt) addMeasurePoint(pt.clone());
  } else if (state.tool === 'add') {
    const pt = groundPoint(e.clientX, e.clientY);
    if (pt) addBuildingAt(pt.clone());
  } else if (state.tool === 'delete') {
    const hit = pickAt(e.clientX, e.clientY);
    if (hit?.object.userData.type === 'building') {
      setSelected(hit.object); deleteSelected();
    }
  } else {
    const hit = pickAt(e.clientX, e.clientY);
    setSelected(hit ? hit.object : null);
  }
}
function onPointerMove(e) {
  if (state.tool !== 'select') return;
  const hit = pickAt(e.clientX, e.clientY);
  renderer.domElement.style.cursor = hit ? 'pointer' : 'default';
}

/* ---------- camera presets ---------- */
function setView(name) {
  const t = new THREE.Vector3(0, 0, 0);
  if (name === 'top') { camera.position.set(0, 320, 0.1); }
  else if (name === 'iso') { camera.position.set(90, 110, 130); }
  else if (name === 'street') { camera.position.set(8, 4, 18); }
  controls.target.copy(t);
  controls.update();
}

/* ---------- timeline ---------- */
function setDay(d) {
  state.day = Math.max(0, Math.min(dates.length-1, d));
  $('daySlider').value = state.day;
  $('daySlider').max = dates.length - 1;
  $('dateLabel').textContent = dates[state.day] || '';
  refreshLayerColors();
}
function togglePlay() {
  state.playing = !state.playing;
  $('btnPlay').textContent = state.playing ? '⏸️' : '▶️';
  if (state.playing) {
    state.playTimer = setInterval(() => setDay((state.day + 7) % dates.length), 400);
  } else clearInterval(state.playTimer);
}

/* ---------- tools ---------- */
function setTool(t) {
  state.tool = t;
  for (const id of ['toolSelect','toolMeasure','toolAdd','toolDelete'])
    $(id).classList.remove('active');
  $({select:'toolSelect',measure:'toolMeasure',add:'toolAdd',delete:'toolDelete'}[t]).classList.add('active');
  if (t !== 'measure') clearMeasure();
  renderer.domElement.style.cursor = t === 'select' ? 'default' : 'crosshair';
}

function onKey(e) {
  if (e.target.tagName === 'INPUT') return;
  const k = e.key.toLowerCase();
  if (k === 'v') setTool('select');
  else if (k === 'm') setTool('measure');
  else if (k === 'b') setTool('add');
  else if (k === 'delete' || k === 'backspace') deleteSelected();
  else if (k === '1') setView('top');
  else if (k === '2') setView('iso');
  else if (k === '3') setView('street');
  else if (k === 'p') screenshot();
  else if (k === 'h') $('help').style.display = $('help').style.display === 'none' ? 'block' : 'none';
  else if (k === ' ') { e.preventDefault(); togglePlay(); }
  else if (k === 'arrowright') setDay(state.day + 1);
  else if (k === 'arrowleft') setDay(state.day - 1);
}

function screenshot() {
  renderer.render(scene, camera);
  const a = document.createElement('a');
  a.download = 'lupus-twin-' + (dates[state.day] || 'view') + '.png';
  a.href = renderer.domElement.toDataURL('image/png');
  a.click();
}

/* ---------- stats ---------- */
function updateStats() {
  const L = LAYERS[state.layer];
  $('statText').innerHTML =
    '<b>' + buildingMeshes.length + '</b> buildings · ' +
    '<b>' + (D.stations||[]).length + '</b> stations · ' +
    '<b>' + (D.grid||[]).length + '</b> grid cells · ' +
    'layer: <b>' + L.label + '</b> · ' + (dates[state.day] || '');
}

/* ---------- wire UI ---------- */
function wireUI() {
  $('toolSelect').onclick = () => setTool('select');
  $('toolMeasure').onclick = () => setTool('measure');
  $('toolAdd').onclick = () => setTool('add');
  $('toolDelete').onclick = () => setTool('delete');
  $('viewTop').onclick = () => setView('top');
  $('viewIso').onclick = () => setView('iso');
  $('viewStreet').onclick = () => setView('street');
  $('btnShot').onclick = screenshot;
  $('btnHelp').onclick = () => {
    $('help').style.display = $('help').style.display === 'none' ? 'block' : 'none';
  };
  $('btnPlay').onclick = togglePlay;
  $('daySlider').oninput = e => setDay(+e.target.value);
  $('chkBuildings').onchange = e => { state.showBuildings = e.target.checked; buildingGroup.visible = state.showBuildings; };
  $('chkTerrain').onchange = e => { state.showTerrain = e.target.checked; if (terrainMesh) terrainMesh.visible = state.showTerrain; };
  $('chkStations').onchange = e => { state.showStations = e.target.checked; stationGroup.visible = state.showStations; };
  $('chkGrid').onchange = e => { state.showGrid = e.target.checked; gridGroup.visible = state.showGrid; };
  $('chkPop').onchange = e => { state.showPop = e.target.checked; popGroup.visible = state.showPop; };
  $('sunTime').oninput = e => updateSun(+e.target.value);
  $('vertExag').onchange = e => {
    state.vertExag = Math.max(1, Math.min(100, +e.target.value || 25));
    rebuildHeights();
  };
}

function rebuildHeights() {
  // rebuild terrain + reposition objects after exaggeration change
  if (terrainMesh) { scene.remove(terrainMesh); terrainMesh.geometry.dispose(); }
  buildTerrain();
  const place = (m, lat, lon, lift) => {
    const [x, z] = latLonToXZ(lat, lon);
    const h = terrainHeightAt(lat, lon) * state.vertExag/100*10;
    m.position.x = x; m.position.z = z;
    if (lift != null) m.position.y = h + lift;
  };
  for (const m of buildingMeshes) {
    const u = m.userData;
    place(m, u.lat, u.lon, m.scale.y/2);
  }
  D.stations?.forEach((s, i) => { const m = stationMeshes[i]; if (m) place(m, s.lat, s.lon, 6); });
  D.grid?.forEach((g, i) => { const m = gridMeshes[i]; if (m) place(m, g.lat, g.lon, 0.3); });
  refreshLayerColors();
}

/* ---------- main ---------- */
function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

async function main() {
  try {
    initScene();
    wireUI();
    buildLayerList();
    await loadData();
    setLoad(96, 'Building terrain…');
    buildTerrain();
    setLoad(97, 'Placing buildings…');
    buildBuildings();
    buildStations();
    buildGrid();
    buildPopulation();
    computeRanges();
    setDay(0);
    refreshLayerColors();
    updateStats();
    setLoad(100, 'Ready');
    $('loading').style.display = 'none';
    animate();
  } catch (err) {
    $('loadMsg').textContent = '❌ ' + err.message;
    $('loadMsg').style.color = '#ff8d8d';
    console.error(err);
  }
}

main();
