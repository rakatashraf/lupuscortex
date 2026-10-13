/* Lupus Cortex 3D Terrain Generator — topoexport-style.
   Search any place → generate 3D terrain from real satellite elevation (Terrarium)
   + satellite imagery (Esri World Imagery). Edit the model → auto-send to backend
   AI model for analysis → results shown in frontend. */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = id => document.getElementById(id);

// ---------- tile math ----------
const TILE_Z = 12; // zoom for elevation detail
function latLonToTile(lat, lon, z) {
  const n = 2 ** z;
  const x = Math.floor((lon + 180) / 360 * n);
  const latR = lat * Math.PI / 180;
  const y = Math.floor((1 - Math.log(Math.tan(latR) + 1/Math.cos(latR)) / Math.PI) / 2 * n);
  return [x, y];
}
function tileToLatLon(x, y, z) {
  const n = 2 ** z;
  const lon = x / n * 360 - 180;
  const latR = Math.atan(Math.sinh(Math.PI * (1 - 2*y/n)));
  return [latR * 180 / Math.PI, lon];
}
// Terrarium: elevation = R*256 + G + B/256 - 32768
function decodeTerrarium(r, g, b) { return r * 256 + g + b / 256 - 32768; }

// ---------- state ----------
const state = {
  tool: 'select', vertExag: 1.5, texMode: 'satellite',
  center: null, sizeKm: 10,
  buildings: [], selected: null,
  terrain: null, analyzing: false,
};
let scene, camera, renderer, controls, raycaster, pointer;
let sun, buildingGroup, measureGroup;
let elevGrid = null; // {lats, lons, data, rows, cols, minElev, maxElev}

// ---------- scene ----------
function initScene() {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0f1a);
  scene.fog = new THREE.Fog(0x0a0f1a, 200, 800);
  camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 0.1, 5000);
  camera.position.set(0, 30, 40);
  renderer = new THREE.WebGLRenderer({ antialias:true, preserveDrawingBuffer:true });
  renderer.setSize(innerWidth, innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  $('viewport').appendChild(renderer.domElement);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.maxPolarAngle = Math.PI * 0.495;

  scene.add(new THREE.HemisphereLight(0xbdd7ff, 0x1a2332, 0.7));
  sun = new THREE.DirectionalLight(0xfff2dd, 1.8);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left:-80, right:80, top:80, bottom:-80, far:600 });
  sun.position.set(100, 150, 60);
  scene.add(sun);

  buildingGroup = new THREE.Group();
  measureGroup = new THREE.Group();
  scene.add(buildingGroup, measureGroup);

  raycaster = new THREE.Raycaster();
  pointer = new THREE.Vector2();
  renderer.domElement.addEventListener('pointerdown', onPointerDown);
  window.addEventListener('resize', () => {
    camera.aspect = innerWidth/innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });
  window.addEventListener('keydown', onKey);
  animate();
}
function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

// ---------- status ----------
function setStatus(text, mode) {
  $('statusText').textContent = text;
  $('statusDot').className = 'dot' + (mode ? ' ' + mode : '');
}

// ---------- place search (Nominatim) ----------
async function searchPlace(q) {
  const url = 'https://nominatim.openstreetmap.org/search?q=' + encodeURIComponent(q) +
    '&format=json&limit=6&addressdetails=1';
  const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
  if (!res.ok) throw new Error('Search failed (HTTP ' + res.status + ')');
  return res.json();
}
function showSearchResults(items) {
  const el = $('searchResults');
  if (!items.length) { el.innerHTML = '<div class="sr-item">No results found.</div>'; el.classList.add('show'); return; }
  el.innerHTML = items.map((it, i) =>
    '<div class="sr-item" data-i="' + i + '"><div class="t">' + escapeHtml(it.display_name.split(',').slice(0,2).join(',')) +
    '</div><div class="s">' + escapeHtml(it.display_name) + '</div></div>').join('');
  el.classList.add('show');
  el.querySelectorAll('.sr-item').forEach(div => {
    div.onclick = () => {
      const it = items[+div.dataset.i];
      $('inLat').value = (+it.lat).toFixed(4);
      $('inLon').value = (+it.lon).toFixed(4);
      el.classList.remove('show');
      generateTerrain();
    };
  });
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// ---------- elevation fetch (Terrarium tiles) ----------
function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Tile load failed'));
    img.src = url;
  });
}
async function fetchElevationGrid(lat, lon, sizeKm) {
  // Determine tile range covering the bbox at zoom TILE_Z
  const halfDegLat = (sizeKm/2) / 110.9;
  const halfDegLon = (sizeKm/2) / (111.32 * Math.cos(lat * Math.PI/180));
  const [x0, y0] = latLonToTile(lat + halfDegLat, lon - halfDegLon, TILE_Z);
  const [x1, y1] = latLonToTile(lat - halfDegLat, lon + halfDegLon, TILE_Z);
  const tx0 = Math.min(x0,x1), tx1 = Math.max(x0,x1);
  const ty0 = Math.min(y0,y1), ty1 = Math.max(y0,y1);

  const RES = 128; // samples per side
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = RES;
  const ctx = canvas.getContext('2d', { willReadFrequently:true });

  // For simplicity: fetch center tile + neighbors, sample elevation grid
  // Use a grid of sample points, each decoded from the tile covering it
  const tileCache = new Map();
  async function tileImg(tx, ty) {
    const k = tx + ',' + ty;
    if (!tileCache.has(k)) {
      tileCache.set(k, loadImage('https://s3.amazonaws.com/elevation-tiles-prod/terrarium/' + TILE_Z + '/' + tx + '/' + ty + '.png'));
    }
    return tileCache.get(k);
  }

  const data = new Float32Array(RES * RES);
  const lats = [], lons = [];
  let minE = 1e9, maxE = -1e9;
  const total = RES * RES;
  let done = 0;

  for (let r = 0; r < RES; r++) {
    const fLat = lat + halfDegLat - (r / (RES-1)) * 2 * halfDegLat;
    lats.push(fLat);
    for (let c = 0; c < RES; c++) {
      const fLon = lon - halfDegLon + (c / (RES-1)) * 2 * halfDegLon;
      if (r === 0) lons.push(fLon);
      const [tx, ty] = latLonToTile(fLat, fLon, TILE_Z);
      try {
        const img = await tileImg(tx, ty);
        // pixel within tile
        const n = 2 ** TILE_Z;
        const px = Math.floor(((fLon + 180) / 360 * n - tx) * 256);
        const py = Math.floor(((1 - Math.log(Math.tan(fLat*Math.PI/180) + 1/Math.cos(fLat*Math.PI/180)) / Math.PI) / 2 * n - ty) * 256);
        const tc = document.createElement('canvas');
        tc.width = tc.height = 1;
        const tctx = tc.getContext('2d', { willReadFrequently:true });
        tctx.drawImage(img, Math.max(0,Math.min(255,px)), Math.max(0,Math.min(255,py)), 1, 1, 0, 0, 1, 1);
        const d = tctx.getImageData(0, 0, 1, 1).data;
        const e = decodeTerrarium(d[0], d[1], d[2]);
        data[r*RES+c] = e;
        if (e > -1000 && e < 9000) { minE = Math.min(minE, e); maxE = Math.max(maxE, e); }
      } catch { data[r*RES+c] = 0; }
      done++;
      if (done % 2048 === 0) setLoad(done/total*70, 'Sampling elevation… ' + Math.round(done/total*100) + '%');
    }
  }
  return { data, lats, lons, rows:RES, cols:RES, minElev:minE, maxElev:maxE, lat, lon, sizeKm };
}

// ---------- satellite texture (Esri) ----------
async function buildSatelliteTexture(grid) {
  const TEX = 1024;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = TEX;
  const ctx = canvas.getContext('2d');
  // Esri tiles at zoom ~15 for the area
  const z = 15;
  const [tx0, ty0] = latLonToTile(grid.lats[0], grid.lons[0], z);
  const [tx1, ty1] = latLonToTile(grid.lats[grid.lats.length-1], grid.lons[grid.lons.length-1], z);
  const ntx = Math.min(4, Math.abs(tx1-tx0)+1), nty = Math.min(4, Math.abs(ty1-ty0)+1);
  const sx = Math.min(tx0,tx1), sy = Math.min(ty0,ty1);
  let loaded = 0;
  const jobs = [];
  for (let ty = 0; ty < nty; ty++) for (let tx = 0; tx < ntx; tx++) {
    jobs.push((async () => {
      try {
        const img = await loadImage('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/' + z + '/' + (sy+ty) + '/' + (sx+tx));
        ctx.drawImage(img, tx/ntx*TEX, ty/nty*TEX, TEX/ntx, TEX/nty);
      } catch {}
      loaded++;
      setLoad(70 + loaded/jobs.length*20, 'Loading satellite imagery…');
    })());
  }
  await Promise.all(jobs);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// ---------- terrain mesh ----------
function elevColor(t) {
  // green → yellow → brown → white
  const stops = [[0.13,0.55,0.13],[0.55,0.75,0.25],[0.55,0.42,0.25],[0.9,0.9,0.9]];
  t = Math.min(1, Math.max(0, t));
  const x = t * (stops.length-1), i = Math.floor(x), f = x-i;
  const a = stops[i], b = stops[Math.min(i+1, stops.length-1)];
  return new THREE.Color(a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f);
}
async function buildTerrainMesh(grid, satTex) {
  if (state.terrain) { scene.remove(state.terrain); state.terrain.geometry.dispose(); }
  const { rows, cols, data, minElev, maxElev, sizeKm } = grid;
  const geo = new THREE.PlaneGeometry(sizeKm, sizeKm, cols-1, rows-1);
  geo.rotateX(-Math.PI/2);
  const pos = geo.attributes.position;
  const colors = new Float32Array(pos.count * 3);
  const range = Math.max(1, maxElev - minElev);
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const i = r * cols + c;
    const e = data[i];
    pos.setY(i, e * state.vertExag / 100);
    const t = (e - minElev) / range;
    const col = elevColor(t);
    colors[i*3] = col.r; colors[i*3+1] = col.g; colors[i*3+2] = col.b;
  }
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geo.computeVertexNormals();

  let mat;
  if (state.texMode === 'satellite' && satTex) {
    mat = new THREE.MeshStandardMaterial({ map:satTex, roughness:0.95 });
  } else if (state.texMode === 'wireframe') {
    mat = new THREE.MeshBasicMaterial({ wireframe:true, color:0x61e7c7 });
  } else {
    mat = new THREE.MeshStandardMaterial({ vertexColors:true, roughness:0.95 });
  }
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = true;
  mesh.userData.isTerrain = true;
  scene.add(mesh);
  state.terrain = mesh;
  elevGrid = grid;

  // fit camera
  const maxDim = Math.max(sizeKm, (maxElev-minElev) * state.vertExag/100);
  camera.position.set(sizeKm*0.7, maxDim*1.2 + 15, sizeKm*1.1);
  controls.target.set(0, 0, 0);
  controls.update();
  // shadow bounds
  Object.assign(sun.shadow.camera, { left:-sizeKm, right:sizeKm, top:sizeKm, bottom:-sizeKm });
  sun.shadow.camera.updateProjectionMatrix();
}

// ---------- main generate ----------
function setLoad(pct, msg) {
  const bar = $('loadBar'), m = $('loadMsg');
  if (bar) bar.style.width = pct + '%';
  if (m && msg) m.textContent = msg;
}
async function generateTerrain() {
  const lat = parseFloat($('inLat').value), lon = parseFloat($('inLon').value);
  const sizeKm = Math.min(50, Math.max(1, parseFloat($('inSize').value) || 10));
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) { setStatus('Enter valid coordinates', 'busy'); return; }
  state.center = { lat, lon }; state.sizeKm = sizeKm;
  $('loading').style.display = 'grid';
  setLoad(2, 'Fetching elevation data (Terrarium DEM)…');
  setStatus('Generating 3D terrain…', 'busy');
  try {
    const grid = await fetchElevationGrid(lat, lon, sizeKm);
    setLoad(72, 'Loading satellite imagery (Esri)…');
    const satTex = await buildSatelliteTexture(grid);
    setLoad(95, 'Building 3D mesh…');
    await buildTerrainMesh(grid, satTex);
    // clear old buildings
    buildingGroup.clear(); state.buildings = [];
    $('modelInfo').innerHTML =
      '📍 ' + lat.toFixed(4) + ', ' + lon.toFixed(4) + '<br>' +
      '📐 ' + sizeKm + ' × ' + sizeKm + ' km<br>' +
      '⛰️ Elevation ' + grid.minElev.toFixed(1) + ' – ' + grid.maxElev.toFixed(1) + ' m<br>' +
      '🛰️ Esri World Imagery + Terrarium DEM';
    updateBuildingInfo();
    setStatus('Terrain ready — edit or press 🧠 to analyze', 'ok');
    // auto-analyze the fresh terrain
    triggerAnalysis('terrain_generated');
  } catch (err) {
    console.error(err);
    setStatus('❌ ' + err.message, 'busy');
  } finally {
    $('loading').style.display = 'none';
  }
}

// ---------- buildings ----------
function addBuildingAt(point) {
  const h = 0.3 + Math.random() * 1.2; // km-scale relative
  const w = 0.25 + Math.random() * 0.4;
  const mat = new THREE.MeshStandardMaterial({
    color: new THREE.Color().setHSL(0.58, 0.3, 0.4 + Math.random()*0.15), roughness:0.85 });
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, 1, w), mat);
  // sit on terrain
  const e = sampleElevation(point.x, point.z);
  m.scale.y = h;
  m.position.set(point.x, e * state.vertExag/100 + h/2, point.z);
  m.castShadow = true; m.receiveShadow = true;
  m.userData = { type:'building', height_km:+h.toFixed(2) };
  buildingGroup.add(m);
  state.buildings.push(m);
  setSelected(m);
  updateBuildingInfo();
  triggerAnalysis('building_added');
}
function sampleElevation(x, z) {
  if (!elevGrid) return 0;
  const { rows, cols, data, sizeKm } = elevGrid;
  const c = Math.round((x/sizeKm + 0.5) * (cols-1));
  const r = Math.round((0.5 - z/sizeKm) * (rows-1));
  const cc = Math.max(0, Math.min(cols-1, c)), rr = Math.max(0, Math.min(rows-1, r));
  return data[rr*cols+cc] || 0;
}
function updateBuildingInfo() {
  $('buildingInfo').textContent = state.buildings.length + ' buildings placed.';
}
function deleteSelected() {
  if (state.selected?.userData.type === 'building') {
    buildingGroup.remove(state.selected);
    state.buildings = state.buildings.filter(b => b !== state.selected);
    setSelected(null);
    updateBuildingInfo();
    triggerAnalysis('building_deleted');
  }
}

// ---------- selection / measure ----------
function setSelected(obj) {
  if (state.selected?.material?.emissive) state.selected.material.emissive.setHex(0x000000);
  state.selected = obj;
  if (obj?.material?.emissive) obj.material.emissive.setHex(0x2a4a3a);
}
let measurePts = [];
function pickBuilding(cx, cy) {
  pointer.x = (cx/innerWidth)*2-1; pointer.y = -(cy/innerHeight)*2+1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(state.buildings, false);
  return hits[0]?.object || null;
}
function groundPoint(cx, cy) {
  pointer.x = (cx/innerWidth)*2-1; pointer.y = -(cy/innerHeight)*2+1;
  raycaster.setFromCamera(pointer, camera);
  if (state.terrain) {
    const hit = raycaster.intersectObject(state.terrain, false)[0];
    if (hit) return hit.point;
  }
  const pt = new THREE.Vector3();
  raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0,1,0), 0), pt);
  return pt;
}
function onPointerDown(e) {
  if (e.button !== 0 || !$('loading').style.display.includes('none') && $('loading').style.display !== 'none') return;
  if (state.tool === 'building') {
    const pt = groundPoint(e.clientX, e.clientY);
    if (pt) addBuildingAt(pt);
  } else if (state.tool === 'measure') {
    const pt = groundPoint(e.clientX, e.clientY);
    if (pt) {
      measurePts.push(pt.clone());
      const s = new THREE.Mesh(new THREE.SphereGeometry(0.15, 10, 10),
        new THREE.MeshBasicMaterial({ color:0xff5555 }));
      s.position.copy(pt); measureGroup.add(s);
      if (measurePts.length === 2) {
        const d = measurePts[0].distanceTo(measurePts[1]);
        setStatus('📏 ' + d.toFixed(2) + ' km', 'ok');
        const line = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(measurePts),
          new THREE.LineBasicMaterial({ color:0xff5555 }));
        measureGroup.add(line);
        measurePts = [];
      }
    }
  } else if (state.tool === 'delete') {
    const b = pickBuilding(e.clientX, e.clientY);
    if (b) { setSelected(b); deleteSelected(); }
  } else {
    setSelected(pickBuilding(e.clientX, e.clientY));
  }
}
function onKey(e) {
  if (/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
  const k = e.key.toLowerCase();
  if (k === 'v') setTool('select');
  else if (k === 'b') setTool('building');
  else if (k === 'm') setTool('measure');
  else if (k === 'delete' || k === 'backspace') deleteSelected();
  else if (k === 'a') triggerAnalysis('manual');
  else if (k === 'p') screenshot();
  else if (k === '1') { camera.position.set(0, state.sizeKm*2.2, 0.1); controls.target.set(0,0,0); }
  else if (k === '2') { camera.position.set(state.sizeKm*0.7, state.sizeKm*0.8, state.sizeKm*1.1); controls.target.set(0,0,0); }
}
function setTool(t) {
  state.tool = t;
  for (const id of ['toolSelect','toolBuilding','toolMeasure','toolDelete'])
    $(id).classList.remove('active');
  $({ select:'toolSelect', building:'toolBuilding', measure:'toolMeasure', delete:'toolDelete' }[t]).classList.add('active');
  renderer.domElement.style.cursor = t === 'select' ? 'default' : 'crosshair';
}
function screenshot() {
  renderer.render(scene, camera);
  const a = document.createElement('a');
  a.download = 'terrain-' + Date.now() + '.png';
  a.href = renderer.domElement.toDataURL('image/png');
  a.click();
}

// ---------- backend analysis ----------
function apiBase() {
  const v = ($('apiUrl').value || '').trim().replace(/\/+$/, '');
  try { localStorage.setItem('lc_twin_api', v); } catch {}
  return v || 'http://127.0.0.1:8100';
}
let analyzeTimer = null;
function triggerAnalysis(reason) {
  // debounce: batch rapid edits
  clearTimeout(analyzeTimer);
  analyzeTimer = setTimeout(() => sendToBackend(reason), 1200);
}
async function sendToBackend(reason) {
  if (!state.center || !elevGrid || state.analyzing) return;
  state.analyzing = true;
  setStatus('🧠 Analyzing with AI model…', 'busy');
  const payload = {
    lat: state.center.lat, lon: state.center.lon,
    name: 'Twin 3D model @ ' + state.center.lat.toFixed(3) + ',' + state.center.lon.toFixed(3),
    date: new Date().toISOString().slice(0, 10),
    scene: {
      reason,
      size_km: state.sizeKm,
      elevation_min_m: +elevGrid.minElev.toFixed(1),
      elevation_max_m: +elevGrid.maxElev.toFixed(1),
      elevation_mean_m: +([...elevGrid.data].reduce((a,b)=>a+b,0)/elevGrid.data.length).toFixed(1),
      relief_m: +(elevGrid.maxElev - elevGrid.minElev).toFixed(1),
      building_count: state.buildings.length,
      total_building_volume_km3: +state.buildings.reduce((a,b) => a + b.scale.x*b.scale.z*b.scale.y, 0).toFixed(3),
    }
  };
  try {
    const res = await fetch(apiBase() + '/api/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('API HTTP ' + res.status);
    const data = await res.json();
    showAnalysis(data);
    setStatus('✅ Analysis complete', 'ok');
  } catch (err) {
    setStatus('⚠️ Model API unreachable — start backend/model_api.py or set API URL', 'busy');
    $('analysisBody').innerHTML = '<p class="note">Could not reach the model API at <b>' + escapeHtml(apiBase()) +
      '</b>: ' + escapeHtml(err.message) + '<br><br>Run <b>python backend/model_api.py</b> locally, or enter your deployed API URL above.</p>';
    $('analysisPanel').classList.add('show');
  } finally {
    state.analyzing = false;
  }
}
function showAnalysis(d) {
  const badgeColor = { 'Perfect':'#166534','Well managed':'#365314','Needs improvement':'#854d0e','Poor':'#9a3412','Harmful':'#991b1b' }[d.hwi?.label] || '#475569';
  let html = '<div style="text-align:center;margin-bottom:10px"><div style="font-size:34px;font-weight:800">' +
    d.hwi.score + '<span style="font-size:16px;color:var(--muted)">/100</span></div>' +
    '<span class="badge" style="background:' + badgeColor + ';color:#fff">' + escapeHtml(d.hwi.label) + '</span></div>';
  if (d.components) {
    html += '<h4 style="margin:10px 0 4px;font-size:11px;color:var(--muted)">COMPONENTS</h4>';
    for (const [k, c] of Object.entries(d.components)) {
      html += '<div class="metric"><span class="k">' + k.toUpperCase() + '</span><span class="v">' +
        c.predicted_value + ' ' + (c.unit || '') + '</span></div>';
    }
  }
  if (d.warnings?.length) {
    html += '<h4 style="margin:10px 0 4px;font-size:11px;color:var(--muted)">WARNINGS</h4>' +
      d.warnings.map(w => '<div class="metric"><span class="k">⚠️</span><span>' + escapeHtml(w) + '</span></div>').join('');
  }
  if (d.recommendations?.length) {
    html += '<h4 style="margin:10px 0 4px;font-size:11px;color:var(--muted)">RECOMMENDATIONS</h4>' +
      d.recommendations.slice(0,3).map(r => '<div class="metric"><span class="k">💡</span><span>' + escapeHtml(r) + '</span></div>').join('');
  }
  html += '<p class="note" style="margin-top:8px">Model ' + escapeHtml(d.model_version || '') +
    ' · analysis ' + escapeHtml(d.analysis_id || '') + '</p>';
  $('analysisBody').innerHTML = html;
  $('analysisPanel').classList.add('show');
}

// ---------- wire UI ----------
function wireUI() {
  $('btnSearch').onclick = async () => {
    const q = $('placeSearch').value.trim();
    if (!q) return;
    setStatus('Searching…', 'busy');
    try {
      const items = await searchPlace(q);
      showSearchResults(items);
      setStatus(items.length + ' result(s)', 'ok');
    } catch (e) { setStatus('❌ ' + e.message, 'busy'); }
  };
  $('placeSearch').addEventListener('keydown', e => { if (e.key === 'Enter') $('btnSearch').click(); });
  $('btnGenerate').onclick = generateTerrain;
  $('toolSelect').onclick = () => setTool('select');
  $('toolBuilding').onclick = () => setTool('building');
  $('toolMeasure').onclick = () => setTool('measure');
  $('toolDelete').onclick = () => setTool('delete');
  $('viewTop').onclick = () => { camera.position.set(0, state.sizeKm*2.2, 0.1); controls.target.set(0,0,0); };
  $('viewIso').onclick = () => { camera.position.set(state.sizeKm*0.7, state.sizeKm*0.8, state.sizeKm*1.1); controls.target.set(0,0,0); };
  $('btnAnalyze').onclick = () => triggerAnalysis('manual');
  $('btnShot').onclick = screenshot;
  $('vertExag').oninput = e => {
    state.vertExag = +e.target.value;
    if (state.terrain && elevGrid) {
      const pos = state.terrain.geometry.attributes.position;
      const { rows, cols, data } = elevGrid;
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++)
        pos.setY(r*cols+c, data[r*cols+c] * state.vertExag/100);
      pos.needsUpdate = true;
      state.terrain.geometry.computeVertexNormals();
      triggerAnalysis('exaggeration_changed');
    }
  };
  $('texMode').onchange = async e => {
    state.texMode = e.target.value;
    if (state.terrain && elevGrid) {
      setStatus('Switching texture…', 'busy');
      const tex = state.texMode === 'satellite' ? await buildSatelliteTexture(elevGrid) : null;
      await buildTerrainMesh(elevGrid, tex);
      setStatus('Ready', 'ok');
    }
  };
  try {
    const saved = localStorage.getItem('lc_twin_api');
    if (saved) $('apiUrl').value = saved;
  } catch {}
}

// ---------- main ----------
async function main() {
  initScene();
  wireUI();
  // auto-load default (Dhaka)
  $('loading').style.display = 'none';
  setStatus('Search a place or press Generate 3D', 'ok');
  // generate default terrain in background
  generateTerrain();
}
main();
