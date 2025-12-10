# Switch Manager – NETCONF Interface & VLAN Manager

## Overzicht

Dit project is een **NETCONF‑based switch management applicatie** met:

* 🔌 Interface visualisatie (incl. AE, VC‑ports)
* 🧠 Cached + live data (SQLite)
* 🧾 Change requests + approval flow
* 🌐 Web UI (grid‑based port view)
* ⏱️ Periodieke background refresh (interfaces & VLANs)

Backend draait volledig in Docker en praat via **ncclient** met Juniper switches.

---

## Architectuur

```
┌──────────┐     REST      ┌────────────┐     NETCONF      ┌─────────────┐
│ Frontend │ ───────────▶ │ FastAPI     │ ─────────────▶ │ Juniper EX   │
│ (JS/HTML)│               │ Backend     │                 │ Switches    │
└──────────┘               │             │                 └─────────────┘
                           │ SQLite DB   │
                           └────────────┘
```

### Kernprincipes

* **Cached by default** – UI leest vrijwel altijd uit database
* **Live fetch expliciet** – via "Refresh interfaces" knop
* **Single source of truth** – database is leidend voor UI
* **No destructive rebuilds** – frontend herbouwt DOM niet onnodig

---

## Backend structuur

```
backend/
├── app/
│   ├── main.py            # FastAPI entrypoint
│   ├── netconf.py         # Alle NETCONF logica
│   ├── devices.py         # Device inventory (JSON)
│   ├── database.py        # SQLAlchemy setup
│   ├── models.py          # DB modellen
│   └── jobs/
│       ├── refresh_interfaces.py
│       ├── refresh_vlans.py
│       └── nightly_refresh.py
├── data/
│   └── app.db             # SQLite database
└── Dockerfile
```

### Database modellen

| Model          | Doel                                    |
| -------------- | --------------------------------------- |
| InterfaceCache | Snapshot van alle interfaces per switch |
| CachedVlan     | VLAN lijst per switch                   |
| ChangeRequest  | Approval workflow                       |

---

## Frontend structuur

```
frontend/
├── index.html
├── app.js        # state + API calls
├── renderer.js   # drawPorts(), grid layout
└── style.css
```

### State management (app.js)

```js
CURRENT_SWITCH
PORT_STATE_CACHE
VLANS_CACHE
PENDING_REQUESTS
```

De renderer **leest alleen state**, en doet geen fetches.

---

## Interface data lifecycle

### 1. Pagina openen

* `/api/switches/{device}/interfaces`
* Data komt uit `InterfaceCache`

### 2. Refresh interfaces (per switch)

* `/api/switches/{device}/interfaces/retrieve`
* Live NETCONF → cache overschrijven

### 3. Port click

* `/interface/{ifname}/live`
* Alleen die poort, met korte TTL

---

## VLAN data lifecycle

* Wordt periodiek opgehaald via job
* Tabel: `vlan_cache`
* UI toont status: *"VLANs cached • last updated 03:00"*

---

## VC‑ports & Virtual Chassis

* VC‑ports komen **alleen** uit:

  ```
  show virtual-chassis vc-port | display xml
  ```
* VC‑ports zijn:

  * Niet configureerbaar
  * Hebben eigen status (`vc_status`)
  * Worden niet overschreven door interface‑config

Frontend:

* VC‑ports krijgen `vc_port: true`
* Visual linking via `data-vc-link`

---

## Change requests

1. User maakt request
2. Request = `pending`
3. Approver keurt goed
4. NETCONF apply (candidate + confirm)
5. Cache invalideert

Rollback‑veilig via confirm commit.

---

## Periodieke jobs

```bash
python -m app.jobs.nightly_refresh
```

* Interfaces refresh
* VLAN refresh
* Veilig standalone uitvoerbaar

---

## Docker Compose

```yaml
services:
  backend:
    build: ./backend
    volumes:
      - ./backend/data:/app/data
  frontend:
    build: ./frontend
    ports:
      - "8081:80"
```

---

## Ontwikkelrichtlijnen

✅ Geen DOM rebuilds per state wijziging
✅ No hidden network calls in renderer
✅ Cache blijft leidend
✅ Live fetch = expliciete actie
✅ Alles per device scoped

---

## Volgende uitbreidingen

* ✅ Renderer diff‑based updates
* ✅ VC‑link animaties
* ⏳ PoE / optics info
* ⏳ Role‑based UI
* ⏳ Websocket auto‑refresh

---

## Status

✅ Productiestabiel voor EX + Virtual Chassis
