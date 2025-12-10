// frontend/public/renderer.js
// Responsible for rendering the EX4300 faceplate, hover tooltips, VLAN highlight

const _client_live_cache = {}; // key: device|ifname -> {ts, data}
const CLIENT_LIVE_TTL = 10 * 1000; // 10s
let activeTooltip = null;
let globalTooltip = null;

// ---------- helpers ----------
function getTooltip() {
  if (!globalTooltip) {
    globalTooltip = document.createElement("div");
    globalTooltip.className = "port-tooltip hidden";
    document.body.appendChild(globalTooltip);
  }
  return globalTooltip;
}

function parseIfname(name) {
  // ge-0/0/12 → { type:"ge", member:0, index:12 }
  const ge = name.match(/^(ge|xe)-(\d+)\/\d+\/(\d+)$/);
  if (ge) {
    return {
      type: ge[1],
      member: Number(ge[2]),
      index: Number(ge[3])
    };
  }

  // ae0 → { type:"ae" }
  if (name.startsWith("ae")) {
    return { type: "ae" };
  }

  return null;
}

function positionTooltip(t, ev) {
  const pad = 8;
  let left = ev.pageX + pad, top = ev.pageY + pad;
  const r = t.getBoundingClientRect();
  if (left + r.width > window.innerWidth) left = ev.pageX - r.width - pad;
  if (top + r.height > window.innerHeight) top = ev.pageY - r.height - pad;
  t.style.left = left + "px";
  t.style.top = top + "px";
}

// ---------- client-side live fetch with TTL & safety ----------
export async function fetchInterfaceLiveClient(device, ifname, portObj = null) {
  if (!device) return null;

  // Hard absolute safety: only query /live for configured + up ports
  if (!portObj || portObj.configured !== true || portObj.oper_up !== true) return null;

  const key = `${device}|${ifname}`;
  const now = Date.now();
  const entry = _client_live_cache[key];
  if (entry && (now - entry.ts < CLIENT_LIVE_TTL)) return entry.data;

  try {
    const res = await fetch(`/api/switches/${device}/interface/${encodeURIComponent(ifname)}/live`);
    if (!res.ok) return null;
    const data = await res.json();
    _client_live_cache[key] = { ts: now, data };
    return data;
  } catch (e) {
    return null;
  }
}

// ---------- VLAN highlight ----------
export function highlightVlan(vlan) {
  if (!vlan) return;
  document.querySelectorAll(`[data-vlan~="${vlan}"]`).forEach(el => el.classList.add("vlan-hover"));
}
export function clearVlanHighlight() {
  document.querySelectorAll(".vlan-hover").forEach(el => el.classList.remove("vlan-hover"));
}

// ---------- port tile ----------
function portTile(port, device) {
  const el = document.createElement("div");
  el.className = "port";
  el.dataset.ifname = port.name;

  /* ---------------------------
     DATA ATTRIBUTES
  --------------------------- */
  const vlanTokens = [];
  if (port.access_vlan) vlanTokens.push(String(port.access_vlan));
  if (Array.isArray(port.trunk_vlans)) {
    vlanTokens.push(...port.trunk_vlans.map(String));
  }
  if (vlanTokens.length) el.dataset.vlan = vlanTokens.join(" ");

  if (port.bundle) el.dataset.bundle = port.bundle;
  if (port.type === "ae") el.dataset.ae = port.name;
  
  /* ---------------------------
     STATE CLASSES
  --------------------------- */
  if (!port.configured) el.classList.add("unconfigured");
  if (port.oper_up) el.classList.add("oper-up");
  else el.classList.add("oper-down");
  // pending icon
  if (port.pending) {
    const badge = document.createElement("div");
    badge.className = "pending-badge";
    badge.title = "Pending change request";
    badge.textContent = "⏳";
    el.appendChild(badge);
    el.classList.add("pending");
  }
  if (port.approved) el.classList.add("approved");

  if (port.bundle) el.classList.add("is-bundled");
  if (port.mode === "trunk") el.classList.add("is-trunk");
  if (port.mode === "access") el.classList.add("is-access");

  if (port.vc_port) {
    el.classList.add("vc-port");
    el.dataset.vcLink = vcLinkId(port.name);
  }
  if (port.name.startsWith("ge-1/") || port.name.startsWith("xe-1/")) {
    el.classList.add("member-1");
  } else {
    el.classList.add("member-0");
  }

  /* ---------------------------
     LABEL
  --------------------------- */
  const label = document.createElement("div");
  label.className = "label-top";
  label.textContent = port.name.split("/").pop();
  el.appendChild(label);

  /* ---------------------------
     STATUS DOT
  --------------------------- */
  const dot = document.createElement("div");
  dot.className = `dot ${port.oper_up ? "oper-up" : "oper-down"}`;
  el.appendChild(dot);

  /* ---------------------------
     TOOLTIP + HOVER
  --------------------------- */
  el.addEventListener("mouseenter", ev => {
    // LACP highlighting
    if (port.type === "ae") highlightBundle(port.name);
    if (port.bundle) {
      document
        .querySelectorAll(`[data-bundle="${port.bundle}"]`)
        .forEach(p => p.classList.add("lacp-highlight"));
    }

    showTooltip(`
      <strong>${port.name}</strong>
      <div>Status: ${port.oper_up ? "up" : "down"}</div>
      <div>Mode: ${port.mode ?? "—"}</div>
      <div>Access VLAN: ${port.access_vlan ?? "—"}</div>
      <div>Bundle: ${port.bundle ?? "—"}</div>
    `, ev);
  });

  el.addEventListener("mousemove", moveTooltip);

  el.addEventListener("mouseleave", () => {
    hideTooltip();
    clearBundle();
    document
      .querySelectorAll(".lacp-highlight")
      .forEach(p => p.classList.remove("lacp-highlight"));
  });

  /* ---------------------------
     CLICK → MODAL
  --------------------------- */
  el.addEventListener("click", () => {
    if (window.openModalForPort) {
      window.openModalForPort(port);
    }
  });

  return el;
}

// ---------- main renderer ----------
export function drawPorts(ports, device) {

  // containers
  const grids = {
    m0: {
      geTop: document.getElementById("m0-ge-top"),
      geBottom: document.getElementById("m0-ge-bottom"),
      xe: document.getElementById("m0-xe"),
    },
    m1: {
      geTop: document.getElementById("m1-ge-top"),
      geBottom: document.getElementById("m1-ge-bottom"),
      xe: document.getElementById("m1-xe"),
    },
    ae: document.getElementById("grid-ae")
  };
  el.classList.toggle("cached", p._source === "cache");
  el.classList.toggle("live", p._source === "live");
  el.classList.toggle("pending", p.pending === true);
  
  // clear
  Object.values(grids).forEach(g => {
    if (!g) return;
    Object.values(g).forEach(el => el && (el.innerHTML = ""));
  });

  ports.forEach(port => {
    
    const info = parseIfname(port.name);
    if (!info) return;

    // ---------- GE ----------
    if (info.type === "ge") {
      const member = info.member === 1 ? grids.m1 : grids.m0;
      if (!member) return;

      const target =
        info.index % 2 === 0 ? member.geTop : member.geBottom;

      if (target) {
        target.appendChild(portTile(port, device));
      }
    }

    // ---------- XE ----------
    else if (info.type === "xe") {
      const member = port.name.startsWith("xe-1/")
        ? grids.m1
        : grids.m0;

      if (member?.xe) {
        member.xe.appendChild(portTile(port, device));
      }
    }

    // ---------- AE ----------
    else if (info.type === "ae" && port.configured) {
      grids.ae?.appendChild(portTile(port, device));
    }
  });
}

function highlightBundle(ae) {
  document
    .querySelectorAll(`[data-bundle="${ae}"], [data-ae="${ae}"]`)
    .forEach(p => p.classList.add("bundle-hover"));
}

function clearBundle() {
  document
    .querySelectorAll(".bundle-hover, .lacp-highlight")
    .forEach(p => {
      p.classList.remove("bundle-hover");
      p.classList.remove("lacp-highlight");
    });
}

function showTooltip(html, ev) {
  const t = getTooltip();
  t.innerHTML = html;
  t.classList.remove("hidden");
  moveTooltip(ev);
}

function moveTooltip(ev) {
  const t = getTooltip();
  const pad = 10;
  let x = ev.pageX + pad;
  let y = ev.pageY + pad;

  const r = t.getBoundingClientRect();
  if (x + r.width > window.innerWidth) x -= r.width + pad;
  if (y + r.height > window.innerHeight) y -= r.height + pad;

  t.style.left = x + "px";
  t.style.top = y + "px";
}

function hideTooltip() {
  if (globalTooltip) globalTooltip.classList.add("hidden");
}

function vcLinkId(ifname) {
  // xe-0/2/2 → 2/2
  const m = ifname.match(/^xe-\d+\/(\d+)\/(\d+)$/);
  return m ? `${m[1]}/${m[2]}` : null;
}

/* =========================================
   VC LINK HOVER & LOCK
========================================= */

document.addEventListener("mouseover", e => {
  const port = e.target.closest(".port.vc-port");
  if (!port || !port.dataset.vcLink) return;

  const id = port.dataset.vcLink;

  document
    .querySelectorAll(`.port.vc-port[data-vc-link="${id}"]`)
    .forEach(p => p.classList.add("vc-highlight"));
});

document.addEventListener("mouseout", e => {
  const port = e.target.closest(".port.vc-port");
  if (!port) return;

  document
    .querySelectorAll(".port.vc-highlight")
    .forEach(p => p.classList.remove("vc-highlight"));
});

// ❌ VC ports cannot be clicked
document.addEventListener("click", e => {
  const port = e.target.closest(".port.vc-port");
  if (port) {
    e.stopPropagation();
    e.preventDefault();
  }
}, true);
