from typing import List
from .schemas import InterfaceConfig

GE_SPEEDS = {'auto','10m','100m','1g'}
XE_SPEEDS = {'10g'}

def validate_config(config: InterfaceConfig) -> List[str]:
    errors = []
    if config.mode == 'access':
        if config.access_vlan is None:
            errors.append('Access mode vereist access_vlan.')
        elif not (1 <= config.access_vlan <= 4094):
            errors.append('access_vlan buiten bereik (1–4094).')
    elif config.mode == 'trunk':
        if not config.trunk_vlans:
            errors.append('Trunk mode vereist trunk_vlans lijst.')
        else:
            for vid in config.trunk_vlans:
                if not (1 <= vid <= 4094):
                    errors.append(f'trunk VLAN {vid} buiten bereik.')
        if config.native_vlan is not None and not (1 <= config.native_vlan <= 4094):
            errors.append('native_vlan buiten bereik (1–4094).')
    if config.type == 'xe' and config.fpc == 2:
        if config.mode != 'trunk':
            errors.append('Uplink policy: xe-*/2/* moet trunk zijn.')
        if config.speed and config.speed != '10g':
            errors.append('Uplink policy: xe-*/2/* snelheid verplicht 10g.')
    if config.type == 'xe' and config.poe:
        errors.append('PoE is niet toegestaan op SFP+ (xe).')
    if config.type == 'ge':
        if config.speed and config.speed not in GE_SPEEDS:
            errors.append('Ongeldige snelheid voor ge-poort.')
    if config.type == 'xe':
        if config.speed and config.speed not in XE_SPEEDS:
            errors.append('Ongeldige snelheid voor xe-poort (alleen 10g).')
    if config.duplex and config.duplex not in {'auto','full'}:
        errors.append('Ongeldige duplex waarde.')
    return errors
