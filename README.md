# Juniper Switch Manager (NETCONF)

Een webbased **Juniper switch management applicatie** gebaseerd op **NETCONF**.  
De applicatie biedt een visuele weergave van switchpoorten, ondersteuning voor **Virtual Chassis**,  
en een **change request / approval workflow** voor veilige configuratiewijzigingen.

---

## Features

### Switch & Interface overzicht
- Visuele weergave van:
  - GE ports
  - XE uplinks
  - AE (LACP) interfaces
- Ondersteuning voor **Virtual Chassis** (multi-member)
- Automatische detectie van **VC-ports**
- VC-ports visueel gemarkeerd
- LACP members inzichtelijk per AE

### Configuratiebeheer
- Port configuratie via UI:
  - description
  - access / trunk mode
  - access VLAN
  - trunk VLANs
  - native VLAN
- Ook **unconfigured ports** zijn zichtbaar en configureerbaar
- Duidelijke **diff-weergave** voor submit

### Change Requests & Approval Flow
- Wijzigingen worden eerst opgeslagen als **pending change request**
- Approvers kunnen:
  - diff bekijken
  - request goedkeuren of afkeuren
- Bij approve:
  - NETCONF candidate config
  - `commit confirmed`
  - definitieve commit
- Pending requests zichtbaar per interface
- Goedgekeurde poorten worden visueel gehighlight

### Cached & Live data
- Interface data wordt gecached in **SQLite**
- Live interface status kan opnieuw opgehaald worden
- UI toont duidelijk:
  - cached vs live state
  - pending changes

---

## Architectuur

frontend/
public/
app.js - UI logica
renderer.js - Poort rendering
style.css - Styling

backend/
app/
main.py - FastAPI endpoints
netconf.py - Juniper NETCONF logic
models.py - SQLAlchemy modellen
database.py - SQLite setup

docker-compose.yml

---

## Tech stack

### Backend
- Python 3.11
- FastAPI
- ncclient (NETCONF)
- SQLAlchemy
- SQLite
- Juniper JunOS

### Frontend
- Vanilla JavaScript
- HTML / CSS
- Geen frontend framework

### Infra
- Docker
- Docker Compose
- NETCONF over SSH

---

## Installatie

### Repository clonen
```bash
git clone https://github.com/EFCloudInfra-devops/test-niels.git
cd test-niels
Devices configureren
Voeg je Juniper devices toe in het inventory/config bestand (bijv. devices.yaml):

SW1:
  host: 10.0.0.1
  username: netconf
  password: secret
Start applicatie
bash
Code kopiëren
docker-compose up --build
Web UI
arduino
Code kopiëren
http://localhost:8081
Authenticatie & Rollen
Authenticatie is simpel gehouden via HTTP headers (demo-opzet).

Header	Omschrijving
X-User	Gebruikersnaam
X-Role	reader / approver / admin

Voorbeeld approve request:

http
Code kopiëren
POST /api/requests/1/approve
X-User: admin
X-Role: approver
Veiligheid
Alle configuraties via NETCONF candidate

commit confirmed voorkomt vastgelopen configs

Automatische rollback bij fouten

Change requests zorgen voor audit trail

Roadmap
User accounts & login

Role Based Access Control (RBAC)

Config history per interface

Rollback per change request

UI indicators voor cached vs live state

Status
Dit project is actief in ontwikkeling en bedoeld voor:

lab omgevingen

network automation testing

proof-of-concepts

Auteur
Niels Krijgsman
EFCloudInfra – DevOps / Network Automation

Licentie
MIT
