# /app/backend/app/netconf.py
import os
import json
import time
import threading, re
from ncclient import manager
from ncclient.xml_ import to_ele
from lxml import etree
from datetime import datetime
from .models import InterfaceCache

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
        configured = (unit0 is not None) or aggregate
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

def get_interfaces_raw(dev):
    """
    Return merged config + oper with VC ports included.

    Rules:
    - VC ports use ONLY 'show virtual-chassis vc-port' for status
    - No get-interface-information for VC
    - VC ports are non-configurable
    """

    cfg_ele   = get_configuration(dev)
    cfg_ports = parse_interfaces_config(cfg_ele)  # configured only
    oper      = get_operational(dev)
    vc_ports  = get_vc_ports_raw(dev)

    print("VC PORTS RAW:", vc_ports)

    cfg_map = {p["name"]: p for p in cfg_ports}
    vc_map  = {p["name"]: p for p in vc_ports}

    # -------------------------------------------------
    # 1️⃣ FIRST: process configured interfaces
    # -------------------------------------------------
    for name, p in cfg_map.items():

        # -------- VC PORT OVERRIDE --------
        if name in vc_map:
            vc = vc_map[name]

            p["vc_port"]   = True
            p["vc_status"] = vc["vc_status"]

            # ✅ VC status is authoritative
            p["oper_up"]  = (vc["vc_status"] == "Up")
            p["admin_up"] = True

            # 🔐 VC ports are non-configurable
            p["bundle"]        = None
            p["mode"]          = None
            p["access_vlan"]   = None
            p["trunk_vlans"]   = None
            p["native_vlan"]   = None
            p["configured"]    = False

            continue  # 🚨 critical

        # -------- NORMAL PORT --------
        o = oper.get(name)
        if o:
            p["admin_up"] = o.get("admin_up")
            p["oper_up"]  = o.get("oper_up")

        p["vc_port"]   = False
        p["vc_status"] = None

    # -------------------------------------------------
    # 2️⃣ ADD VC-ONLY PORTS (not in config)
    # -------------------------------------------------
    for vp in vc_ports:
        name = vp["name"]
        if name in cfg_map:
            continue

        # parse xe-<member>/<pic>/<port>
        m = re.match(r'^(xe|ge)-(\d+)\/(\d+)\/(\d+)$', name)
        if m:
            scheme   = m.group(1)
            member_i = int(m.group(2))
            fpc_i    = int(m.group(3))
            port_i   = int(m.group(4))
        else:
            scheme   = "xe"
            member_i = 0
            fpc_i    = 0
            port_i   = 0

        vc_up = (vp.get("vc_status") == "Up")

        base = {
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
            "oper_up": vc_up,         # ✅ from VC
            "configured": False,
            "description": "VC Port",
            "lacp_mode": None,
            "vc_port": True,
            "vc_status": vp.get("vc_status"),
        }

        cfg_ports.append(base)
        cfg_map[name] = base

        # -------------------------------------------------
        # 3️⃣ ADD UNCONFIGURED BUT OPERATIONAL PORTS
        # -------------------------------------------------
        for ifname, o in oper.items():
            if ifname in cfg_map:
                continue

            m = re.match(r'^(ge|xe)-(\d+)\/(\d+)\/(\d+)$', ifname)
            if m:
                scheme   = m.group(1)
                member_i = int(m.group(2))
                fpc_i    = int(m.group(3))
                port_i   = int(m.group(4))
            else:
                continue

            base = {
                "name": ifname,
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
                "admin_up": o.get("admin_up"),
                "oper_up": o.get("oper_up"),
                "configured": False,
                "description": None,
                "lacp_mode": None,
                "vc_port": False,
                "vc_status": None,
            }

            cfg_ports.append(base)
            cfg_map[ifname] = base

    return cfg_ports

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
        return {
            "timestamp": row.updated_at.isoformat(),
            "interfaces": row.data
        }

    # fallback: live ophalen
    interfaces = get_interfaces_raw(device)
    store_interfaces_cache(db, device, interfaces)

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "interfaces": interfaces,
        "source": "cache"  # of "live"
    }

def store_interfaces_cache(db, device: str, interfaces: list):
    row = (
        db.query(InterfaceCache)
          .filter(InterfaceCache.device == device)
          .one_or_none()
    )

    if not row:
        row = InterfaceCache(device=device)
        db.add(row)

    row.data = interfaces
    row.updated_at = datetime.utcnow()
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
            # commit confirmed for safety
            m.commit(confirm='30')
            # final commit once validated
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
    Apply config using candidate pattern against an ncclient manager.Manager instance.
    mgr: ncclient.manager.Manager
    interface: name (e.g. ge-0/0/1)
    config: dict like { mode: "access", access_vlan: "v200" } or trunk config
    """
    if config.get("vc_port"):
        raise ValueError("VC port configuration is not allowed")

    # build set commands
    cmds = []
    if config["mode"] == "access":
        cmds.append(f"set interfaces {interface} unit 0 family ethernet-switching interface-mode access")
        if config.get("access_vlan"):
            cmds.append(f"set interfaces {interface} unit 0 family ethernet-switching vlan members {config['access_vlan']}")
    elif config["mode"] == "trunk":
        cmds.append(f"set interfaces {interface} unit 0 family ethernet-switching interface-mode trunk")
        for v in config.get("trunk_vlans", []):
            cmds.append(f"set interfaces {interface} unit 0 family ethernet-switching vlan members {v}")
        if config.get("native_vlan"):
            cmds.append(f"set interfaces {interface} native-vlan-id {config['native_vlan']}")

    if not cmds:
        raise ValueError("No configuration commands generated")

    # candidate pattern: lock candidate, edit_config target=candidate, commit confirm+final
    try:
        mgr.lock('candidate')
    except Exception:
        # best-effort continue (some devices may not support candidate)
        pass

    try:
        set_cfg = "\n".join(cmds)
        # use edit-config on candidate
        mgr.edit_config(target='candidate', config=f"<configuration>\n{set_cfg}\n</configuration>", default_operation='merge')
        # commit confirmed then final commit
        mgr.commit(confirm='30')
        mgr.commit()
    finally:
        try:
            mgr.unlock('candidate')
        except Exception:
            pass
        
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

def parse_vc_ports_xml(res):
    ele = to_ele(res)
    ports = []

    for item in ele.xpath('.//*[local-name()="multi-routing-engine-item"]'):
        # fpc number from <re-name>fpc0</re-name>
        re_name = item.xpath('./*[local-name()="re-name"]/text()')
        if not re_name:
            continue

        m = re.match(r'fpc(\d+)', re_name[0])
        if not m:
            continue
        member = int(m.group(1))

        # walk port-information entries
        for p in item.xpath('.//*[local-name()="port-information"]'):
            pname = p.xpath('./*[local-name()="port-name"]/text()')
            status = p.xpath('./*[local-name()="port-status"]/text()')

            if not pname:
                continue

            pic, port = pname[0].split('/')
            iface = f"xe-{member}/{pic}/{port}"

            ports.append({
                "name": iface,
                "vc_status": status[0] if status else None
            })

    return ports

