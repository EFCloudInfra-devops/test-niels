
from typing import List, Union
from .schemas import InterfaceConfig

def _is_vlan_ok(v: Union[int, str, None]) -> bool:
    if v is None: return False
    if isinstance(v, int): return 1 <= v <= 4094
    if isinstance(v, str): return len(v.strip()) > 0
    return False

def validate_config(config: InterfaceConfig) -> List[str]:
    errors = []
    if config.mode == 'access':
        if not _is_vlan_ok(config.access_vlan):
            errors.append('Access mode requires a valid access_vlan (ID 1–4094 or name).')
    elif config.mode == 'trunk':
        if not config.trunk_vlans:
            errors.append('Trunk mode requires trunk_vlans list.')
        else:
            for v in config.trunk_vlans:
                if not _is_vlan_ok(v):
                    errors.append(f'Invalid trunk VLAN: {v}')
        if config.native_vlan is not None and not _is_vlan_ok(config.native_vlan):
            errors.append('Invalid native_vlan (ID 1–4094 or name).')
    return errors
