# /app/backend/app/netconf.py
import os
import json
import time
import threading, re
from ncclient import manager
from ncclient.xml_ import to_ele, new_ele, sub_ele, to_xml
from lxml import etree
from datetime import datetime
from .models import InterfaceCache
import xml.sax.saxutils as sax

DEFAULT_PORT = 830

# Simple in-memory caches and locks
_CACHE_LOCK = threading.Lock()
_cache_interfaces = {}   # device -> {"ts": float, "data": [...]}
_cache_live = {}         # (device, ifname) -> {"ts": float, "data": {...}}
_device_locks = {}       # device -> threading.Lock()
_cache_ae = {}            # (device, ae_name) -> {"ts": float, "data": dict}
_CACHE_VC = {}       # device -> { ts, data }


# TTLs (seconds)
INTERFACES_TTL = float(os.getenv("INTERFACES_TTL", "5"))   # small TTL
INTERFACE_LIVE_TTL = float(os.getenv("INTERFACE_LIVE_TTL", "3"))
AE_TTL = float(os.getenv("AE_TTL", "15"))

def fetch_interfaces(device):
    """
    Public wrapper used by jobs — returns a list of interfaces.
    """
    # use the existing "get_interfaces_raw" which returns a list (config+oper)
    return get_interfaces_raw(device)

def _get_device_lock(dev_name):
    with _CACHE_LOCK:
        if dev_name not in _device_locks:
            _device_locks[dev_name] = threading.Lock()
        return _device_locks[dev_name]

def connect(dev):
    """dev may be dict or device-name (string)"""
    from .devices import get_device
    if isinstance(dev, str):
        dev = get_device(dev)
    host = dev.get("host")
    port = dev.get("port", DEFAULT_PORT)
    user = dev.get("username")
    pw = dev.get("password")
    return manager.connect(host=host, port=port, username=user, password=pw,
                           hostkey_verify=False, allow_agent=False, look_for_keys=False, timeout=30)

def to_ele(response):
    try:
        data_xml = response.data_xml
    except Exception:
        data_xml = str(response)
    return etree.fromstring(data_xml.encode()) if isinstance(data_xml, str) else response

# --------------------------
# (Your existing parsing functions)
# I include the same parse_interfaces_config, get_configuration, get_operational,
# get_vlans, get_interface_live, commit_changes etc., but keep them as internal helpers.
# --------------------------

def get_configuration(dev):
    with connect(dev) as m:
        try:
            criteria = etree.XML('<configuration><interfaces/></configuration>')
            reply = m.get_config(source='running', filter=('subtree', criteria))
            return to_ele(reply)
        except Exception:
            reply = m.get_config(source='running')
            return to_ele(reply)
        
def _get_interfaces_config_cached_ele(dev_name):
    """
    Returns cached <configuration><interfaces> element for device.
    Reuses get_interfaces_cached TTL but returns XML tree for AE parsing.
    """
    cfg = get_configuration(dev_name)
    return cfg

def parse_interfaces_config(cfg_ele):
    interfaces = []
    for ifl in cfg_ele.xpath('//*[local-name()="configuration"]/*[local-name()="interfaces"]/*[local-name()="interface"]'):
        # name
        name_list = ifl.xpath('./*[local-name()="name"]/text()')
        name = name_list[0].strip() if name_list else None
        if not name:
            continue
        # type/indices
        if name.startswith('ae'):
            scheme = 'ae'
            member_i = 0; fpc_i = 0
            port_i = int(name[2:]) if name[2:].isdigit() else 0
        else:
            try:
                scheme, rest = name.split('-', 1)
                member, fpc, port = rest.split('/')
                member_i = int(member); fpc_i = int(fpc); port_i = int(port)
            except Exception:
                # unknown naming scheme — skip
                continue
        # description
        desc_list = ifl.xpath('./*[local-name()="description"]/text()')
        description = desc_list[0].strip() if desc_list else None
        # unit/family/esw
        unit_nodes = ifl.xpath('./*[local-name()="unit"]')
        unit0 = unit_nodes[0] if unit_nodes else None
        family_nodes = unit0.xpath('./*[local-name()="family"]') if unit0 is not None else []
        family = family_nodes[0] if family_nodes else None
        esw_nodes = family.xpath('./*[local-name()="ethernet-switching"]') if family is not None else []
        esw = esw_nodes[0] if esw_nodes else None
        mode = 'access'; access_vlan = None; trunk_vlans = None; native_vlan = None
        if esw is not None:
            im_list = esw.xpath('./*[local-name()="interface-mode"]/text()')
            if not im_list:
                im_list = esw.xpath('./*[local-name()="port-mode"]/text()')
            if im_list:
                mode = im_list[0].strip()
            members = esw.xpath('./*[local-name()="vlan"]/*[local-name()="members"]/text()')
            members = [m.strip() for m in members if m and m.strip()]
            if mode == 'access' and members:
                access_vlan = members[0]
            elif mode == 'trunk' and members:
                trunk_vlans = members
            nvid_list = esw.xpath('./*[local-name()="native-vlan-id"]/text()')
            if nvid_list:
                native_vlan = nvid_list[0].strip()
        # ether-options / 802.3ad bundle
        poe = None; speed = None; duplex = None; bundle = None
        eo_nodes = ifl.xpath('./*[local-name()="ether-options"]')
        eo = eo_nodes[0] if eo_nodes else None
        if eo is not None:
            sp_list = eo.xpath('./*[local-name()="speed"]/text()')
            if sp_list: speed = sp_list[0].strip()
            nd_nodes = eo.xpath('./*[local-name()="no-auto-negotiation"]')
            if nd_nodes: duplex = 'full'
            b1 = eo.xpath('.//*[local-name()="ieee-802.3ad"]/*[local-name()="bundle"]/text()')
            if not b1:
                b1 = eo.xpath('.//*[contains(local-name(),"802.3ad")]/text()')
            if b1: bundle = b1[0].strip()
        # aggregated-ether-options for ae*
        agg_nodes = ifl.xpath('./*[local-name()="aggregated-ether-options"]')
        agg = agg_nodes[0] if agg_nodes else None
        lacp_mode = None
        if agg is not None:
            if agg.xpath('./*[local-name()="lacp"]/*[local-name()="active"]'): lacp_mode = 'active'
            elif agg.xpath('./*[local-name()="lacp"]/*[local-name()="passive"]'): lacp_mode = 'passive'
        aggregate = (scheme == 'ae')
        # determine configured state
        has_switching = unit0 is not None
        has_description = bool(description)
        has_bundle = bool(bundle)
        has_speed = bool(speed) or bool(duplex)

        configured = has_switching or has_description or has_bundle or aggregate or has_speed

        interfaces.append({
            'name': name,
            'member': member_i,
            'fpc': fpc_i,
            'type': scheme,
            'aggregate': aggregate,
            'bundle': bundle,
            'port': port_i,
            'mode': mode,
            'access_vlan': access_vlan,
            'trunk_vlans': trunk_vlans,
            'native_vlan': native_vlan,
            'poe': poe,
            'speed': speed,
            'duplex': duplex,
            'admin_up': True,
            'oper_up': False,
            'configured': configured,
            'description': description,
            'lacp_mode': lacp_mode,
        })
    return interfaces

def get_ae_summary_cached(dev_name, ae_name):
    """
    Fast AE lookup WITHOUT live RPC.
    Uses interfaces config only.
    """
    now = time.time()
    key = (dev_name, ae_name)

    # cache hit
    entry = _cache_ae.get(key)
    if entry and (now - entry["ts"] < AE_TTL):
        return entry["data"]

    cfg_ele = _get_interfaces_config_cached_ele(dev_name)

    result = {
        "name": ae_name,
        "type": "ae",
        "configured": False,
        "oper_up": False,
        "admin_up": True,
        "members": [],
        "bundle": None,
        "mode": None,
        "access_vlan": None,
        "trunk_vlans": None,
        "native_vlan": None,
        "lacp_mode": None,
        "description": None,
    }

    # walk interfaces once
    for ifl in cfg_ele.xpath('//*[local-name()="interfaces"]/*[local-name()="interface"]'):
        name = ifl.xpath('./*[local-name()="name"]/text()')
        if not name:
            continue
        ifname = name[0].strip()

        # AE config itself
        if ifname == ae_name:
            result["configured"] = True
            desc = ifl.xpath('./*[local-name()="description"]/text()')
            if desc:
                result["description"] = desc[0].strip()

            agg = ifl.xpath('./*[local-name()="aggregated-ether-options"]')
            if agg:
                if agg[0].xpath('./*[local-name()="lacp"]/*[local-name()="active"]'):
                    result["lacp_mode"] = "active"
                elif agg[0].xpath('./*[local-name()="lacp"]/*[local-name()="passive"]'):
                    result["lacp_mode"] = "passive"

        # physical members
        bundle = ifl.xpath('.//*[local-name()="ieee-802.3ad"]/*[local-name()="bundle"]/text()')
        if bundle and bundle[0].strip() == ae_name:
            result["members"].append(ifname)

    result["members"].sort()

    _cache_ae[key] = {"ts": now, "data": result}
    return result

def get_operational(dev):
    with connect(dev) as m:
        rpc = etree.XML('<get-interface-information><terse/></get-interface-information>')
        res = m.dispatch(rpc)
        ele = to_ele(res)
        oper = {}
        for phy in ele.xpath('//*[local-name()="physical-interface"]'):
            name_list  = phy.xpath('./*[local-name()="name"]/text()')
            admin_list = phy.xpath('./*[local-name()="admin-status"]/text()')
            oper_list  = phy.xpath('./*[local-name()="oper-status"]/text()')
            name   = name_list[0].strip()  if name_list  else None
            admin  = admin_list[0].strip() if admin_list else None
            oper_s = oper_list[0].strip()  if oper_list  else None
            if not name:
                continue
            oper[name] = {'admin_up': (admin == 'up'), 'oper_up': (oper_s == 'up')}
        return oper

# def get_interfaces_raw(dev):
#     """
#     Return merged config + oper with VC ports included.

#     Rules:
#     - VC ports use ONLY 'show virtual-chassis vc-port' for status
#     - No get-interface-information for VC
#     - VC ports are non-configurable
#     """

#     cfg_ele   = get_configuration(dev)
#     cfg_ports = parse_interfaces_config(cfg_ele)  # configured only
#     oper      = get_operational(dev)
#     vc_ports  = get_vc_ports_raw(dev)

#     print("VC PORTS RAW:", vc_ports)

#     cfg_map = {p["name"]: p for p in cfg_ports}
#     vc_map  = {p["name"]: p for p in vc_ports}

#     # -------------------------------------------------
#     # 1️⃣ FIRST: process configured interfaces
#     # -------------------------------------------------
#     for name, p in cfg_map.items():

#         # -------- VC PORT OVERRIDE --------
#         if name in vc_map:
#             vc = vc_map[name]

#             p["vc_port"]   = True
#             p["vc_status"] = vc["vc_status"]

#             # ✅ VC status is authoritative
#             p["oper_up"]  = (vc["vc_status"] == "Up")
#             p["admin_up"] = True

#             # 🔐 VC ports are non-configurable
#             p["bundle"]        = None
#             p["mode"]          = None
#             p["access_vlan"]   = None
#             p["trunk_vlans"]   = None
#             p["native_vlan"]   = None
#             p["configured"]    = False

#             continue  # 🚨 critical

#         # -------- NORMAL PORT --------
#         o = oper.get(name)
#         if o:
#             p["admin_up"] = o.get("admin_up")
#             p["oper_up"]  = o.get("oper_up")

#         p["vc_port"]   = False
#         p["vc_status"] = None

#     # -------------------------------------------------
#     # 2️⃣ ADD VC-ONLY PORTS (not in config)
#     # -------------------------------------------------
#     for vp in vc_ports:
#         name = vp["name"]
#         if name in cfg_map:
#             continue

#         # parse xe-<member>/<pic>/<port>
#         m = re.match(r'^(xe|ge)-(\d+)\/(\d+)\/(\d+)$', name)
#         if m:
#             scheme   = m.group(1)
#             member_i = int(m.group(2))
#             fpc_i    = int(m.group(3))
#             port_i   = int(m.group(4))
#         else:
#             scheme   = "xe"
#             member_i = 0
#             fpc_i    = 0
#             port_i   = 0

#         vc_up = (vp.get("vc_status") == "Up")

#         base = {
#             "name": name,
#             "member": member_i,
#             "fpc": fpc_i,
#             "type": scheme,
#             "aggregate": False,
#             "bundle": None,
#             "port": port_i,
#             "mode": None,
#             "access_vlan": None,
#             "trunk_vlans": None,
#             "native_vlan": None,
#             "poe": None,
#             "speed": None,
#             "duplex": None,
#             "admin_up": True,
#             "oper_up": vc_up,         # ✅ from VC
#             "configured": False,
#             "description": "VC Port",
#             "lacp_mode": None,
#             "vc_port": True,
#             "vc_status": vp.get("vc_status"),
#         }

#         cfg_ports.append(base)
#         cfg_map[name] = base

#         # -------------------------------------------------
#         # 3️⃣ ADD UNCONFIGURED BUT OPERATIONAL PORTS
#         # -------------------------------------------------
#         for ifname, o in oper.items():
#             if ifname in cfg_map:
#                 continue

#             m = re.match(r'^(ge|xe)-(\d+)\/(\d+)\/(\d+)$', ifname)
#             if m:
#                 scheme   = m.group(1)
#                 member_i = int(m.group(2))
#                 fpc_i    = int(m.group(3))
#                 port_i   = int(m.group(4))
#             else:
#                 continue

#             base = {
#                 "name": ifname,
#                 "member": member_i,
#                 "fpc": fpc_i,
#                 "type": scheme,
#                 "aggregate": False,
#                 "bundle": None,
#                 "port": port_i,
#                 "mode": None,
#                 "access_vlan": None,
#                 "trunk_vlans": None,
#                 "native_vlan": None,
#                 "poe": None,
#                 "speed": None,
#                 "duplex": None,
#                 "admin_up": o.get("admin_up"),
#                 "oper_up": o.get("oper_up"),
#                 "configured": False,
#                 "description": None,
#                 "lacp_mode": None,
#                 "vc_port": False,
#                 "vc_status": None,
#             }

#             cfg_ports.append(base)
#             cfg_map[ifname] = base

#     return cfg_ports

def get_interfaces_raw(dev):
    """
    Return merged list of interfaces — but *only*:
      - all configured interfaces (from configuration)
      - VC ports (from `show virtual-chassis vc-port`)
    This avoids returning the entire physical skeleton (48* members).
    """
    # accept either device-name or device-dict
    from .devices import get_device
    if isinstance(dev, str):
        dev_info = get_device(dev)
        dev_name = dev
    else:
        dev_info = dev
        dev_name = dev.get("name") if isinstance(dev, dict) else None

    # 1) configured interfaces from running config
    cfg_ele = get_configuration(dev_info)
    cfg_ports = parse_interfaces_config(cfg_ele)  # returns only configured iface entries

    # create map by name for quick overlay
    cfg_map = {p["name"]: p for p in cfg_ports}

    # 2) operational state (for configured interfaces)
    try:
        oper = get_operational(dev_info)
    except Exception:
        oper = {}

    for name, p in list(cfg_map.items()):
        # overlay oper state if available
        o = oper.get(name)
        if o:
            p["admin_up"] = o.get("admin_up")
            p["oper_up"] = o.get("oper_up")
        else:
            # keep conservative defaults from parse_interfaces_config
            p.setdefault("admin_up", True)
            p.setdefault("oper_up", False)

        # ensure VC fields default off
        p["vc_port"] = False
        p["vc_status"] = None
        p["_source"] = p.get("_source", "live")

    # 3) VC ports (authoritative for vc-status). These must be included even if not in config.
    try:
        vc_ports = get_vc_ports_raw(dev_info)
    except Exception:
        vc_ports = []

    # vc_map: name -> vc entry
    vc_map = {p["name"]: p for p in vc_ports}

    # If a configured interface matches a VC port: mark it as vc_port and treat status authoritative.
    for name, vc in vc_map.items():
        if name in cfg_map:
            p = cfg_map[name]
            p["vc_port"] = True
            p["vc_status"] = vc.get("vc_status")
            # VC status defines oper_up for these ports
            p["oper_up"] = (vc.get("vc_status") == "Up")
            p["admin_up"] = True
            # VC ports are non-configurable from UI perspective -> clear switching fields
            p["bundle"] = None
            p["mode"] = None
            p["access_vlan"] = None
            p["trunk_vlans"] = None
            p["native_vlan"] = None
            p["configured"] = False  # show as non-configurable in UI
            p["_source"] = "live"
        else:
            # VC-only port (not present in config): add a minimal record
            # parse interface name (we expect xe-<member>/<pic>/<port>)
            m = re.match(r'^(xe|ge)-(\d+)\/(\d+)\/(\d+)$', name)
            if m:
                scheme = m.group(1)
                member_i = int(m.group(2))
                fpc_i = int(m.group(3))
                port_i = int(m.group(4))
            else:
                scheme = "xe"; member_i = 0; fpc_i = 0; port_i = 0

            cfg_map[name] = {
                "name": name,
                "member": member_i,
                "fpc": fpc_i,
                "type": scheme,
                "aggregate": False,
                "bundle": None,
                "port": port_i,
                "mode": None,
                "access_vlan": None,
                "trunk_vlans": None,
                "native_vlan": None,
                "poe": None,
                "speed": None,
                "duplex": None,
                "admin_up": True,
                "oper_up": (vc.get("vc_status") == "Up"),
                "configured": False,
                "description": "VC Port",
                "lacp_mode": None,
                "vc_port": True,
                "vc_status": vc.get("vc_status"),
                "_source": "live"
            }

    # 4) Final list: only configured interfaces + VC-ports added above
    result = list(cfg_map.values())

    # Sort deterministic (optional — reuse your existing logic if desired)
    result.sort(key=lambda p: p.get("name", ""))

    return result

def get_vlans(dev):
    with connect(dev) as m:
        try:
            criteria = etree.XML('<configuration><vlans/></configuration>')
            reply = m.get_config(source='running', filter=('subtree', criteria))
        except Exception:
            reply = m.get_config(source='running')
        ele = to_ele(reply)
        vlans = []
        for v in ele.xpath('//*[local-name()="configuration"]/*[local-name()="vlans"]/*[local-name()="vlan"]'):
            name_list = v.xpath('./*[local-name()="name"]/text()')
            vid_list  = v.xpath('./*[local-name()="vlan-id"]/text()')
            name = name_list[0].strip() if name_list else None
            vid  = int(vid_list[0]) if vid_list else None
            if name:
                vlans.append({'name': name, 'id': vid})
        return vlans

def get_interface_live_raw(dev, if_name):
    print("LIVE RPC:", if_name)

    """Return detailed information for a single interface (talks to device)."""
    try:
        with connect(dev) as m:
            criteria = etree.XML(
                f'<configuration><interfaces><interface>'
                f'<name>{if_name}</name><unit/><ether-options/><aggregated-ether-options/>'
                f'</interface></interfaces></configuration>'
            )
            cfg = m.get_config(source='running', filter=('subtree', criteria))
            cfg_ele = to_ele(cfg)
            parsed = parse_interfaces_config(cfg_ele)
            info = parsed[0] if parsed else {'name': if_name}
            rpc = etree.XML(f'<get-interface-information><interface-name>{if_name}</interface-name><terse/></get-interface-information>')
            res = m.dispatch(rpc)
            ele = to_ele(res)
            phy_nodes = ele.xpath('.//*[local-name()="physical-interface"]')
            phy = phy_nodes[0] if phy_nodes else None
            if phy is not None:
                admin_list = phy.xpath('./*[local-name()="admin-status"]/text()')
                oper_list  = phy.xpath('./*[local-name()="oper-status"]/text()')
                admin = admin_list[0].strip() if admin_list else None
                oper_s = oper_list[0].strip() if oper_list else None
                info.update({'admin_up': admin == 'up', 'oper_up': oper_s == 'up'})
            # TODO: optionally gather byte counters via RPC <get-interface-statistics>
            info['configured'] = info.get('configured', False) or info.get('type') == 'ae'
            return info
    except Exception:
        oper = get_operational(dev)
        base = {'name': if_name, 'type': if_name.split('-',1)[0] if '-' in if_name else ('ae' if if_name.startswith('ae') else 'ge'),
                'aggregate': if_name.startswith('ae'), 'bundle': None,
                'member': 0, 'fpc': 0, 'port': 0,
                'mode': 'access', 'access_vlan': None, 'trunk_vlans': None, 'native_vlan': None,
                'poe': None, 'speed': None, 'duplex': None,
                'description': None}
        base.update(oper.get(if_name, {'admin_up': None, 'oper_up': None}))
        base['configured'] = False
        return base

# ---- CACHED WRAPPERS ----

def get_interfaces_cached(device: str, db):
    row = (
        db.query(InterfaceCache)
          .filter(InterfaceCache.device == device)
          .one_or_none()
    )

    if row:
        interfaces = row.data or []

        # ✅ NORMALISEER ouwe records
        for i in interfaces:
            i.setdefault("_source", "cache")

        return {
            "timestamp": row.updated_at.isoformat(),
            "interfaces": interfaces,
            "source": "cache"
        }

    # fallback live
    interfaces = get_interfaces_raw(device)
    for i in interfaces:
        i["_source"] = "live"

    store_interfaces_cache(db, device, interfaces)

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "interfaces": interfaces,
        "source": "live"
    }
def store_interfaces_cache(db, device: str, interfaces: list[dict]):
    """
    Overwrite interface cache for a device with fresh live data.
    This is the single source of truth after retrieve.
    """

    db.merge(
        InterfaceCache(
            device=device,
            data=interfaces,
            updated_at=datetime.utcnow()
        )
    )
    db.commit()


def get_interface_live_cached(dev_name, if_name):
    """Return dict of single interface with very short TTL."""
    
    # 🚀 AE FAST PATH (no live rpc)
    if if_name.startswith("ae"):
        return get_ae_summary_cached(dev_name, if_name)

    now = time.time()
    key = (dev_name, if_name)
    lock = _get_device_lock(dev_name)
    with lock:
        entry = _cache_live.get(key)
        if entry and (now - entry["ts"] < INTERFACE_LIVE_TTL):
            return entry["data"]
        data = get_interface_live_raw(dev_name, if_name)
        _cache_live[key] = {"ts": now, "data": data}
        return data


# Simple cache invalidation helper (call after commit)
def invalidate_device_cache(dev_name):
    with _CACHE_LOCK:
        _cache_interfaces.pop(dev_name, None)
        _cache_ae_keys = [k for k in _cache_ae if k[0] == dev_name]
        for k in _cache_ae_keys:
            _cache_ae.pop(k, None)
        keys = [k for k in _cache_live.keys() if k[0] == dev_name]
        for k in keys:
            _cache_live.pop(k, None)

# Example commit_changes placeholder (safe pattern: candidate + confirmed)
def commit_changes(dev, interfaces, config):
    """
    Apply a config change using candidate + confirm pattern.
    This function is intentionally conservative; adapt XML templates for your environment.
    After successful commit or in any exception, it invalidates cache.
    """
    if isinstance(dev, str):
        from .devices import get_device
        dev = get_device(dev)
    with connect(dev) as m:
        m.lock('candidate')
        try:
            # Build your XML 'config' snippet here according to 'config' dict
            # WARNING: modify appropriately for your production templates
            template = '<configuration><interfaces/></configuration>'
            m.edit_config(target='candidate', config=template)
            m.commit()
        finally:
            try:
                m.unlock('candidate')
            except Exception:
                pass
    # invalidate cache so frontend sees changes after commit
    if isinstance(dev, dict):
        devname = dev.get("name")
    else:
        # if dev is device dict without name, just clear all caches (safe)
        devname = None
    if devname:
        invalidate_device_cache(devname)
    else:
        # best effort: clear whole cache
        with _CACHE_LOCK:
            _cache_interfaces.clear()
            _cache_live.clear()

def apply_interface_config(mgr, interface: str, config: dict):
    """
    Apply configuration by sending a proper <config><configuration>... XML snippet.
    - Uses candidate + commit (no confirm)
    - Increases mgr.timeout to avoid RPC timeout during commit
    - Escapes user-provided strings
    """
    if config.get("vc_port"):
        raise ValueError("VC port configuration is not allowed")

    # Build interface XML
    desc = config.get("description")
    mode = config.get("mode")
    access = config.get("access_vlan")
    trunk = config.get("trunk_vlans") or []
    native = config.get("native_vlan")

    # Basic validation
    if mode not in ("access", "trunk"):
        raise ValueError("mode must be 'access' or 'trunk'")

    # escape text fields
    def esc(v):
        return sax.escape(str(v)) if v is not None else None

    desc_xml = f"<description>{esc(desc)}</description>" if desc else ""
    # build vlan members XML (multiple <members> entries)
    vlan_members_xml = ""
    if mode == "access" and access:
        vlan_members_xml = f"<vlan><members>{esc(access)}</members></vlan>"
    elif mode == "trunk" and trunk:
        # trunk: multiple members entries under vlan
        members = "".join(f"<members>{esc(v)}</members>" for v in trunk)
        vlan_members_xml = f"<vlan>{members}</vlan>"

    native_xml = f"<native-vlan-id>{esc(native)}</native-vlan-id>" if native else ""

    # assemble interface XML
    interface_xml = f"""
    <interfaces>
      <interface>
        <name>{esc(interface)}</name>
        {desc_xml}
        <unit>
          <name>0</name>
          <family>
            <ethernet-switching>
              <interface-mode>{esc(mode)}</interface-mode>
              {vlan_members_xml}
              {native_xml}
            </ethernet-switching>
          </family>
        </unit>
      </interface>
    </interfaces>
    """

    # full config wrapper
    xml_payload = f"<config><configuration>{interface_xml}</configuration></config>"

    # execute: use candidate then commit (increase timeout)
    prev_timeout = getattr(mgr, "timeout", None)
    try:
        mgr.timeout = 120  # 2 minutes
        try:
            mgr.lock("candidate")
        except Exception:
            # best-effort (some boxes don't support candidate)
            pass

        # edit-config with full <config> wrapper; use 'merge' to merge with existing config
        mgr.edit_config(target="candidate", config=xml_payload, default_operation="merge")

        # commit (hard commit)
        mgr.commit()
    except Exception as e:
        # raise a helpful error message upwards
        raise RuntimeError(f"NETCONF apply failed: {e}")
    finally:
        try:
            mgr.unlock("candidate")
        except Exception:
            pass
        if prev_timeout is not None:
            mgr.timeout = prev_timeout


def get_vc_ports_raw(dev):
    """
    Parse 'show virtual-chassis vc-port | display xml'
    Return: [{"name": "xe-0/2/2", "vc_status": "Up"}, ...]
    """
    with connect(dev) as m:
        rpc = etree.XML("""
            <command format="xml">
                show virtual-chassis vc-port
            </command>
        """)
        res = m.rpc(rpc)
        return parse_vc_ports_xml(res)
    
def get_rollback_list(dev):
    with connect(dev) as m:
        rpc = etree.XML('<command format="text">show system commit</command>')
        res = m.rpc(rpc)
        ele = etree.fromstring(str(res).encode())
        return ele.xpath('string(//*[local-name()="output"])').strip()

def get_rollback_diff(dev, idx: int):
    with connect(dev) as m:
        try:
            rpc = etree.XML(f"""
                <command format="text">
                    show system rollback compare {idx} 0
                </command>
            """)

            res = m.rpc(rpc)

            #
            # --- Normalize RPCReply into an XML element ---
            #
            if hasattr(res, "data") and res.data is not None:
                # Preferred
                root = res.data
            elif hasattr(res, "element") and res.element is not None:
                root = res.element
            elif hasattr(res, "xml"):
                # Parse XML string
                root = etree.fromstring(res.xml.encode())
            elif isinstance(res, etree._Element):
                # Already an XML element
                root = res
            else:
                raise RuntimeError("Unable to parse NETCONF RPCReply into XML")

            #
            # --- Extract <output> text from Junos command RPC ---
            #
            output = root.find(".//output")
            if output is not None and output.text:
                txt = output.text.strip()
            else:
                txt = root.xpath("string()").strip()

            # 3. Remove stupid extra quotes added by Junos RPC wrapper
            if (txt.startswith('"') and txt.endswith('"')):
                txt = txt[1:-1]

            return txt
        
        except Exception as e:
            raise RuntimeError(f"Rollback diff failed: {e}")

def apply_rollback(nc, idx: int):
    # 1. Load rollback config
    rpc_load = etree.XML(f"<load-configuration rollback=\"{idx}\" />")
    nc.rpc(rpc_load)

    # 2. Commit properly via NETCONF (NOT command RPC)
    commit_rpc = etree.XML("""
        <commit>
            <synchronize/>
        </commit>
    """)
    res = nc.rpc(commit_rpc)

    # 3. Return RPC output
    return res.xpath("string()").strip()

def parse_vc_ports_xml(res):
    ele = to_ele(res)
    ports = []

    for item in ele.xpath('.//*[local-name()="multi-routing-engine-item"]'):
        # re-name = "fpc0", "fpc1"
        re_name = item.xpath('./*[local-name()="re-name"]/text()')
        if not re_name:
            continue

        m = re.match(r'fpc(\d+)', re_name[0])
        if not m:
            continue

        vc_member = int(m.group(1))     # correct VC member

        # parse ports
        for p in item.xpath('.//*[local-name()="port-information"]'):
            pname = p.xpath('./*[local-name()="port-name"]/text()')
            status = p.xpath('./*[local-name()="port-status"]/text()')

            if not pname:
                continue

            # port-name looks like: "2/2"
            try:
                pic, port = pname[0].split('/')
                pic_i = int(pic)
                port_i = int(port)
            except ValueError:
                continue

            # ❌ Skip PIC 1 (QSFP+)
            if pic_i == 1:
                continue

            # Build Juniper-style interface name
            iface = f"xe-{vc_member}/{pic_i}/{port_i}"

            ports.append({
                "name": iface,
                "vc_status": status[0] if status else None
            })

    return ports
