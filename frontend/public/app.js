
// Frontend script (English labels)
const API = '/api'; // proxied by nginx to backend; same origin, no CORS
let DEVICE = null;
let ports = [];
let VLANS = [];
let pollTimer = null;               // normal interval polling timer
let modalOpen = false;              // guard to suspend polling when modal is open
let repollActive = false;           // short re-poll state (after sync)
let repollHandle = null;            // last setTimeout handle for short re-poll
let currentPort = null;

const gridGe0 = document.getElementById('grid-ge0');
const gridGe1 = document.getElementById('grid-ge1');
const gridXe  = document.getElementById('grid-xe');
const gridAe  = document.getElementById('grid-ae');

const lastRefreshEl = document.getElementById('lastRefresh');

function makePort(info) {
  const el = document.createElement('div');
  el.className = `port ${info.type}`;
  const configured = info.configured === true;
  const adminUp = info.admin_up === true;
  const operUp  = info.oper_up === true;
  // Border for admin state
  if (!configured || info.admin_up === null || info.admin_up === undefined) el.classList.add('admin-unknown');
  else el.classList.add(adminUp ? 'admin-up' : 'admin-down');
  // Dot for oper state
  const dot = document.createElement('span');
  dot.className = 'dot';
  if (!configured || info.oper_up === null || info.oper_up === undefined) dot.classList.add('inactive');
  else dot.classList.add(operUp ? 'oper-up' : 'oper-down');
  // Labels: top name, bottom description or type
  const desc = info.description ? info.description : '';
  el.innerHTML = `<span class="label-top">${info.name}</span><span class="label-bottom">${desc}</span>`;
  el.appendChild(dot);
  el.addEventListener('click', async () => { currentPort = info; await openModal(info); });
  return el;
}

function render() {
  if (!gridGe0 || !gridGe1 || !gridXe || !gridAe) return;
  gridGe0.innerHTML = ''; gridGe1.innerHTML = ''; gridXe.innerHTML = ''; gridAe.innerHTML = '';
  // GE rows
  for (let p=0; p<48; p++) {
    const n0 = `ge-0/0/${p}`; const n1 = `ge-1/0/${p}`;
    const i0 = ports.find(x=>x.name===n0) || {name:n0, type:'ge', configured:false, admin_up:null, oper_up:null};
    const i1 = ports.find(x=>x.name===n1) || {name:n1, type:'ge', configured:false, admin_up:null, oper_up:null};
    gridGe0.appendChild(makePort(i0));
    gridGe1.appendChild(makePort(i1));
  }
  // XE uplinks
  for (let p=0; p<4; p++) {
    const n0 = `xe-0/2/${p}`; const n1 = `xe-1/2/${p}`;
    const i0 = ports.find(x=>x.name===n0) || {name:n0, type:'xe', configured:false, admin_up:null, oper_up:null};
    const i1 = ports.find(x=>x.name===n1) || {name:n1, type:'xe', configured:false, admin_up:null, oper_up:null};
    gridXe.appendChild(makePort(i0));
    gridXe.appendChild(makePort(i1));
  }
  // AE aggregates (sorted)
  ports.filter(x=>x.type==='ae').sort((a,b)=>a.name.localeCompare(b.name)).forEach(ae=>gridAe.appendChild(makePort(ae)));
}

async function fetchInterfaces() {
  try {
    const res = await fetch(`${API}/switches/${DEVICE}/interfaces`);
    if (!res.ok) return;
    ports = await res.json();
  } catch(e) { /* ignore */ }
}


async function syncInBackgroundAndRepoll() {
  try { await fetch(`${API}/sync/${DEVICE}`, { method:'POST' }); } catch(e) {}
  repollActive = true;
  const until = Date.now() + 10_000;

  async function repoll() {
    // stop if modal is open or time expired or canceled
    if (!repollActive || Date.now() > until) { repollActive = false; repollHandle = null; return; }
    if (!modalOpen) {
      await fetchInterfaces(); render(); await fetchLastRefresh();
    }
    repollHandle = setTimeout(repoll, 2000);
  }

  repoll();
}

function cancelRepoll() {
  repollActive = false;
  if (repollHandle) { clearTimeout(repollHandle); repollHandle = null; }
}

async function initInventory() {
  try { await fetch(`${API}/health`); } catch(e) { console.error('Backend down'); return; }
  const inv = await (await fetch(`${API}/inventory`)).json();
  const deviceSelect = document.getElementById('deviceSelect');
  deviceSelect.innerHTML = inv.map(d=>`<option value="${d.name}">${d.name}</option>`).join('');
  DEVICE = inv[0]?.name || null;
  deviceSelect.value = DEVICE;
  // Fetch VLANs up front
  VLANS = await (await fetch(`${API}/devices/${DEVICE}/vlans`)).json();
  // Initial fetch (may be empty on fresh start) -> render immediately
  await fetchInterfaces(); render(); await fetchLastRefresh();
  // Kick sync in background and repoll for ~10s
  await syncInBackgroundAndRepoll();
  // Start normal polling afterwards
  setupPolling();
}

function setupPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const interval = Math.max(5, parseInt(document.getElementById('pollInput').value,10) || 60) * 1000;
  pollTimer = setInterval(async ()=>{
    await fetchInterfaces(); render(); await fetchLastRefresh();
  }, interval);
}

function fillVlanSelect(selectEl, allowEmpty=false) {
  const opts = VLANS.map(v => `<option value="${v.name}">${v.name}${v.id ? ` (${v.id})` : ''}</option>`).join('');
  selectEl.innerHTML = (allowEmpty ? `<option value=""></option>` : '') + opts;
}

function setTrunkInline(selected) {
  const holder = document.getElementById('trunkSelected');
  holder.innerHTML = (selected || []).map(n => `<span class="inline-badge">${n}</span>`).join('');
}

async function openModal(info) {

  // Cancel all background polling while modal is open
  modalOpen = true;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  cancelRepoll();

  await updateModalLive(info.name);
  document.getElementById('modal').classList.remove('hidden');

}

function closeModal() {
  document.getElementById('modal').classList.add('hidden');
  modalOpen = false;

  // Resume normal polling after closing modal
  setupPolling();
}

// Close when clicking backdrop
document.getElementById('modal').addEventListener('click', (e) => {
  if (e.target && e.target.id === 'modal') closeModal();
});

// Close on Esc
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && modalOpen) closeModal();
});

async function updateModalLive(ifName) {
  try {
    const res = await fetch(`${API}/switches/${DEVICE}/interface/${encodeURIComponent(ifName)}/live`);
    if (!res.ok) { const txt = await res.text(); throw new Error(`Live failed: ${txt}`); }
    const live = await res.json();
    const modalTitle = document.getElementById('modalTitle');
    const adminBadge = document.getElementById('adminBadge');
    const modeSelect = document.getElementById('modeSelect');
    const accessSelect = document.getElementById('accessVlanSelect');
    const trunkSelect = document.getElementById('trunkVlanSelect');
    const nativeSelect = document.getElementById('nativeVlanSelect');
    const bundleHint = document.getElementById('bundleHint');

    modalTitle.textContent = `Port ${live.name}`;
    if (adminBadge) adminBadge.classList.toggle('hidden', live.admin_up !== false);

    fillVlanSelect(accessSelect, true);
    fillVlanSelect(nativeSelect, true);
    fillVlanSelect(trunkSelect, false);

    modeSelect.value = live.mode || 'access';
    if (live.mode === 'access') accessSelect.value = live.access_vlan || '';

    const trunkNames = live.trunk_vlans || [];
    [...trunkSelect.options].forEach(opt => { opt.selected = trunkNames.includes(opt.value); });
    setTrunkInline(trunkNames);

    nativeSelect.value = live.native_vlan || '';

    if (live.bundle) {
      const siblings = ports.filter(p => p.bundle === live.bundle && p.name !== live.name).map(p => p.name);
      bundleHint.classList.remove('hidden');
      bundleHint.textContent = `LACP bundle: ${live.bundle}${siblings.length ? ' | peers: ' + siblings.join(', ') : ''}`;
    } else {
      bundleHint.classList.add('hidden');
      bundleHint.textContent = '';
    }
    adjustVisibility();
  } catch(err) {
    console.error('updateModalLive error:', err);
    alert('Failed to load live data: ' + err.message);
  }
}

function adjustVisibility() {
  const isAccess = document.getElementById('modeSelect').value === 'access';
  document.querySelectorAll('.access-only').forEach(e=>e.style.display = isAccess ? 'grid' : 'none');
  document.querySelectorAll('.trunk-only').forEach(e=>e.style.display = !isAccess ? 'grid' : 'none');
}

async function fetchLastRefresh() {
  try {
    const res = await fetch(`/api/last-refresh/${DEVICE}`);
    if (!res.ok) return;
    const data = await res.json();
    lastRefreshEl.textContent = data.last_refresh ? new Date(data.last_refresh).toLocaleString() : '—';
  } catch(e) {
    lastRefreshEl.textContent = '—';
  }
}

// Commit (single-port; bulk removed)
document.getElementById('configForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const modeSelect = document.getElementById('modeSelect');
  const accessSelect = document.getElementById('accessVlanSelect');
  const trunkSelect = document.getElementById('trunkVlanSelect');
  const nativeSelect = document.getElementById('nativeVlanSelect');

  const mode = modeSelect.value;
  const access_vlan = accessSelect.value || undefined;   // VLAN name
  const trunk_vlans = [...trunkSelect.selectedOptions].map(o => o.value);
  const native_vlan = nativeSelect.value || undefined;

  const cfg = {
    name: currentPort.name,
    member: currentPort.member || 0,
    fpc: currentPort.fpc || 0,
    type: currentPort.type,
    port: currentPort.port || 0,
    mode,
    access_vlan,
    trunk_vlans: mode === 'trunk' ? trunk_vlans : undefined,
    native_vlan,
  };

  const payload = { device: DEVICE, user: 'niels', interfaces: [currentPort.name], config: cfg };
  try {
    const res = await fetch(`${API}/commit`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    if (!res.ok) { const txt = await res.text(); throw new Error(`Commit failed: ${txt}`); }
    const data = await res.json();
    if (data.ok) {
      document.getElementById('modal').classList.add('hidden');
      await fetchInterfaces(); render();
    } else {
      alert('Commit failed: ' + data.error);
    }
  } catch(err) {
    alert(err.message);
  }
});

// Toolbar

document.getElementById('syncBtn').addEventListener('click', async ()=>{
  await fetch(`/api/sync/${DEVICE}`, { method:'POST' });
  await fetchInterfaces(); render(); await fetchLastRefresh();
});

document.getElementById('deviceSelect').addEventListener('change', async (e)=>{ DEVICE = e.target.value; VLANS = await (await fetch(`${API}/devices/${DEVICE}/vlans`)).json(); await fetchInterfaces(); render(); });

document.getElementById('pollInput').addEventListener('change', setupPolling);

initInventory();

