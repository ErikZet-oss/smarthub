# Smarthub

Interna aplikacia na porovnanie cien/skladov napriec dodavatelmi a automatizaciu nakupu.

## Stack

- Frontend: Next.js + Tailwind CSS + UI komponenty v style shadcn/ui
- Backend: FastAPI + SQLModel + SQLite
- Automation: Playwright (service skeleton)

## Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev
```

## Backend (FastAPI)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Doplň .env rovnakým SMARTHUB_AUTH_SECRET ako vo frontend/.env.local
uvicorn app.main:app --reload
```

Na Windows môžeš API na porte **8001** spustiť jedným príkazom (ak už máš `.venv` a `backend/.env`):

```powershell
cd backend
.\run-dev.ps1
```

Ak na **8000** už beží iná aplikácia (uvidíš pri prihlásení hlášku o chýbajúcom Smarthub logine), spusti Smarthub API na inom porte a vo `frontend/.env.local` nastav rovnakú adresu:

```bash
uvicorn app.main:app --reload --port 8001
```

`NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8001`

## Render: aby sa nestrácali dáta po reštarte

Backend používa SQLite súbor. Na Renderi musí byť uložený na persistent disku:

1. V backend službe vytvor **Persistent Disk** (napr. mount path `/var/data`).
2. Do backend Environment Variables nastav:
   - `SMARTHUB_DB_PATH=/var/data/procurement.db`
3. Redeploy backend.

Bez tohto nastavenia sa po reštarte služby vrátia staré/čisté dáta.

## Co je pripravene

- Dashboard layout so sidebarom
- Stranka `Vyhladavanie` s filtrami, compact tabulkou a rozbalenim detailu dodavatelov
- Stranka `Dodavatelia` s kartami a password inputom (eye toggle)
- Stranka `Parovanie` so split-screen mapovanim a zelenym feedbackom po sparovani
- API skeleton pre vyhladavanie produktov a akciu add-to-cart
