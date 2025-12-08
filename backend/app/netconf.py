from ncclient import manager
from lxml import etree
from typing import Dict, Any, List, Tuple
from .config import settings, Device

def connect(dev: Device) -> manager.Manager:
    return manager.connect(
        host=dev.mgmt_ip,
        port=830,
        username=settings.netconf_username,
        password=settings.netconf_password,
        hostkey_verify=False,
        allow_agent=False,
        look_for_keys=False,
        timeout=30,
        device_params={'name':'junos'}
    )

def get_configuration(dev: Device) -> etree._Element:
    with connect(dev) as m:
        criteria = etree.XML('''
        <configuration>
          <interfaces/>
          <vlans/>
          <chassis>
            <poe/>
          </chassis>
          <virtual-chassis/>
        </configuration>
        ''')
        reply = m.get_config(source='running', filter=('subtree', criteria))
        return reply.data_ele

def parse_interfaces_config(cfg_ele: etree._Element) -> List[Dict[str, Any]]:
    interfaces = []
    for ifl in cfg_ele.xpath('//configuration/interfaces/interface'):
        name = ifl.findtext('name')
        if not name:
            continue
        try:
            scheme, rest = name.split('-', 1)
            member, fpc, port = rest.split('/')
            member_i = int(member)
            fpc_i = int(fpc)
            port_i = int(port)
        except Exception:
            continue
        mode = 'access'
        access_vlan = None
        trunk_vlans = []
        native_vlan = None
        unit0 = ifl.find('unit')
        if unit0 is not None:
            family = unit0.find('family')
            if family is not None:
                esw = family.find('ethernet-switching')
                if esw is not None:
                    pm = esw.find('port-mode')
                    if pm is not None and pm.text:
                        mode = pm.text
                    members = esw.findall('vlan/members')
                    if members:
                        for mem in members:
                            try:
                                vid = int(mem.text)
                                trunk_vlans.append(vid)
                            except:
                                pass
                    if mode == 'access' and trunk_vlans:
                        access_vlan = trunk_vlans[0]
                        trunk_vlans = []
                    nvid = esw.find('native-vlan-id')
                    if nvid is not None and nvid.text:
                        try:
                            native_vlan = int(nvid.text)
                        except:
                            pass
        poe = None
        speed = None
        duplex = None
        eo = ifl.find('ether-options')
        if eo is not None:
            sp = eo.find('speed')
            if sp is not None and sp.text:
                speed = sp.text
            nd = eo.find('no-auto-negotiation')
            if nd is not None:
                duplex = 'full'
        interfaces.append({
            'name': name,
            'member': member_i,
            'fpc': fpc_i,
            'type': scheme,
            'port': port_i,
            'mode': mode,
            'access_vlan': access_vlan,
            'trunk_vlans': trunk_vlans or None,
            'native_vlan': native_vlan,
            'poe': poe,
            'speed': speed,
            'duplex': duplex,
            'admin_up': True,
            'oper_up': False,
        })
    return interfaces

def get_operational(dev: Device) -> Dict[str, Dict[str, Any]]:
    with connect(dev) as m:
        rpc = etree.XML('<get-interface-information><terse/></get-interface-information>')
        res = m.dispatch(rpc)
        oper = {}
        for phy in res.data_ele.xpath('//physical-interface'):
            name = phy.findtext('name')
            if not name:
                continue
            admin = phy.findtext('admin-status')
            oper_s = phy.findtext('oper-status')
            oper[name] = {
                'admin_up': (admin == 'up'),
                'oper_up': (oper_s == 'up')
            }
        try:
            rpc_poe = etree.XML('<get-power-over-ethernet-information/>')
            poe_res = m.dispatch(rpc_poe)
            for port in poe_res.data_ele.xpath('//poe-interface-information/poe-interface'):
                iname = port.findtext('interface-name')
                if iname:
                    poe_enabled = port.findtext('interface-power-mode')
                    poe_on = (poe_enabled == 'on')
                    oper.setdefault(iname, {}).update({'poe': poe_on})
        except Exception:
            pass
        return oper

def get_vc_roles(dev: Device) -> Tuple[List[str], str | None]:
    roles = []
    mode = None
    with connect(dev) as m:
        try:
            rpc = etree.XML('<get-virtual-chassis-information/>')
            res = m.dispatch(rpc)
            mode = res.data_ele.findtext('.//virtual-chassis-mode')
            for mem in res.data_ele.xpath('//member-info'):
                role = mem.findtext('member-role') or 'linecard'
                roles.append(role)
        except Exception:
            roles = ['unknown','unknown']
    return roles, mode

def get_interface_live(dev: Device, if_name: str) -> Dict[str, Any]:
    with connect(dev) as m:
        criteria = etree.XML(f'''
        <configuration>
          <interfaces>
            <interface>
              <name>{if_name}</name>
              <unit/>
              <ether-options/>
            </interface>
          </interfaces>
          <chassis><poe/></chassis>
        </configuration>
        ''')
        cfg = m.get_config(source='running', filter=('subtree', criteria))
        cfg_ele = cfg.data_ele
        parsed = parse_interfaces_config(cfg_ele)
        info = parsed[0] if parsed else {'name': if_name}
        rpc = etree.XML(f'<get-interface-information><interface-name>{if_name}</interface-name><terse/></get-interface-information>')
        res = m.dispatch(rpc)
        phy = res.data_ele.find('.//physical-interface')
        if phy is not None:
            admin = phy.findtext('admin-status')
            oper_s = phy.findtext('oper-status')
            info.update({'admin_up': admin == 'up', 'oper_up': oper_s == 'up'})
        return info
