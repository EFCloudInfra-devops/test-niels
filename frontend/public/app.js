
const API = 'http://localhost:8000/api';
let DEVICE = null;
const gridGe0 = document.getElementById('grid-ge0');
const gridGe1 = document.getElementById('grid-ge1');
const gridXe  = document.getElementById('grid-xe');
const panesEl = document.getElementById('panes');
const modal = document.getElementById('modal');
const closeModal = document.getElementById('closeModal');
const form = document.getElementById('configForm');
const modalTitle = document.getElementById('modalTitle');
const bulkMode = document.getElementById('bulkMode');
const deviceSelect = document.getElementById('deviceSelect');
const pollInput = document.getElementById('pollInput');
const vcRolesEl = document.getElementById('vcRoles');
let ports = []; let selectedPorts = []; let currentPort = null; let pollTimer = null;

async function initInventory() {
  try { await fetch(`${API}/health`); } catch(e) { console.error('Backend not reachable'); return; }
  const res = await fetch(`${API}/inventory`);
  const inv = await res.json();
  deviceSelect.innerHTML = inv.map(d=>`<option value="${d.name}">${d.name}</option>`).join('');
  DEVICE = inv[0]?.name || null;
  deviceSelect.value = DEVICE;
  await refreshMeta();
  await fetchInterfaces();
  setupPolling();
}

async function refreshMeta() {
  if (!DEVICE) return;
  const res = await fetch(`${API}/devices/${DEVICE}`);
  const d = await res.json();
  const roles = d.roles || [];
  vcRolesEl.textContent = `VC Roles: ${roles.map((r,i)=>`lid ${i}: ${r}`).join(' | ')}`;
}

function groupByMember(items) { const by = {0: [], 1: []}; for (const it of items) { if (it.member in by) by[it.member].push(it); } return by; }

function render() {
  gridGe0.innerHTML = '';
  gridGe1.innerHTML = '';
  gridXe.innerHTML  = '';
  panesEl.innerHTML = '';
  const byMember = groupByMember(ports);
  for (const member of [0,1]) {
    const pane = document.createElement('div'); pane.className = 'pane'; pane.innerHTML = `<h2>VC-lid ${member}</h2>`;
    const grid = document.createElement('div'); grid.className = 'grid';
    for (let p=0; p<48; p++) { const name = `ge-${member}/0/${p}`; const info = ports.find(x => x.name === name) || {name, member, fpc:0, type:'ge', port:p, oper_up:false}; grid.appendChild(renderPort(info)); }
    for (let p=0; p<4; p++) { const name = `xe-${member}/2/${p}`; const info = ports.find(x => x.name === name) || {name, member, fpc:2, type:'xe', port:p, oper_up:false}; grid.appendChild(renderPort(info)); }
    pane.appendChild(grid); panesEl.appendChild(pane);
    
    // RJ-45 per lid
    for (let p=0; p<48; p++) {
      const n0 = `ge-0/0/${p}`;
      const n1 = `ge-1/0/${p}`;
      const i0 = ports.find(x=>x.name===n0) || {name:n0, member:0, fpc:0, type:'ge', port:p, oper_up:false};
      const i1 = ports.find(x=>x.name===n1) || {name:n1, member:1, fpc:0, type:'ge', port:p, oper_up:false};
      gridGe0.appendChild(makePort(i0));
      gridGe1.appendChild(makePort(i1));
    }

    // SFP+ per lid (twee vakjes naast elkaar per p)
    for (let p=0; p<4; p++) {
      const n0 = `xe-0/2/${p}`;
      const n1 = `xe-1/2/${p}`;
      const i0 = ports.find(x=>x.name===n0) || {name:n0, member:0, fpc:2, type:'xe', port:p, oper_up:false};
      const i1 = ports.find(x=>x.name===n1) || {name:n1, member:1, fpc:2, type:'xe', port:p, oper_up:false};
      gridXe.appendChild(makePort(i0));
      gridXe.appendChild(makePort(i1));
    }
  }
}

function renderPort(info) {
  const el = document.createElement('div'); el.className = `port ${info.type} ${info.oper_up ? 'up' : 'down'}`; el.dataset.name = info.name; el.dataset.type = info.type;
  el.innerHTML = `<span class='label'>${info.name}</span><span class='dot ${info.poe ? 'poe' : ''}'></span>`;
  el.addEventListener('click', () => { if (bulkMode.checked) { if (selectedPorts.includes(info.name)) { selectedPorts = selectedPorts.filter(x => x !== info.name); el.classList.remove('bulk-selected'); } else { selectedPorts.push(info.name); el.classList.add('bulk-selected'); } return; } currentPort = info; openModal(info); });
  return el;
}

async function openModal(info) { 
  // haal live config + oper van backend 
  const live = await (await fetch(`${API}/switches/${DEVICE}/interface/${encodeURIComponent(info.name)}/live`)).json(); 
  const merged = {...info, ...live};
  modalTitle.textContent = `Config ${merged.name}`;
  form.mode.value = merged.mode || 'access';
  form.access_vlan.value = merged.access_vlan || '';
  form.trunk_vlans.value = (merged.trunk_vlans || []).join(',');
  form.native_vlan.value = merged.native_vlan || '';
  form.poe.value = (merged.poe === undefined ? '' : merged.poe ? 'true' : 'false');
  form.speed.value = merged.speed || '';
  form.duplex.value = merged.duplex || '';
  adjustVisibility();
  modal.classList.remove('hidden');
}

modal.addEventListener('click', (e) => {
  if (e.target === modal) closeModal();
});
window.addEventListener('keydown', (e)=>{
  if (e.key === 'Escape' && !modal.classList.contains('hidden')) closeModal();
});
function closeModal() { modal.classList.add('hidden'); }

closeModal.addEventListener('click', () => modal.classList.add('hidden'));
form.mode.addEventListener('change', adjustVisibility);
function adjustVisibility() { const isAccess = form.mode.value === 'access'; document.querySelectorAll('.access-only').forEach(e=>e.style.display = isAccess ? 'grid' : 'none'); document.querySelectorAll('.trunk-only').forEach(e=>e.style.display = !isAccess ? 'grid' : 'none'); }

form.addEventListener('submit', async (e) => {
  e.preventDefault(); const cfg = collectConfig(currentPort); const ok = await validate(cfg); if (!ok) return;
  const interfaces = bulkMode.checked && selectedPorts.length ? selectedPorts : [currentPort.name];
  const payload = { device: DEVICE, user: 'niels', interfaces, config: cfg };
  const res = await fetch(`${API}/commit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (!res.ok) { const err = await res.json(); alert('Fout: ' + JSON.stringify(err)); return; }
  const data = await res.json(); if (data.ok) { modal.classList.add('hidden'); selectedPorts = []; await fetchInterfaces(); } else { alert('Commit mislukt: ' + data.error); }
});

async function validate(cfg) { const res = await fetch(`${API}/validate`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)}); const data = await res.json(); if (!data.ok) { alert('Validatiefouten: ' + data.errors.join('; ')); } return data.ok; }

function collectConfig(info) {
  const mode = form.mode.value; const access_vlan = form.access_vlan.value ? parseInt(form.access_vlan.value,10) : undefined; const trunk_vlans = form.trunk_vlans.value ? form.trunk_vlans.value.split(',').map(x=>parseInt(x.trim(),10)).filter(Boolean) : undefined; const native_vlan = form.native_vlan.value ? parseInt(form.native_vlan.value,10) : undefined; const poeVal = form.poe.value; const poe = poeVal === '' ? undefined : (poeVal === 'true'); const speed = form.speed.value || undefined; const duplex = form.duplex.value || undefined; return { name: info.name, member: info.member, fpc: info.fpc, type: info.type, port: info.port, mode, access_vlan, trunk_vlans, native_vlan, poe, speed, duplex };
}

deviceSelect.addEventListener('change', async () => { DEVICE = deviceSelect.value; await refreshMeta(); await fetchInterfaces(); });

function setupPolling() { if (pollTimer) clearInterval(pollTimer); const interval = Math.max(5, parseInt(pollInput.value,10) || 15) * 1000; pollTimer = setInterval(async () => { await fetchInterfaces(); }, interval); }

pollInput.addEventListener('change', setupPolling);

async function fetchInterfaces() { if (!DEVICE) return; const res = await fetch(`${API}/switches/${DEVICE}/interfaces`); ports = await res.json(); render(); }

document.getElementById('syncBtn').addEventListener('click', async ()=>{ if (!DEVICE) return; await fetch(`${API}/sync/${DEVICE}`, { method:'POST' }); await refreshMeta(); await fetchInterfaces(); });

initInventory();
