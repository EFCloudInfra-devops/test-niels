EX4300-48P deployment package

Instructions:
1) Place a JSON inventory file (devices.json) alongside backend/ or set NETCONF_DEVICES_JSON env var pointing to it.
   Example devices.json:
   {
     "switch01": { "host": "192.0.2.10", "username": "netconf", "password": "secret" }
   }

2) Build with Docker: docker compose up --build
   or run directly: pip install -r backend/requirements.txt && uvicorn backend.main:app --reload

Notes:
- VLAN names supported as strings (e.g. v504, v503)
- PoE per-port supported via /api/device/{device}/poe
- commit_changes uses safe candidate+confirmed commit; adapt XML templates as needed
