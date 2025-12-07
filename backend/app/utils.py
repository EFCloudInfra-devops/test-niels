
from typing import List
from .schemas import InterfaceConfig

def validate_config(config: InterfaceConfig) -> List[str]:
    errors = []
    # VLAN checks
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
    # PoE rules
    if config.type == 'xe' and config.poe:
        errors.append('PoE is niet toegestaan op SFP+ (xe) poorten.')
    # Speed/duplex per type
    if config.type == 'ge':
        allowed = {'auto','10m','100m','1g'}
        if config.speed and config.speed not in allowed:
            errors.append('Ongeldige snelheid voor ge-poort.')
    if config.type == 'xe':
        allowed = {'10g','auto'}
        if config.speed and config.speed not in allowed:
            errors.append('Ongeldige snelheid voor xe-poort.')
    if config.duplex and config.duplex not in {'auto','full'}:
        errors.append('Ongeldige duplex waarde.')
    return errors
