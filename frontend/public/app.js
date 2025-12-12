// frontend/public/app.js
import {
  drawPorts,
  fetchInterfaceLiveClient,
  highlightVlan,
  clearVlanHighlight,
} from "./renderer.js";

const POLL_INTERVAL = 30000;
let pollTimer = null;
let currentSwitch = window.currentSwitch = null;
let pendingByInterface = {};
let CURRENT_SWITCH_PORTS = null;
let CURRENT_DEVICE = null;
let CURRENT_PORT = null;
let CURRENT_SWITCH_RENDER_STATE = null;
let auditDT = null;

function authHeaders() {
  return {
    "Content-Type": "application/json",
    "X-User": "admin",
    "X-Role": "admin"
  };
}

function parseIfParts(name) {
  // ge-0/0/12 or xe-1/2/3 or ae1
  if (!name) return {};
  const m = name.match(/^([a-z]+)-(\d+)\/\d+\/(\d+)$/);
  if (m) {
    return { type: m[1], member: Number(m[2]), port: Number(m[3]) };
  }
  const ae = name.match(/^ae(\d+)$/);
  if (ae) return { type: 'ae', ae: Number(ae[1]), member: 0, port: -1 };
  return { type: name, member: 0, port: -1 };
}

function portCompare(a, b) {
  const A = parseIfParts(a.name);
  const B = parseIfParts(b.name);

  // first by type: ge < xe < ae (so physical left-to-right then AE at bottom)
  const order = { ge: 0, xe: 1, ae: 2 };
  const ta = order[A.type] ?? 9;
  const tb = order[B.type] ?? 9;
  if (ta !== tb) return ta - tb;

  // then by member
  if ((A.member || 0) !== (B.member || 0)) return (A.member || 0) - (B.member || 0);

  // then by numeric port
  if ((A.port || 0) !== (B.port || 0)) return (A.port || 0) - (B.port || 0);

  // fallback to string compare
  return (a.name || "").localeCompare(b.name || "");
}

function showApplySpinner() {
  document.getElementById("apply-overlay").classList.remove("hidden");
}

function hideApplySpinner() {
  document.getElementById("apply-overlay").classList.add("hidden");
}

function renderInitialVC() {
  const ports = [
    ...allPhysicalPortsVC(2),
    ...allUplinkPortsVC(2),
    ...allAggregatePorts(8)
  ];

  drawPorts(ports, "__skeleton__");
}

function showView(name) {
  ["switch", "approvals", "audit", "rollback"].forEach(v => {
    const el = document.getElementById(`view-${v}`);
    if (el) el.classList.toggle("hidden", v !== name);

    const tab = document.getElementById(`tab-${v}`);
    if (tab) tab.classList.toggle("active", v === name);
  });

  if (name === "approvals") {
    loadApprovals();
  }

  if (name === "audit") {
    load_audit();
  }

  if (name === "switch") {
    loadPending();
    if (CURRENT_DEVICE && CURRENT_SWITCH_PORTS) {
      drawPorts(CURRENT_SWITCH_PORTS, CURRENT_DEVICE);
    }
  }
  
  if (name === "rollback") {
    loadRollbackDevices();
  }
}

// small helper to set text safely
function setText(id, txt) {
  const el = document.getElementById(id);
  if (el) el.textContent = txt;
}

async function loadSwitches() {
  try {
    const res = await fetch("/api/switches");
    const list = await res.json();

    const sel = document.getElementById("deviceSelect");
    if (!sel) return;

    sel.innerHTML = `<option value="">-- select switch --</option>`;

    list.forEach(s => {
      const o = document.createElement("option");
      o.value = s.name;
      o.textContent = s.name;
      sel.appendChild(o);
    });

    // ✅ expliciet geen selectie
    sel.value = "";
    currentSwitch = null;
    window.currentSwitch = null;
    setSwitchButtons(false); // bij load

    sel.onchange = async () => {
      const sw = sel.value;
      if (!sw) return;
    
      currentSwitch = sw;
      window.currentSwitch = sw; // ✅ BELANGRIJK
      localStorage.setItem("lastSwitch", sw);
    
      // ✅ ALTIJD eerst knoppen activeren
      setSwitchButtons(true);
    
      try {
        await loadPending();
        await loadVlanList();
        await reloadAllPorts(false);
      } catch (e) {
        console.error("switch load failed", e);
      }
    };    

    // ✅ restore laatst gekozen switch
    const last = localStorage.getItem("lastSwitch");
    if (last && [...sel.options].some(o => o.value === last)) {
      sel.value = last;
      sel.dispatchEvent(new Event("change"));
    }

  } catch (e) {
    console.error("loadSwitches failed", e);
  }
}


// load VLAN list from backend for the current switch
let _vlans_cache = [];
async function loadVlanList() {
  _vlans_cache = [];
  if (!currentSwitch) return;
  try {
    const r = await fetch(`/api/switches/${currentSwitch}/vlans`);
    if (!r.ok) return;

    const raw = await r.json();
    _vlans_cache =
      Array.isArray(raw) ? raw :
      Array.isArray(raw.vlans) ? raw.vlans :
      Array.isArray(raw.data)  ? raw.data  :
      [];

    // if (Array.isArray(raw)) {
    //   _vlans_cache = raw;
    // } else if (raw?.data && typeof raw.data === "object") {
    //   _vlans_cache = Object.entries(raw.data).map(([name, v]) => ({
    //     name,
    //     id: v.vlan_id ?? v.id ?? null
    //   }));
    // } else {
    //   _vlans_cache = [];
    // }

    const vlanSel = document.getElementById("vlan-select");
    if (vlanSel) {
      vlanSel.innerHTML = `<option value="">-- highlight VLAN --</option>`;
      _vlans_cache.forEach(v => {
        const o = document.createElement("option");
        o.value = v.name;
        o.textContent = `${v.name} (${v.id ?? ""})`;
        vlanSel.appendChild(o);
      });
      vlanSel.addEventListener("change", (e) => {
        const v = e.target.value;
        clearVlanHighlight();
        if (v) highlightVlan(v);
      });
    }
  } catch (e) {
    console.error("loadVlanList", e);
  }
}

async function reloadAllPorts(live = false, forcedSwitch = null) {
  const sw = forcedSwitch || currentSwitch;
  if (!sw) {
    console.warn("reloadAllPorts called without switch");
    return;
  }

  const url = live
    ? `/api/switches/${sw}/interfaces/retrieve`
    : `/api/switches/${sw}/interfaces`;

  const r = await fetch(url, { method: live ? "POST" : "GET" });
  if (!r.ok) return;

  const data = await r.json();
  mergeAndRedrawPorts(sw, data);
}


function enrichPorts(ports, source, ts) {
  return ports.map(p => ({
    ...p,
    _source: source,
    _retrieved_at: ts,
    pending: pendingByInterface[`${currentSwitch}|${p.name}`] != null
  }));
}

// --- modal logic: now allows opening unconfigured port for configuration
async function openModalForPort(port) {
  CURRENT_PORT = port;
  CURRENT_DEVICE = currentSwitch;

  clearModalDiff();

  // We allow opening modal for unconfigured ports too.
  // But we will not attempt to fetch 'live' info for them (unless configured).
  if (!currentSwitch) return;
  
  // populate initial form values
  const modal = document.getElementById("modal");
  if (!modal) return;
  
  const diffBox = document.getElementById("diff-box");
  if (diffBox) diffBox.innerHTML = "";
  
  const approveBtn = document.getElementById("approve-btn");
  if (approveBtn) approveBtn.disabled = true;
  
  // eventueel statussen resetten
  document.querySelectorAll(".diff-added, .diff-removed")
    .forEach(el => el.classList.remove("diff-added", "diff-removed"));
  setText("modalTitle", `Port ${port.name}`);
  const descr = document.getElementById("ifDescr");
  const modeSel = document.getElementById("modeSelect");
  const accessSel = document.getElementById("accessVlanSelect");
  const trunkSel = document.getElementById("trunkVlanSelect");
  const nativeInput = document.getElementById("nativeVlan");
  const submitBtn = document.getElementById("submitBtn");
  const isPhysical = port.type === "ge" || port.type === "xe";

  // --- reset portInfo altijd ---
  const portInfo = document.getElementById("portInfo");
  if (portInfo) portInfo.innerHTML = "";
  if (port.vc_port) {
    portInfo.innerHTML += `
      <div class="info-block vc-port">
        <strong>Virtual Chassis</strong>
        <div class="badge badge-vc">
          VC port${port.vc_role ? ` (${port.vc_role})` : ""}
        </div>
      </div>
    `;
  }
  if (descr) descr.value = port.description || "";
  if (nativeInput) nativeInput.value = port.native_vlan || "";

  const vlans = Array.isArray(_vlans_cache) ? _vlans_cache : [];
  if (accessSel) {
    accessSel.innerHTML = `<option value="">-- select VLAN --</option>`;
    vlans.forEach(v => {
      const o = document.createElement("option");
      o.value = v.name;
      o.textContent = `${v.name} (${v.id ?? ""})`;
      accessSel.appendChild(o);
    });
  }
  if (trunkSel) {
    trunkSel.innerHTML = "";
    vlans.forEach(v => {
      const o = document.createElement("option");
      o.value = v.name;
      o.textContent = `${v.name} (${v.id ?? ""})`;
      trunkSel.appendChild(o);
    });
    trunkSel.multiple = true;
  }

  // set initial UI depending on whether port is configured
  if (modeSel) modeSel.value = port.mode || "access";
  if (accessSel && port.access_vlan) accessSel.value = port.access_vlan;
  if (trunkSel && Array.isArray(port.trunk_vlans)) {
    const vals = port.trunk_vlans;
    Array.from(trunkSel.options).forEach(opt => {
      opt.selected = vals.includes(opt.value);
    });
  }

  // If port configured & up -> try to fetch live info and show speed etc
  if (isPhysical && port.configured && port.oper_up) {
    try {
      const live = await fetchInterfaceLiveClient(currentSwitch, port.name, port);
      if (live) {
        if (descr && live.description !== undefined) {
          descr.value = live.description;
        }
        if (modeSel) modeSel.value = live.mode || modeSel.value;
        if (accessSel && live.access_vlan) accessSel.value = live.access_vlan;
        if (trunkSel && live.trunk_vlans) {
          Array.from(trunkSel.options).forEach(opt => {
            opt.selected = (live.trunk_vlans || []).includes(opt.value);
          });
        }
        if (nativeInput && live.native_vlan) nativeInput.value = live.native_vlan;
      }
    } catch (e) { /* ignore */ }
  }

  if (port.type === "ae") {
    const members = [...document.querySelectorAll(
      `[data-bundle="${port.name}"]`
    )].map(el => el.dataset.ifname);
  
    portInfo.innerHTML += `
      <div class="info-block">
        <strong>LACP members</strong>
        <ul>
          ${members.length
            ? members.map(m => `<li>${m}</li>`).join("")
            : "<li>None</li>"
          }
        </ul>
      </div>
    `;
  } 

  // UI: show/hide selects based on mode
  const fieldAccess = document.getElementById("field-access");
  const fieldTrunk  = document.getElementById("field-trunk");
  
  const toggleModeFields = () => {
    const m = modeSel?.value || "access";
  
    if (fieldAccess)
      fieldAccess.style.display = (m === "access") ? "" : "none";
  
    if (fieldTrunk)
      fieldTrunk.style.display = (m === "trunk") ? "" : "none";
  };
  
  modeSel?.addEventListener("change", toggleModeFields);
  toggleModeFields();

  // show modal
  modal.classList.remove("hidden");
  
  // attach submit handler (one-time)
  const form = document.getElementById("configForm");
  if (!form) return;
    
  function collectFormConfig(port) {
    const mode = modeSelect.value;
  
    return {
      name: port.name,
      description: ifDescr.value || "",
      mode,
  
      access_vlan:
        mode === "access" ? accessVlanSelect.value || null : port.access_vlan,
  
      trunk_vlans:
        mode === "trunk"
          ? Array.from(trunkVlanSelect.selectedOptions).map(o => o.value)
          : port.trunk_vlans || [],
  
      native_vlan:
        mode === "trunk" ? nativeVlan.value || null : port.native_vlan || null
    };
  }
  
  
  form.addEventListener("input", () => {
    const cfg = collectFormConfig(port);
    const diffs = diffObject(port, cfg);
  
    renderDiff(port, cfg);
    submitBtn.disabled = diffs.length === 0;
    if (port.vc_port && submitBtn) {
      submitBtn.disabled = true;
    }
  });
  
  const onSubmit = async (e) => {
    e.preventDefault();
    // build config object
    const cfg = {
      name: port.name,
      description: descr ? descr.value : "",
      mode: modeSel ? modeSel.value : "access",
      access_vlan: (modeSel && modeSel.value === "access" && accessSel) ? accessSel.value : null,
      trunk_vlans: (modeSel && modeSel.value === "trunk" && trunkSel) ? Array.from(trunkSel.selectedOptions).map(o => o.value) : [],
      native_vlan: (modeSel && modeSel.value === "trunk" && nativeInput) ? nativeInput.value || null : null
    };

    // create change request payload
    const payload = {
      device: currentSwitch,
      interface: cfg.name,
      config: cfg,
      requester: window?.USER?.username || "ui",
      created_at: new Date().toISOString(),
      status: "pending"
    };

    try {
      const r = await fetch("/api/requests", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      if (!r.ok) {
        alert("Failed to create change request");
      } else {
        // alert("Change request created (pending approval)");
        // optionally close modal and fast-repoll to show candidate state if you want
        modal.classList.add("hidden");
      }
    } catch (err) {
      console.error("request create failed", err);
      alert("Failed to create change request (network)");
    } finally {
      form.removeEventListener("submit", onSubmit);
    }
  };
  form.onsubmit = e => {
    e.preventDefault();
    onSubmit(e);
  };

  // close button handling
  const closeBtn = document.getElementById("closeModalBtn");
  if (closeBtn) {
    const close = () => {
      modal.classList.add("hidden");
      form.removeEventListener("submit", onSubmit);
    };
    closeBtn.onclick = close;
  }
}

window.openModalForPort = openModalForPort;

document.getElementById("deleteInterfaceBtn").onclick = async function () {
  if (!CURRENT_PORT || !CURRENT_DEVICE) {
    alert("No active port.");
    return;
  }

  const ifname = CURRENT_PORT.name;
  const device = CURRENT_DEVICE;

  if (!confirm(`Request delete of interface ${ifname} on ${device}?`)) return;

  showApplySpinner();

  try {
    const payload = {
      device,
      interface: ifname,
      comment: "Requested via UI"
    };

    const r = await fetch("/api/requests/delete", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User": "admin",
        "X-Role": "admin"
      },
      body: JSON.stringify(payload)
    });

    const json = await r.json().catch(() => null);

    hideApplySpinner();

    if (!r.ok) {
      alert("Delete request failed:\n" + (json?.detail || "Unknown"));
      return;
    }

    closeModal();
    clearModalDiff();
    loadApprovals();
    load_audit();
    alert("Delete request created (pending approval).");

  } catch (e) {
    hideApplySpinner();
    alert("Delete error: " + e);
  }
};

function closeAllModals() {
  // Config modal
  clearModalDiff();
  const modal = document.getElementById("modal");
  const portInfo = document.getElementById("portInfo");
  if (modal) modal.classList.add("hidden");
  if (portInfo) portInfo.innerHTML = "";
  const form = document.getElementById("configForm");
  if (form) form.reset();

  // Audit detail modal
  const audit = document.getElementById("auditDetailModal");
  const auditContent = document.getElementById("auditDetailContent");
  if (audit) audit.classList.add("hidden");
  if (auditContent) auditContent.innerHTML = "";
}

window.closeAllModals = closeAllModals;

function closeModal() {
  closeAllModals();
}

window.closeModal = closeModal;

document.addEventListener("click", (e) => {
  if (
    e.target.id === "closeModalBtn" ||
    e.target.id === "auditDetailClose" ||
    e.target.classList.contains("modal-close") ||
    e.target.id === "modal" ||
    e.target.id === "auditDetailModal"
  ) {
    closeAllModals();
  }
});

// document.getElementById("modal").addEventListener("click", (e) => {
//   if (e.target.id === "modal") {
//     closeModal();
//   }
// });

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAllModals();
});

// bootstrap
document.addEventListener("DOMContentLoaded", () => {

  document.getElementById("tab-switch").onclick =
    () => showView("switch");

  document.getElementById("tab-approvals").onclick =
    () => showView("approvals");
    
  document.getElementById("tab-audit").onclick = () => {
    initAuditTable();
    showView("audit");
  };
  document.getElementById("tab-rollback").onclick = () => {
    showView("rollback");
    initRollbackTab();
  };

  // ✅ audit filter hooks (bestaan alleen in approvals view)
  const auditDevice = document.getElementById("audit-device");
  const auditIface  = document.getElementById("audit-interface");

  if (auditDevice) auditDevice.onchange  = applyAuditFilters;
  if (auditIface)  auditIface.oninput    = debounce(applyAuditFilters, 200);
  

  renderInitialVC();
  loadSwitches();
  loadPending();
  loadAuditDevices();
  showView("switch");
});

// -----------------------------
// Approvals UI
// -----------------------------
async function loadApprovals() {
  const list = document.getElementById("approvalsList");
  const empty = document.getElementById("no-approvals");
  const count = document.getElementById("approvals-count");

  list.innerHTML = "Loading…";

  const r = await fetch("/api/requests?status=pending", {
    headers: { "X-Role": "approver" }
  });

  if (!r.ok) {
    list.textContent = "Failed to load approvals";
    return;
  }

  const items = await r.json();
  list.innerHTML = "";

  count.textContent = items.length;

  if (items.length === 0) {
    empty.classList.remove("hidden");
    return;
  }

  empty.classList.add("hidden");

  items.forEach(req => {
    const card = document.createElement("div");
    card.className = "approval-card";
  
    // ===== header =====
    const meta = document.createElement("div");
    meta.className = "approval-meta";
    meta.innerHTML = `
      <div>
        <strong>${req.device}</strong>
        <span class="dim">${req.interface}</span>
      </div>
      <div class="approval-tags">
        <span class="tag pending">PENDING</span>
        <span class="tag user">${req.requester}</span>
        <span class="tag time">
          ${new Date(req.created_at).toLocaleString()}
        </span>
      </div>
    `;
  
    // ===== diff =====
    const diffBox = document.createElement("div");
    diffBox.className = "approval-diff";
  
    const diffs = diffObject(req.current_config, req.config);
  
    const pre = document.createElement("pre");
    pre.textContent = diffs.map(d =>
      `${d.old !== undefined ? "- " : ""}${d.key}: ${d.old ?? "—"}\n` +
      `${d.new !== undefined ? "+ " : ""}${d.key}: ${d.new ?? "—"}`
    ).join("\n");
  
    diffBox.appendChild(pre);
  
    // ===== actions =====
    const actions = document.createElement("div");
    actions.className = "approval-actions";
  
    const approveBtn = document.createElement("button");
    approveBtn.className = "btn approve";
    approveBtn.textContent = "✅ Approve";
    approveBtn.onclick = () => approveRequest(req.id);
  
    const rejectBtn = document.createElement("button");
    rejectBtn.className = "btn reject";
    rejectBtn.textContent = "❌ Reject";
    rejectBtn.onclick = () => rejectRequest(req.id);
  
    actions.append(approveBtn, rejectBtn);
  
    // ===== assemble card =====
    card.append(meta, diffBox, actions);
    list.appendChild(card);
  });  
}

async function approveRequest(id) {
  if (!confirm("Approve & apply this change?")) return;

  showApplySpinner();

  const r = await fetch(`/api/requests/${id}/approve`, {
    method: "POST",
    headers: {
      "X-User": "admin",
      "X-Role": "approver"
    }
  });

  let payload = null;
  try { payload = await r.json(); } catch {}

  // ----------------------------
  // ❌ CASE 1: backend error
  // ----------------------------
  if (!r.ok) {
    hideApplySpinner();
    alert(`Approve failed:\n${payload?.detail || "Unknown error"}`);
    await loadApprovals();
    load_audit();
    return;
  }

  // ----------------------------
  // ✔ CASE 2: succes
  // ----------------------------
  const approvedReq = payload || {};

  // Always determine device:
  const device =
    approvedReq.device ||
    document.getElementById("deviceSelect")?.value ||
    null;

  if (!device) {
    console.warn("Approve success, but no device available for refresh");
  } else {
    try {
      const live = await fetch(
        `/api/switches/${device}/interfaces/retrieve`,
        { method: "POST" }
      );

      if (live.ok) {
        const data = await live.json();
        mergeAndRedrawPorts(device, data.interfaces || data);
      }
    } catch (e) {
      console.error("Live refresh failed after approve", e);
    }
  }

  if (approvedReq?.device && approvedReq?.interface) {
    flashApprovedPort(approvedReq.device, approvedReq.interface);
  }

  // Always refresh lists
  await loadApprovals();
  load_audit();

  // Track last approved change (also for delete)
  window.LAST_CHANGED = {
    device: approvedReq.device,
    interface: approvedReq.interface
  };

  // 🔥 FINALLY
  clearModalDiff();
  hideApplySpinner();
}

async function rejectRequest(id) {
  const reason = prompt("Reject reason?");
  if (!reason) return;

  await fetch(`/api/requests/${id}/reject?comment=${encodeURIComponent(reason)}`, {
      method: "POST",
      headers: {
          "X-User": "admin",
          "X-Role": "approver"
      }
  });

  await loadApprovals();
  load_audit(); // 👈 audit altijd vers
}

function setFetchStatus(text, cls = "loading") {
  const el = document.getElementById("fetch-status");
  el.textContent = text;
  el.className = "status " + cls;
}

function allPhysicalPortsVC(members = 2) {
  const ports = [];
  for (let member = 0; member < members; member++) {
    for (let i = 0; i < 48; i++) {
      ports.push({
        name: `ge-${member}/0/${i}`,
        type: "ge",
        member,
        port: i,
        oper_up: false,
        admin_up: false,
        configured: false,
        bundle: null,
        access_vlan: null,
        trunk_vlans: [],
        description: null
      });
    }
  }

  return ports;
}

function allUplinkPortsVC(members = 2) {
  const ports = [];

  for (let member = 0; member < members; member++) {
    for (let i = 0; i < 4; i++) {
      ports.push({
        name: `xe-${member}/2/${i}`,
        type: "xe",
        member,
        configured: false,
        oper_up: false
      });
    }
  }
  return ports;
}

function allAggregatePorts(max = 8) {
  return Array.from({ length: max }, (_, i) => ({
    name: `ae${i}`,
    type: "ae",
    configured: false,
    oper_up: false,
    members: []
  }));
}

// robust merge + redraw helper
function mergeAndRedrawPorts(device, dataOrArray) {
  CURRENT_DEVICE = device;

  // -------- normalize input --------
  let incoming = [];
  if (Array.isArray(dataOrArray)) {
    incoming = dataOrArray;
  } else if (dataOrArray?.interfaces) {
    incoming = dataOrArray.interfaces;
  } else if (dataOrArray?.data) {
    incoming = dataOrArray.data;
  }

  incoming = incoming.filter(p => p?.name);

  // -------- build deterministic skeleton --------
  const map = {};

  // GE (48 per member)
  [0, 1].forEach(member => {
    for (let i = 0; i < 48; i++) {
      const name = `ge-${member}/0/${i}`;
      map[name] = {
        name,
        type: "ge",
        member,
        port: i,
        configured: false,
        oper_up: false,
        admin_up: false,
        bundle: null,
        access_vlan: null,
        trunk_vlans: [],
        vc_port: false,
        pending: false,
        _source: "skeleton"
      };
    }
  });

  // XE (uplinks)
  [0, 1].forEach(member => {
    for (let i = 0; i < 4; i++) {
      const name = `xe-${member}/2/${i}`;
      map[name] = {
        name,
        type: "xe",
        member,
        port: i,
        configured: false,
        oper_up: false,
        bundle: null,
        vc_port: false,
        pending: false,
        _source: "skeleton"
      };
    }
  });

  // AE placeholders (stabiel, max 8)
  for (let i = 0; i < 8; i++) {
    const name = `ae${i}`;
    map[name] = {
      name,
      type: "ae",
      configured: false,
      oper_up: false,
      members: [],
      _source: "skeleton"
    };
  }

  // -------- overlay real data --------
  incoming.forEach(p => {
    const base = map[p.name] || {};
    const pend = pendingByInterface[`${device}|${p.name}`] || null;
  
    map[p.name] = {
      ...base,
      ...p,
      _source: p._source || "live",
  
      pending: !!pend,
      pending_type: pend?.type || null,   // "delete" of null
  
      pending_request: pend || null       // optioneel, voor toekomstige UI
    };
  });
  

  // -------- final render list --------
  const renderPorts = Object.values(map)
    .filter(Boolean)
    .sort(portCompare);

  CURRENT_SWITCH_PORTS = renderPorts;

  drawPorts(renderPorts, device);
}

async function loadPending() {
  const r = await fetch("/api/requests?status=pending");
  if (!r.ok) return;

  const reqs = await r.json();
  pendingByInterface = {};

  reqs.forEach(req => {
    pendingByInterface[`${req.device}|${req.interface}`] = req;
  });
}

function diffObject(oldCfg = {}, newCfg = {}) {
  return Object.keys(newCfg).flatMap(k =>
    oldCfg[k] !== newCfg[k]
      ? [{ key: k, old: oldCfg[k], new: newCfg[k] }]
      : []
  );
}


function fmtVal(v) {
  if (v === undefined || v === null) return "";
  if (Array.isArray(v)) return v.join(",") || "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function isPresent(v) {
  // consider non-empty string / non-empty array / non-null non-undefined as present
  if (v === undefined || v === null) return false;
  if (Array.isArray(v)) return v.length > 0;
  return String(v).trim().length > 0;
}

function debounce(fn, delay) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), delay);
  };
}

function renderDiff(oldCfg, newCfg) {
  const box = document.getElementById("diffBox");
  if (!box) return;

  const diffs = diffObject(oldCfg, newCfg);

  if (!diffs.length) {
    box.innerHTML = `<span style="opacity:.6">No changes</span>`;
    return;
  }

  // build compact HTML (minimal whitespace) and small vertical spacing
  box.innerHTML = diffs.map(d => {
    const oldPresent = isPresent(d.old);
    const newPresent = isPresent(d.new);
    const oldText = fmtVal(d.old) || "—";
    const newText = fmtVal(d.new) || "—";

    // cases:
    // 1) old && new -> show both - and +
    // 2) !old && new -> only + (creation)
    // 3) old && !new -> only - (deletion)
    // 4) neither -> shouldn't happen (skip)
    let out = "";

    if (oldPresent && newPresent) {
      out += `<div class="diff-line diff-remove" style="margin:3px 0">- ${d.key}: ${oldText}</div>`;
      out += `<div class="diff-line diff-add"    style="margin:3px 0">+ ${d.key}: ${newText}</div>`;
    } else if (!oldPresent && newPresent) {
      out += `<div class="diff-line diff-add"    style="margin:3px 0">+ ${d.key}: ${newText}</div>`;
    } else if (oldPresent && !newPresent) {
      out += `<div class="diff-line diff-remove" style="margin:3px 0">- ${d.key}: ${oldText}</div>`;
    }
    return out;
  }).join("");

  // optional: ensure box uses a compact font/line-height
  box.style.lineHeight = "1.15";
}


function collectVcLinks(ports) {
  const vc = ports.filter(p => p.vc_port);

  // group by fpc/port (2/2 matcht 2/2)
  const map = {};
  for (const p of vc) {
    const key = `${p.fpc}/${p.port}`;
    map[key] ||= [];
    map[key].push(p);
  }

  // only pairs
  return Object.values(map).filter(g => g.length === 2);
}

function flashApprovedPort(device, ifname) {
  // alleen highlighten als we op die switch kijken
  if (device !== CURRENT_DEVICE) return;

  const el = document.querySelector(
    `.port[data-ifname="${ifname}"]`
  );

  if (!el) return;

  el.classList.add("approved");

  setTimeout(() => {
    el.classList.remove("approved");
  }, 1600);
}

async function loadVlanCacheStatus(device) {
  const el = document.getElementById("vlan-cache-status");
  el.classList.add("hidden");

  try {
    const r = await fetch(`/api/devices/${device}/vlans`);
    if (!r.ok) return;

    const data = await r.json();

    const ts = new Date(data.updated_at);
    const hh = ts.getHours().toString().padStart(2, "0");
    const mm = ts.getMinutes().toString().padStart(2, "0");

    el.textContent = `VLANs cached • last updated ${hh}:${mm}`;
    el.classList.remove("hidden");
  } catch (e) {
    console.warn("No VLAN cache");
  }
}

document.getElementById("btn-refresh-interfaces").onclick = async () => {
  if (!currentSwitch) return;

  const overlay = document.getElementById("grid-overlay");
  overlay.classList.remove("hidden");

  try {
    // ---- fast refresh if we know what changed ----
    if (window.LAST_CHANGED &&
        window.LAST_CHANGED.device === currentSwitch) {

      const ifname = window.LAST_CHANGED.interface;

      try {
        const rFast = await fetch(
          `/api/interface/${currentSwitch}/${ifname}/refresh`,
          { method: "POST" }
        );

        if (rFast.ok) {
          const j = await rFast.json();
          if (j?.data) {
            console.log("Fast refresh hit:", ifname);
            mergeAndRedrawPorts(currentSwitch, [j.data]);
          }
        }
      } catch (e) {
        console.warn("Fast refresh error:", e);
      }
    }

    // ---- then load cached full list (almost instant) ----
    const r = await fetch(`/api/switches/${currentSwitch}/interfaces`);
    const data = await r.json();
    mergeAndRedrawPorts(currentSwitch, data.interfaces || data);

  } catch (e) {
    console.error(e);
    alert("Config ophalen mislukt");
  } finally {
    overlay.classList.add("hidden");
  }
};

document.getElementById("btn-refresh-vlans").onclick = async () => {
  if (!currentSwitch) return;
  try {
    const r = await fetch(`/api/switches/${currentSwitch}/vlans/refresh`, { method: "POST" });
    if (!r.ok) throw new Error("vlan refresh failed");
    await loadVlanCacheStatus(currentSwitch);
    alert("VLANs refreshed");
  } catch (e) {
    console.error(e);
    alert("VLAN refresh failed");
  }
};

function setSwitchButtons(enabled) {
  document
    .querySelectorAll("[data-requires-switch]")
    .forEach(b => b.disabled = !enabled);
}

async function load_audit() {
  const device = document.getElementById("audit-device")?.value;
  const iface = document.getElementById("audit-interface")?.value;

  const params = new URLSearchParams();
  if (device) params.set("device", device);
  if (iface) params.set("interface", iface);

  const res = await fetch(`/api/audit?${params.toString()}`, {
    headers: authHeaders()
  });

  if (!res.ok) {
    console.error("audit load failed");
    return;
  }

  const rows = await res.json();
  renderAudit(rows);
}

async function loadAuditDevices() {
  const sel = document.getElementById("audit-device");
  if (!sel) return;

  const res = await fetch("/api/switches");
  if (!res.ok) return;

  const list = await res.json();

  list.forEach(d => {
    const o = document.createElement("option");
    o.value = d.name;
    o.textContent = d.name;
    sel.appendChild(o);
  });
}

function renderAudit(rows) {
  if (!auditDT) initAuditTable();
  auditDT.clear();

  rows.forEach(r => {
    const ts = new Date(r.timestamp);
    const epoch = ts.getTime();
    const pretty = ts.toLocaleString("nl-NL");

    const cls = `audit-${r.action}`;
    const icon = {
      approve: "✔️",
      reject: "❌",
      apply_success: "⚙️",
      apply_failed: "⚠️"
    }[r.action] || "ℹ️";

    // één kolom voor tijd
    const timelineCell = `
      <div class="audit-timeline" data-epoch="${epoch}">
        <div class="timeline-dot ${cls}"></div>
        <div class="timeline-content">
          <div class="timeline-time">${pretty}</div>
        </div>
      </div>
    `;

    auditDT.row.add([
      timelineCell,
      r.actor,
      `<span class="audit-event ${cls}">${icon} ${r.action}</span>`,
      r.device,
      r.interface ?? "",
      r.comment ?? ""
    ]);
  });

  auditDT.draw();

  // clickable rows
  $("#audit-table tbody tr").off("click").on("click", function () {
    const idx = auditDT.row(this).index();
    openAuditDetail(rows[idx]);
  });
}

function applyAuditFilters() {
  if (!auditDT) return;
  const table = $('#audit-table').DataTable();

  const dev   = document.getElementById("audit-device").value.trim();
  const iface = document.getElementById("audit-interface").value.trim().toLowerCase();

  table.rows().every(function () {
    const d = this.data();

    const devMatch = !dev || d[3] === dev;
    const ifMatch = !iface || (d[4] && d[4].includes(iface));

    const tr = this.node();     // ← FIXED

    if (!tr) return;

    if (devMatch && ifMatch) {
      tr.style.display = "";      // show
    } else {
      tr.style.display = "none";  // hide
    }
  });
}


// ================== ROLLBACK HANDLERS ==================

/** safe helper to escape HTML */
function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Normalize raw diff text from Junos RPC:
 *  - strip surrounding quotes if present
 *  - remove leading/trailing [edit ...] wrapper lines
 *  - normalize CRLF -> LF
 */
function normalizeRollbackText(txt) {
  if (!txt) return "";

  // Some RPC returns a quoted string (leading/trailing "...")
  txt = txt.trim();
  if ((txt.startsWith('"') && txt.endsWith('"')) || (txt.startsWith("'") && txt.endsWith("'"))) {
    // remove only one outer pair
    txt = txt.slice(1, -1);
  }

  // normalize line endings
  txt = txt.replace(/\r\n/g, "\n");

  // remove [edit ...] and enclosing lines like "[edit interfaces]" or header lines
  // and blank first/last lines
  const lines = txt.split("\n");

  // remove common leading wrapper lines like '[edit interfaces]' or '---'
  let start = 0, end = lines.length;
  while (start < end && lines[start].trim() === "") start++;
  if (start < end && /^\[edit\b/.test(lines[start])) start++;
  while (end > start && lines[end-1].trim() === "") end--;

  return lines.slice(start, end).join("\n");
}

/** scroll sync (idempotent) */
function attachRollbackScrollSync() {
  const left = document.querySelector(".rb-left");
  const right = document.querySelector(".rb-right");
  if (!left || !right) return;

  // avoid binding multiple times: use a flag on element
  if (!left._syncBound) {
    left.addEventListener("scroll", () => {
      right.scrollTop = left.scrollTop;
    });
    left._syncBound = true;
  }
  if (!right._syncBound) {
    right.addEventListener("scroll", () => {
      left.scrollTop = right.scrollTop;
    });
    right._syncBound = true;
  }
}

/** Load devices into rb-device select */
async function loadRollbackDevices() {
  const sel = document.getElementById("rb-device");

  sel.innerHTML = `<option value="">Select a device…</option>`;

  const res = await fetch("/api/inventory");
  if (!res.ok) return;

  const devices = await res.json();

  // voorkom dubbele opties
  const added = new Set();

  devices.forEach(d => {
    if (!added.has(d.name)) {
      const opt = document.createElement("option");
      opt.value = d.name;
      opt.textContent = d.name;
      sel.appendChild(opt);
      added.add(d.name);
    }
  });

  // correcte binding
  sel.onchange = loadRollbackList;
}


/** Load commit list for selected device */
async function loadRollbackList() {
  const dev = document.getElementById("rb-device").value;
  const list = document.getElementById("rb-list");
  const left = document.getElementById("rb-left");
  const right = document.getElementById("rb-right");
  const applyBtn = document.getElementById("rb-apply");

  if (!list || !left || !right || !applyBtn) return;

  list.innerHTML = "";
  left.textContent = "Select a commit…";
  right.textContent = "";

  applyBtn.disabled = true;
  applyBtn.onclick = null;

  if (!dev) return;

  const res = await fetch(`/api/rollback/${dev}`, { headers: authHeaders() });
  const commits = await res.json();

  commits.forEach(item => {
    const li = document.createElement("li");
    li.textContent = `${item.index}: ${item.timestamp} — ${item.user}`;
    li.dataset.rb = item.index;
    li.onclick = () => loadRollbackDiff(dev, item.index, li);
    list.appendChild(li);
  });
}

/** Load and render diff for a selected commit index */
async function loadRollbackDiff(device, index, li) {
  document.querySelectorAll(".rb-list li").forEach(n => n.classList.remove("selected"));
  li.classList.add("selected");

  const applyBtn = document.getElementById("rb-apply");
  const leftBox = document.getElementById("rb-left");
  const rightBox = document.getElementById("rb-right");

  // Enable apply button
  applyBtn.disabled = false;
  applyBtn.onclick = () => applyRollback(device, index);

  leftBox.textContent = "Loading…";
  rightBox.textContent = "Loading…";

  const res = await fetch(`/api/rollback/${device}/${index}/diff`, {
    headers: authHeaders()
  });

  if (!res.ok) {
    leftBox.textContent = "Failed to load diff.";
    rightBox.textContent = "";
    return;
  }

  const txt = await res.text();

  if (!txt.trim()) {
    leftBox.textContent = "(no diff)";
    rightBox.textContent = "";
    return;
  }

  // Parse diff in twee kolommen
  const parsed = parseDiff(txt);

  leftBox.textContent = parsed.left.join("\n");
  rightBox.textContent = parsed.right.join("\n");
}


async function applyRollback(device, idx) {
  if (!confirm(`Are you sure you want to apply rollback ${idx} on ${device}?`)) {
    return;
  }

  try {
    const res = await fetch(
      `/api/rollback/${device}/${idx}/apply`,
      {
        method: "POST",
        headers: authHeaders()
      }
    );

    const txt = await res.json();

    if (!res.ok) {
      alert("Rollback failed: " + txt.detail);
      return;
    }
  
    // --- SUCCESS ---
    alert(`Rollback ${idx} applied successfully.`);
  
    // 🔄 Refresh Audit log
    load_audit();
    loadRollbackList();
  
    // 🔄 Refresh interfaces grid (if you're on switch tab)
    if (currentSwitch) {
      await fetch(`/api/switches/${currentSwitch}/interfaces/retrieve`, { method: "POST" });
    }
  } catch (e) {
    console.error("applyRollback:", e);
    alert("Network error while applying rollback.");
  }
}

function parseDiff(txt) {
  const left = [];
  const right = [];

  const lines = txt.split("\n");

  lines.forEach((line, i) => {

    // 1️⃣ Eerste regel = context → in beide kolommen
    if (i === 0 && !line.startsWith("+") && !line.startsWith("-")) {
      left.push("  " + line);
      right.push("  " + line);
      return;
    }

    // 2️⃣ Diff regels
    if (line.startsWith("-")) {
      left.push(line);
      right.push("");
    }
    else if (line.startsWith("+")) {
      left.push("");
      right.push(line);
    }
    else {
      // 3️⃣ unchanged
      left.push("  " + line);
      right.push("  " + line);
    }
  });

  return { left, right };
}

// init hookup (call once on DOMContentLoaded)
function initRollbackTab() {
  // ensure left/right exist -> attach sync
  attachRollbackScrollSync();
}

function initAuditTable() {
  if (!auditDT) {
    auditDT = new DataTable("#audit-table", {
      paging: true,
      lengthChange: false,
      searching: false,
      info: false,
      ordering: true,
      pageLength: 15,
      stripeClasses: [],
      order: [[0, 'desc']],
      columnDefs: [
        {
          targets: 0,
          orderable: true,
          type: "num",
          render: function(data, type, row) {
            if (type === "sort") {
              // extract epoch
              const el = document.createElement("div");
              el.innerHTML = data;
              return el.querySelector(".audit-timeline")?.dataset.epoch ?? 0;
            }
            return data;
          }
        }
      ]
    });
  }
}

function openAuditDetail(row) {
  const modal = document.getElementById("auditDetailModal");
  const box   = document.getElementById("auditDetailBox");

  box.innerHTML = `
    <div><b>Device:</b> ${row.device}</div>
    <div><b>Interface:</b> ${row.interface}</div>
    <div><b>Action:</b> ${row.action}</div>
    <div><b>User:</b> ${row.actor}</div>
    <hr>
    <pre>${JSON.stringify(row, null, 2)}</pre>
  `;

  modal.classList.remove("hidden");
}

document.getElementById("auditDetailClose").onclick = () => {
  document.getElementById("auditDetailModal").classList.add("hidden");
};

function clearModalDiff() {
  const diffBox = document.getElementById("diffBox");
  if (diffBox) diffBox.innerHTML = "No changes";
}
