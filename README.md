
# Juniper EX4300 Visual Port UI (VC-aware) — Mini Backend + Frontend

Dit pakket levert een minimale, docker-compose gebaseerde oplossing om een EX4300-48P (met Virtual Chassis) visueel weer te geven,
poorten aan te passen (VLAN, access/trunk, PoE, speed/duplex), status te poll'en, bulkselecties te doen en audit/rollback te loggen via NETCONF.

**Scope voor nu**: 1 device `BRB2-ACCESS-SW01` (VC met 2 leden), mgmt IP `10.22.0.11`. JUNOS 21.4R3-S11.3.

## Overzicht
- Frontend (Nginx, statisch): CSS/JS port-grid voor 48x RJ-45 + 4x SFP+ per VC-lid; klikbare poorten met modals.
- Backend (FastAPI, ncclient): NETCONF candidate/commit, validaties, audit logging (SQLite), periodieke config-sync voor desired vs actual.
- Docker Compose: start `backend` en `frontend`. 

## Snel starten
1. **SSH keys** (zie onderaan voor JUNOS-setup): Zet je private key op je host en map deze in de backend container.
   Plaats b.v. `id_rsa` in `./secrets/id_rsa` en zorg dat de bijbehorende public key op de switch is geconfigureerd.

2. Pas `.env` aan indien nodig (default bevat `BRB2-ACCESS-SW01`).

3. Start:
   ```bash
   docker compose up -d --build
   ```

   - Frontend: http://localhost:8080
   - Backend:  http://localhost:8000/docs (OpenAPI)

## Functionaliteit
- **Visualisatie**: Ports per VC-lid:
  - RJ-45: ge-<member>/0/0..47 (klikbaar)
  - SFP+:  xe-<member>/2/0..3  (klikbaar; PoE verboden)
- **Modals**: VLAN, mode (access/trunk), trunk members/native VLAN, PoE (RJ-45 only), speed/duplex.
- **Validatie**: VLAN range 1–4094, geen PoE op SFP, speed/duplex per type.
- **Bulk**: Selecteer meerdere poorten in de UI; server voert per device een NETCONF transactie uit (lock→edit-config→commit of discard).
- **Polling/Sync**: Periodieke sync haalt echte device-config op en vergelijkt met desired state; UI toont drift indicator.
- **Audit**: Logt user (via header of UI field), device, interface(s), requested change, status, en simpele diff snapshot.
- **Rollback**: Bewaart pre-config snapshot; bij failure doet discard-changes of rollback 1.

## Omgevingsvariabelen (`backend/.env`)
- `DEVICE_NAME=BRB2-ACCESS-SW01`
- `MGMT_IP=10.22.0.11`
- `NETCONF_USERNAME=netconf_automation`
- `NETCONF_KEY_PATH=/app/keys/id_rsa`  # gemapt via docker-compose
- `SYNC_INTERVAL_SECONDS=180`          # periodieke actual sync

## SSH key + NETCONF op JUNOS (EX4300)
1. **NETCONF via SSH inschakelen**
   ```
   set system services netconf ssh
   set system services ssh protocol-version v2
   set system services ssh root-login prohibit-password
   ```
2. **User met public key (voorbeeld)**
   ```
   set system login user netconf_automation class super-user
   set system login user netconf_automation authentication ssh-rsa "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ... user@host"
   commit
   ```
3. **(Optioneel) PoE configuratie per interface**
   - PoE enable/disable: `set chassis poe interface ge-0/0/10 disable` (of zonder `disable` voor enable)
   - Status check (CLI): `show poe interface`, `show poe controller`

> Let op: interface namen volgen VC-indices (member 0 en 1): `ge-0/0/x` en `xe-0/2/x` volgens jouw naming.

## Bekende constraints
- RJ-45 ge-ports: 10/100/1000, duplex auto of full; autoneg op SFP niet van toepassing.
- SFP+ xe-ports: 10G; PoE **nooit** toegestaan.
- VLAN IDs 1–4094; `access` — één VLAN; `trunk` — members lijst en (optioneel) native-vlan-id.

## Structuur
```
juniper-switch-ui/
├─ docker-compose.yml
├─ README.md
├─ backend/
│  ├─ Dockerfile
│  ├─ requirements.txt
│  ├─ .env
│  └─ app/
│     ├─ main.py
│     ├─ netconf.py
│     ├─ models.py
│     ├─ schemas.py
│     ├─ config.py
│     ├─ utils.py
│     └─ __init__.py
└─ frontend/
   ├─ Dockerfile
   └─ public/
      ├─ index.html
      ├─ styles.css
      └─ app.js
```

## Gebruik
- Klik op poorten om de modal te openen. 
- Bulkselectie: activeer in UI en selecteer meerdere poorten; sla wijzigingen op → backend commit per device.
- Drift badge verschijnt als actual ≠ desired; druk op "Sync" om cache te verversen.

## Veiligheid
- Keys worden **alleen** ingelezen uit het gemapte pad in de container.
- Audit en config snapshots staan lokaal in SQLite (`/app/data/app.db`).

## Disclaimer
Dit is een minimalistisch voorbeeld voor snelle start-up. Pas validaties en RPC-filters aan jouw omgeving aan.
