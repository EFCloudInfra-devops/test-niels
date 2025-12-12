// frontend/public/renderer.js
// Responsible for rendering the EX4300 faceplate, hover tooltips, VLAN highlight

const _client_live_cache = {}; // key: device|ifname -> {ts, data}
const CLIENT_LIVE_TTL = 10 * 1000; // 10s
// let activeTooltip = null;
let globalTooltip = null;

/* =========================================
   New: global registries to prevent duplication
========================================= */
const PORT_DOM = new Map(); // ifname → DOM element
// ✅ debug expose (alleen voor debugging)
window.__PORT_DOM__ = PORT_DOM;
let RENDER_DEVICE = null;

// ========================================

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

function createPortTile(initialPort) {
  const el = document.createElement("div");
  el.className = "port";
  el.dataset.ifname = initialPort.name;

  // store initial data, but will be updated by updatePortTile()
  el.dataset.port = JSON.stringify(initialPort);

  const label = document.createElement("div");
  label.className = "label-top";
  el.appendChild(label);

  const dot = document.createElement("div");
  dot.className = "dot";
  el.appendChild(dot);

  el.addEventListener("mouseenter", ev => {
    const port = JSON.parse(el.dataset.port || "{}");

    if (port.type === "ae") highlightBundle(port.name);
    if (port.bundle) {
      document
        .querySelectorAll(`[data-bundle="${port.bundle}"]`)
        .forEach(p => p.classList.add("lacp-highlight"));
    }

    const aeMembers =
      port.type === "ae"
        ? [...document.querySelectorAll(`[data-bundle="${port.name}"]`)]
            .map(e => e.dataset.ifname)
            .join(", ") || "None"
        : null;

    showTooltip(
      `
      <div class="tt-title"><strong>${port.name}</strong></div>

      ${
        port.description
          ? `<div class="tt-desc">${port.description}</div>`
          : `<div class="tt-desc tt-muted">No description</div>`
      }

      <div class="tt-line"><strong>Status:</strong> ${
        port.oper_up === true ? "Up" :
        port.oper_up === false ? "Down" :
        "Cached"
      }</div>

      <div class="tt-line"><strong>Mode:</strong> ${port.mode ?? "—"}</div>

      <div class="tt-line"><strong>Access VLAN:</strong> ${port.access_vlan ?? "—"}</div>

      <div class="tt-line"><strong>Native VLAN:</strong> ${port.native_vlan ?? "—"}</div>

      <div class="tt-line"><strong>Trunk VLANs:</strong> ${
        Array.isArray(port.trunk_vlans) && port.trunk_vlans.length
          ? port.trunk_vlans.join(", ")
          : "—"
      }</div>

      <div class="tt-line"><strong>Bundle:</strong> ${port.bundle ?? "—"}</div>

      ${
        port.type === "ae"
          ? `<div class="tt-line"><strong>AE members:</strong> ${aeMembers}</div>`
          : ""
      }

      ${
        port.vc_port
          ? `<div class="tt-line"><strong>VC Status:</strong> ${
              port.vc_status || "Up"
            }</div>`
          : ""
      }
      `,
      ev
    );
  });

  el.addEventListener("mousemove", moveTooltip);

  el.addEventListener("mouseleave", () => {
    hideTooltip();
    clearBundle();
    document
      .querySelectorAll(".lacp-highlight")
      .forEach(p => p.classList.remove("lacp-highlight"));
  });

  el.addEventListener("click", () => {
    const port = JSON.parse(el.dataset.port || "{}");
    window.openModalForPort?.(port);
  });

  return el;
}

function updatePortTile(el, port) {
  el.classList.remove(
    "cached",
    "live",
    "pending",
    "approved",
    "unconfigured",
    "oper-up",
    "oper-down",
    "is-bundled",
    "vc-port",
    "member-0",
    "member-1"
  );
  el.classList.add("port");  el.dataset.port = JSON.stringify(port);
  el.dataset.description = port.description ?? "";

  if (port._source === "cache") el.classList.add("cached");
  if (port._source === "live") el.classList.add("live");
  if (port.pending) el.classList.add("pending");
  if (port.approved) el.classList.add("approved");
  if (!port.configured) el.classList.add("unconfigured");

  el.classList.add(port.oper_up ? "oper-up" : "oper-down");

  if (port.bundle) {
    el.classList.add("is-bundled");
    el.dataset.bundle = port.bundle;
  } else {
    delete el.dataset.bundle;
  }

  if (port.type === "ae") el.dataset.ae = port.name;
  else delete el.dataset.ae;

  if (port.vc_port) {
    el.classList.add("vc-port");
    el.dataset.vcLink = vcLinkId(port.name);
  } else {
    delete el.dataset.vcLink;
  }

  el.classList.add(
    port.name.startsWith("ge-1/") || port.name.startsWith("xe-1/")
      ? "member-1"
      : "member-0"
  );

  const label = el.querySelector(".label-top");
  label.textContent = port.name.split("/").pop();

  const dot = el.querySelector(".dot");
  dot.className = `dot ${port.oper_up ? "oper-up" : "oper-down"}`;

  // VLAN tokens
  const vlans = [];
  if (port.access_vlan) vlans.push(port.access_vlan);
  if (Array.isArray(port.trunk_vlans)) vlans.push(...port.trunk_vlans);
  if (vlans.length) el.dataset.vlan = vlans.join(" ");
  else delete el.dataset.vlan;
  
  // ---------------------------------------------
  // PENDING BADGES (change + delete) + pulsate
  // ---------------------------------------------

  // verwijder oude badges
  el.querySelectorAll(".pending-badge").forEach(b => b.remove());

  if (port.pending) {
    const badge = document.createElement("div");
    badge.classList.add("pending-badge", "pulsate");

    if (port.pending_type === "delete") {
      badge.textContent = "DEL";
      badge.classList.add("pending-delete");
    } else {
      badge.textContent = "PEND";
      badge.classList.add("pending-change");
    }

    el.appendChild(badge);
  }
}

// ---------- main renderer ----------
export function drawPorts(ports, device) {

  // first-time init: set RENDER_DEVICE if null
  if (RENDER_DEVICE === null) {
    RENDER_DEVICE = device;
    // do NOT clear PORT_DOM on first set — we want skeleton elements reused
  } else if (device !== RENDER_DEVICE) {
    // device changed AFTER initial render
    // Only clear if switching between TWO real devices (not skeleton -> real)
    const bothReal = (RENDER_DEVICE !== "__skeleton__") && (device !== "__skeleton__");
    if (bothReal) {
      // switching between two real devices -> clear DOM cache so we don't mix devices
      PORT_DOM.forEach(el => el.remove());
      PORT_DOM.clear();
    }
    // if going skeleton -> real OR real -> skeleton, we keep PORT_DOM and reuse nodes
    RENDER_DEVICE = device;
  }

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

  // track which ports we saw this frame
  const seen = new Set();

  for (const port of ports) {
    if (!port || !port.name) continue;
    const info = parseIfname(port.name);
    if (!info) continue;

    seen.add(port.name);

    let el = PORT_DOM.get(port.name);

    if (!el) {
      // not present yet — create and attach
      el = createPortTile(port);
      PORT_DOM.set(port.name, el);

      if (info.type === "ae") {
        grids.ae?.appendChild(el);
      } else if (info.type === "xe") {
        const m = port.name.startsWith("xe-1/") ? grids.m1 : grids.m0;
        m?.xe?.appendChild(el);
      } else {
        const m = info.member === 1 ? grids.m1 : grids.m0;
        const target = info.index % 2 === 0 ? m.geTop : m.geBottom;
        target?.appendChild(el);
      }
    } else {
      // already have element — ensure it's in the right container (in case structure changed)
      const parentExpected = (info.type === "ae")
        ? grids.ae
        : (info.type === "xe")
          ? (port.name.startsWith("xe-1/") ? grids.m1?.xe : grids.m0?.xe)
          : (info.member === 1 ? (info.index % 2 === 0 ? grids.m1?.geTop : grids.m1?.geBottom) : (info.index % 2 === 0 ? grids.m0?.geTop : grids.m0?.geBottom));

      if (parentExpected && el.parentElement !== parentExpected) {
        parentExpected.appendChild(el);
      }
    }

    // update visual state
    updatePortTile(el, port);
  }

  // cleanup any port DOM entries that are no longer in the incoming list
  for (const [key, el] of PORT_DOM.entries()) {
    if (!seen.has(key)) {
      // remove from DOM and cache
      el.remove();
      PORT_DOM.delete(key);
    }
  }
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

function renderAudit(rows) {
  const tbody = document.getElementById("audit-body");
  if (!tbody) return;

  tbody.innerHTML = "";

  rows.forEach(r => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${new Date(r.timestamp).toLocaleString()}</td>
      <td>${r.actor}</td>
      <td>${r.action}</td>
      <td>${r.device || "-"}</td>
      <td>${r.interface || "-"}</td>
      <td>${r.comment || ""}</td>
    `;
    tbody.appendChild(tr);
  });
}
