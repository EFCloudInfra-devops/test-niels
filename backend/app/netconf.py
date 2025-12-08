
from ncclient import manager
from lxml import etree
from typing import Dict, Any, List, Tuple
from .config import settings, Device

# --- helper: normalize reply to lxml Element ---
def to_ele(reply):
    if hasattr(reply, 'data_ele') and reply.data_ele is not None:
        return reply.data_ele
    xml = getattr(reply, 'xml', None)
    if xml:
        root = etree.fromstring(xml.encode() if isinstance(xml, str) else xml)
        data = root.find('.//*[local-name()="data"]')
        return data if data is not None else root
    return reply


def connect(dev: Device) -> manager.Manager:
    return manager.connect(
        host=dev.mgmt_ip,
        port=830,
        username=settings.netconf_username,
        password=settings.netconf_password,
        hostkey_verify=False,
        allow_agent=False,
        look_for_keys=False,
        timeout=20,
        device_params={'name':'junos'}
    )


def get_configuration(dev: Device):
    with connect(dev) as m:
        try:
            criteria = etree.XML('<configuration><interfaces/></configuration>')
            reply = m.get_config(source='running', filter=('subtree', criteria))
            return to_ele(reply)
        except Exception:
            reply = m.get_config(source='running')
            return to_ele(reply)


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
                im_list = esw.xpath('./*[local-name()="port-mode"]/text()')  # fallback
            if im_list:
                mode = im_list[0].strip()            
            members = esw.xpath('./*[local-name()="vlan"]/*[local-name()="members"]/text()')
            members = [m.strip() for m in members if m and m.strip()]
            if mode == 'access' and members:
                access_vlan = members[0]
            elif mode == 'trunk' and members:
                trunk_vlans = members   # list of VLAN names
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


def get_operational(dev: Device) -> Dict[str, Dict[str, Any]]:
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


def get_vlans(dev: Device):
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


def get_interface_live(dev: Device, if_name: str) -> Dict[str, Any]:
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
            # Mark configured: if we had a unit or it's an ae*
            info['configured'] = info.get('configured', False) or info['type'] == 'ae'
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


def commit_changes(dev: Device, interfaces: List[str], cfg: Dict[str, Any]) -> Dict[str, Any]:
    # Stub: return ok; wire actual commit later
    return {'ok': True}
