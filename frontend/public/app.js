
const API = 'http://localhost:8000/api';
let DEVICE = null;
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
  panesEl.innerHTML = '';
  const byMember = groupByMember(ports);
  for (const member of [0,1]) {
    const pane = document.createElement('div'); pane.className = 'pane'; pane.innerHTML = `<h2>VC-lid ${member}</h2>`;
    const grid = document.createElement('div'); grid.className = 'grid';
    for (let p=0; p<48; p++) { const name = `ge-${member}/0/${p}`; const info = ports.find(x => x.name === name) || {name, member, fpc:0, type:'ge', port:p, oper_up:false}; grid.appendChild(renderPort(info)); }
    for (let p=0; p<4; p++) { const name = `xe-${member}/2/${p}`; const info = ports.find(x => x.name === name) || {name, member, fpc:2, type:'xe', port:p, oper_up:false}; grid.appendChild(renderPort(info)); }
    pane.appendChild(grid); panesEl.appendChild(pane);
  }
}

function renderPort(info) {
  const el = document.createElement('div'); el.className = `port ${info.type} ${info.oper_up ? 'up' : 'down'}`; el.dataset.name = info.name; el.dataset.type = info.type;
  el.innerHTML = `<span class='label'>${info.name}</span><span class='dot ${info.poe ? 'poe' : ''}'></span>`;
  el.addEventListener('click', () => { if (bulkMode.checked) { if (selectedPorts.includes(info.name)) { selectedPorts = selectedPorts.filter(x => x !== info.name); el.classList.remove('bulk-selected'); } else { selectedPorts.push(info.name); el.classList.add('bulk-selected'); } return; } currentPort = info; openModal(info); });
  return el;
}

function openModal(info) {
  modalTitle.textContent = `Config ${info.name}`;
  form.mode.value = info.mode || 'access';
  form.access_vlan.value = info.access_vlan || '';
  form.trunk_vlans.value = (info.trunk_vlans || []).join(',');
  form.native_vlan.value = info.native_vlan || '';
  form.poe.value = (info.poe === undefined ? '' : info.poe ? 'true' : 'false');
  form.speed.value = info.speed || '';
  form.duplex.value = info.duplex || '';
  adjustVisibility(); modal.classList.remove('hidden');
}

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
