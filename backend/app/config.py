
from pydantic import BaseModel
import os

class Settings(BaseModel):
    device_name: str = os.getenv('DEVICE_NAME', 'BRB2-ACCESS-SW01')
    mgmt_ip: str = os.getenv('MGMT_IP', '10.22.0.11')
    netconf_username: str = os.getenv('NETCONF_USERNAME', 'netconf_automation')
    netconf_key_path: str = os.getenv('NETCONF_KEY_PATH', '/app/keys/id_rsa')
    sync_interval_seconds: int = int(os.getenv('SYNC_INTERVAL_SECONDS', '180'))

settings = Settings()
