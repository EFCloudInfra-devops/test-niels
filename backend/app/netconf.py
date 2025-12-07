
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
        timeout=30
    )


def get_configuration(dev: Device) -> etree._Element:
    with connect(dev) as m:
        filter_cfg = etree.XML("""
        <filter>
          <configuration>
            <interfaces/>
            <vlans/>
            <chassis>
              <poe/>
            </chassis>
            <virtual-chassis/>
          </configuration>
        </filter>
        """)
        reply = m.get_config(source='running', filter=filter_cfg)
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


def get_vc_roles(dev: Device):
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


def build_edit_config(if_name: str, cfg: Dict[str, Any]) -> etree._Element:
    root = etree.Element('config')
    configuration = etree.SubElement(root, 'configuration')
    interfaces = etree.SubElement(configuration, 'interfaces')
    interface = etree.SubElement(interfaces, 'interface')
    etree.SubElement(interface, 'name').text = if_name
    unit = etree.SubElement(interface, 'unit')
    etree.SubElement(unit, 'name').text = '0'
    family = etree.SubElement(unit, 'family')
    esw = etree.SubElement(family, 'ethernet-switching')
    etree.SubElement(esw, 'port-mode').text = cfg.get('mode','access')
    vlan = etree.SubElement(esw, 'vlan')
    if cfg.get('mode') == 'access' and cfg.get('access_vlan'):
        mem = etree.SubElement(vlan, 'members')
        mem.text = str(cfg['access_vlan'])
    elif cfg.get('mode') == 'trunk' and cfg.get('trunk_vlans'):
        for vid in cfg['trunk_vlans']:
            mem = etree.SubElement(vlan, 'members')
            mem.text = str(vid)
        if cfg.get('native_vlan'):
            etree.SubElement(esw, 'native-vlan-id').text = str(cfg['native_vlan'])
    if cfg.get('poe') is not None:
        chassis = etree.SubElement(configuration, 'chassis')
        poe = etree.SubElement(chassis, 'poe')
        iface = etree.SubElement(poe, 'interface')
        etree.SubElement(iface, 'name').text = if_name
        if not cfg['poe']:
            etree.SubElement(iface, 'disable')
    if cfg.get('speed') or cfg.get('duplex'):
        eo = etree.SubElement(interface, 'ether-options')
        if cfg.get('speed'):
            etree.SubElement(eo, 'speed').text = cfg['speed']
        if cfg.get('duplex') == 'full':
            etree.SubElement(eo, 'no-auto-negotiation')
    return root


def show_compare(m) -> str:
    try:
        cmd = etree.XML('<command>show | compare</command>')
        res = m.dispatch(cmd)
        return ''.join(res.xpath('//output/text()')) or (res.xml if hasattr(res, 'xml') else '')
    except Exception as e:
        return f'compare failed: {e}'


def commit_bulk(dev: Device, device_changes: List[Dict[str, Any]]):
    with connect(dev) as m:
        m.lock()
        try:
            pre = m.get_config(source='running').data_xml
            for item in device_changes:
                edit = build_edit_config(item['interface'], item['config'])
                m.edit_config(target='candidate', config=etree.tostring(edit).decode())
            diff = show_compare(m)
            m.commit()
            post = m.get_config(source='running').data_xml
            return {'ok': True, 'pre': pre, 'post': post, 'diff': diff}
        except Exception as e:
            try:
                m.discard_changes()
            except Exception:
                pass
            return {'ok': False, 'error': str(e)}
        finally:
            try:
                m.unlock()
            except Exception:
                pass


def rollback(dev: Device, level: int = 1):
    with connect(dev) as m:
        try:
            rpc = etree.XML(f'<load-configuration><rollback>{level}</rollback></load-configuration>')
            m.dispatch(rpc)
            diff = show_compare(m)
            m.commit()
            return {'ok': True, 'diff': diff}
        except Exception as e:
            try:
                m.discard_changes()
            except Exception:
                pass
            return {'ok': False, 'error': str(e)}
