# Juniper EX4300 Visual Port UI (VC-aware) — Backend/Frontend (v2)

Toevoegingen:
- VC‑member detectie + role in UI.
- Aanpasbare polling interval.
- Multi‑device inventory (3 VC's): BRB2-ACCESS-SW01, GRN1-ACCESS-SW01, GRN1-ACCESS-SW02.
- Validatie: uplink policy (xe-*/2/* trunk & 10G), basis speed/duplex matrix.
- NETCONF password auth.
- Echte diff via `show | compare`.
- Rollback endpoint (rollback 1).

Start: pas `backend/.env` en `backend/inventory.json`, dan `docker compose up -d --build`.
