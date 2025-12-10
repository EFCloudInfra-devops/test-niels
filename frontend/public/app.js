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
let CURRENT_SWITCH_RENDER_STATE = null;
const authHeaders = {
  "X-User": "admin",
  "X-Role": "admin",
};
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

function renderInitialVC() {
  const ports = [
    ...allPhysicalPortsVC(2),
    ...allUplinkPortsVC(2),
    ...allAggregatePorts(8)
  ];

  drawPorts(ports, "__skeleton__");
}

function showView(name) {
  const views = ["switch", "approvals", "audit"];

  views.forEach(v => {
    document
      .getElementById(`view-${v}`)
      ?.classList.toggle("hidden", v !== name);

    document
      .getElementById(`tab-${v}`)
      ?.classList.toggle("active", v === name);
  });

  if (name === "approvals") {
    loadApprovals();
    return;
  }

  if (name === "audit") {
    load_audit();   // 👈 straks je audit loader
    return;
  }

  if (name === "switch") {
    loadPending();

    // alleen redraw als data al bestaat
    if (CURRENT_DEVICE && CURRENT_SWITCH_PORTS) {
      drawPorts(CURRENT_SWITCH_PORTS, CURRENT_DEVICE);
    }
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
        if (descr) descr.value = live.description || descr.value || "";
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

  const tabAudit  = document.getElementById("tab-audit");
  const tabConfig = document.getElementById("tab-config");
  const panelCfg  = document.getElementById("tab-panel-config");
  const panelAud  = document.getElementById("tab-panel-audit");

  if (tabAudit && tabConfig) {
    tabAudit.onclick = () => {
      tabAudit.classList.add("active");
      tabConfig.classList.remove("active");

      panelAud.classList.remove("hidden");
      panelCfg.classList.add("hidden");

      load_audit(port); // 🔥 HIER
    };

    tabConfig.onclick = () => {
      tabConfig.classList.add("active");
      tabAudit.classList.remove("active");

      panelCfg.classList.remove("hidden");
      panelAud.classList.add("hidden");
    };
  }
  
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
        alert("Change request created (pending approval)");
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

function closeModal() {
  const modal = document.getElementById("modal");
  const portInfo = document.getElementById("portInfo");

  modal.classList.add("hidden");

  // 🔥 dit miste
  if (portInfo) portInfo.innerHTML = "";

  // optional: reset form
  const form = document.getElementById("configForm");
  if (form) form.reset();
}

window.closeModal = closeModal;

document.addEventListener("click", (e) => {
  if (
    e.target.id === "cancelModalBtn" ||
    e.target.id === "closeModalBtn" ||
    e.target.classList.contains("modal-close")
  ) {
    closeModal();
  }
});

document.getElementById("modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") {
    closeModal();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

// bootstrap
document.addEventListener("DOMContentLoaded", () => {

  document.getElementById("tab-switch").onclick = () => showView("switch");
  document.getElementById("tab-approvals").onclick = () => showView("approvals");
  document.getElementById("tab-audit").onclick = () => showView("audit");

  renderInitialVC();     // ✅ altijd iets tonen
  loadSwitches();        // vult dropdown
  loadPending();
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

  const r = await fetch(`/api/requests/${id}/approve`, {
    method: "POST",
    headers: {
      "X-User": "admin",
      "X-Role": "approver"
    }
  });

  if (!r.ok) { alert("Approve failed"); return; }

  let approvedReq = null;
  try {
    approvedReq = await r.json();
  } catch {
    // fallback: niets terug
  }

  // 🔥 refresh pending map
  await loadPending();

  // 🔄 refresh switch data (cached first)
  await reloadAllPorts();

  // ✨ highlight approved port
  if (approvedReq) {
    flashApprovedPort(
      approvedReq.device,
      approvedReq.interface
    );
  }

  // refresh approvals list
  await loadApprovals();
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
}

function setFetchStatus(text, cls = "loading") {
  const el = document.getElementById("fetch-status");
  el.textContent = text;
  el.className = "status " + cls;
}

// async function loadInterfaces(device) {
//   try {
//     const r = await fetch(`/api/switches/${device}/interfaces`);
//     const data = await r.json();

//     setFetchStatus("✅ Switch data geladen", "ok");

//     mergeAndRedrawPorts(device, data);

//   } catch {
//     setFetchStatus("⚠️ Kan switch niet ophalen", "error");
//   }
// }

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
    map[p.name] = {
      ...base,
      ...p,
      _source: p._source || "live",
      pending: !!pendingByInterface[`${device}|${p.name}`]
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
    const r = await fetch(`/api/switches/${currentSwitch}/interfaces/retrieve`, { method: "POST" });
    if (!r.ok) throw new Error("retrieve failed");
    const data = await r.json();
    // endpoint returns { interfaces: [...] } — pass the full payload as 'live' override
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

async function load_audit(device = "") {
  const res = await fetch(
    `/api/audit${device ? `?device=${device}` : ""}`,
    { headers: authHeaders }
  );

  const data = await res.json();

  const body = document.getElementById("auditTableBody");
  const empty = document.getElementById("no-audit");

  body.innerHTML = "";

  if (!data.length) {
    empty.classList.remove("hidden");
    return;
  }

  empty.classList.add("hidden");

  for (const row of data) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${new Date(row.updated_at || row.created_at).toLocaleString()}</td>
      <td>${row.device}</td>
      <td>${row.interface}</td>
      <td class="status ${row.status}">${row.status}</td>
      <td>${row.approver || row.requester}</td>
      <td>${row.comment || ""}</td>
    `;
    body.appendChild(tr);
  }
}