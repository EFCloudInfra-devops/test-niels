const API = 'http://localhost:8000/api';
let DEVICE = null;
const gridGe0 = document.getElementById('grid-ge0');
const gridGe1 = document.getElementById('grid-ge1');
const gridXe  = document.getElementById('grid-xe');
const deviceSelect = document.getElementById('deviceSelect');
const pollInput = document.getElementById('pollInput');
const vcRolesEl = document.getElementById('vcRoles');
const bulkMode = document.getElementById('bulkMode');

const modal = document.getElementById('modal');
const form = document.getElementById('configForm');
const modalTitle = document.getElementById('modalTitle');
const adminBadge = document.getElementById('adminBadge');
let currentPort = null;
let selectedPorts = [];
let ports = [];
let pollTimer = null;
let modalPollTimer = null;

function makePort(info) {
  const el = document.createElement('div');
  el.className = `port ${info.type} ${info.oper_up ? 'up' : 'down'}`;
  el.dataset.name = info.name;
  el.dataset.type = info.type;
  el.innerHTML = `<span class='label'>${info.name}</span><span class='dot ${info.poe ? 'poe' : ''}'></span>`;
  el.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (bulkMode.checked) {
      if (selectedPorts.includes(info.name)) {
        selectedPorts = selectedPorts.filter(x => x !== info.name);
        el.classList.remove('bulk-selected');
      } else {
        selectedPorts.push(info.name);
        el.classList.add('bulk-selected');
      }
      return;
    }
    currentPort = info;
    await openModal(info);
  });
  return el;
}

function render() {
  gridGe0.innerHTML = '';
  gridGe1.innerHTML = '';
  gridXe.innerHTML  = '';
  for (let p=0; p<48; p++) {
    const n0 = `ge-0/0/${p}`;
    const n1 = `ge-1/0/${p}`;
    const i0 = ports.find(x=>x.name===n0) || {name:n0, member:0, fpc:0, type:'ge', port:p, oper_up:false, admin_up:true};
    const i1 = ports.find(x=>x.name===n1) || {name:n1, member:1, fpc:0, type:'ge', port:p, oper_up:false, admin_up:true};
    gridGe0.appendChild(makePort(i0));
    gridGe1.appendChild(makePort(i1));
  }
  for (let p=0; p<4; p++) {
    const n0 = `xe-0/2/${p}`;
    const n1 = `xe-1/2/${p}`;
    const i0 = ports.find(x=>x.name===n0) || {name:n0, member:0, fpc:2, type:'xe', port:p, oper_up:false, admin_up:true};
    const i1 = ports.find(x=>x.name===n1) || {name:n1, member:1, fpc:2, type:'xe', port:p, oper_up:false, admin_up:true};
    gridXe.appendChild(makePort(i0));
    gridXe.appendChild(makePort(i1));
  }
}

async function initInventory() {
  try { await fetch(`${API}/health`); } catch(e) { console.error('Backend down'); return; }
  const inv = await (await fetch(`${API}/inventory`)).json();
  deviceSelect.innerHTML = inv.map(d=>`<option value="${d.name}">${d.name}</option>`).join('');
  DEVICE = inv[0]?.name || null;
  deviceSelect.value = DEVICE;
  await refreshMeta();
  await fetchInterfaces();
  setupPolling();
}

async function refreshMeta() {
  if (!DEVICE) return;
  const d = await (await fetch(`${API}/devices/${DEVICE}`)).json();
  const roles = d.roles || [];
  vcRolesEl.textContent = `VC Roles: ${roles.map((r,i)=>`lid ${i}: ${r}`).join(' | ')}`;
}

async function fetchInterfaces() {
  if (!DEVICE) return;
  ports = await (await fetch(`${API}/switches/${DEVICE}/interfaces`)).json();
  render();
}

function setupPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const interval = Math.max(5, parseInt(pollInput.value,10) || 15) * 1000;
  pollTimer = setInterval(fetchInterfaces, interval);
}

modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
window.addEventListener('keydown', (e)=>{ if (e.key === 'Escape' && !modal.classList.contains('hidden')) closeModal(); });

function closeModal() {
  modal.classList.add('hidden');
  if (modalPollTimer) { clearInterval(modalPollTimer); modalPollTimer = null; }
}

async function openModal(info) {
  await updateModalLive(info.name);
  if (modalPollTimer) clearInterval(modalPollTimer);
  modalPollTimer = setInterval(async () => { await updateModalLive(info.name); }, 10_000);
  modal.classList.remove('hidden');
}

async function updateModalLive(ifName) {
  const live = await (await fetch(`${API}/switches/${DEVICE}/interface/${encodeURIComponent(ifName)}/live`)).json();
  const merged = { ...live };
  modalTitle.textContent = `Config ${merged.name}`;
  adminBadge.classList.toggle('hidden', merged.admin_up !== false);
  form.mode.value = merged.mode || 'access';
  form.access_vlan.value = merged.access_vlan || '';
  form.trunk_vlans.value = (merged.trunk_vlans || []).join(',');
  form.native_vlan.value = merged.native_vlan || '';
  form.poe.value = (merged.poe === undefined ? '' : merged.poe ? 'true' : 'false');
  form.speed.value = merged.speed || '';
  form.duplex.value = merged.duplex || '';
  adjustVisibility();
}

function adjustVisibility() {
  const isAccess = form.mode.value === 'access';
  document.querySelectorAll('.access-only').forEach(e=>e.style.display = isAccess ? 'grid' : 'none');
  document.querySelectorAll('.trunk-only').forEach(e=>e.style.display = !isAccess ? 'grid' : 'none');
}
form.mode.addEventListener('change', adjustVisibility);

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const info = currentPort;
  const cfg = collectConfig(info);
  const ok = await validate(cfg);
  if (!ok) return;
  const interfaces = bulkMode.checked && selectedPorts.length ? selectedPorts : [info.name];
  const payload = { device: DEVICE, user: 'niels', interfaces, config: cfg };
  const res = await fetch(`${API}/commit`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
  if (!res.ok) { const err = await res.json(); alert('Fout: ' + JSON.stringify(err)); return; }
  const data = await res.json();
  if (data.ok) { closeModal(); selectedPorts = []; await fetchInterfaces(); }
  else { alert('Commit mislukt: ' + data.error); }
});

async function validate(cfg) {
  const data = await (await fetch(`${API}/validate`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)})).json();
  if (!data.ok) alert('Validatiefouten: ' + data.errors.join('; '));
  return data.ok;
}

function collectConfig(info) {
  const mode = form.mode.value;
  const access_vlan = form.access_vlan.value ? parseInt(form.access_vlan.value,10) : undefined;
  const trunk_vlans = form.trunk_vlans.value ? form.trunk_vlans.value.split(',').map(x=>parseInt(x.trim(),10)).filter(Boolean) : undefined;
  const native_vlan = form.native_vlan.value ? parseInt(form.native_vlan.value,10) : undefined;
  const poeVal = form.poe.value; const poe = poeVal === '' ? undefined : (poeVal === 'true');
  const speed = form.speed.value || undefined; const duplex = form.duplex.value || undefined;
  return { name: info.name, member: info.member, fpc: info.fpc, type: info.type, port: info.port, mode, access_vlan, trunk_vlans, native_vlan, poe, speed, duplex };
}

document.getElementById('syncBtn').addEventListener('click', async ()=>{ if (!DEVICE) return; await fetch(`${API}/sync/${DEVICE}`, { method:'POST' }); await refreshMeta(); await fetchInterfaces(); });
deviceSelect.addEventListener('change', async ()=>{ DEVICE = deviceSelect.value; await refreshMeta(); await fetchInterfaces(); });
pollInput.addEventListener('change', setupPolling);
initInventory();
