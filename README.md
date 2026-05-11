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

## Render: backend sa hneď po deployi vypne (exit 1)

Príkaz `uvicorn app.main:app` musí bežať z priečinka **`backend`** (tam je Python balík `app`). Ak je v Renderi **Root Directory** prázdny (koreň monorepa), `import app` zlyhá a proces skončí hneď po štarte.

**Oprava (jedna z možností):**

1. V službe Web Service nastav **Root Directory** na `backend`, **Build Command** na `pip install -r requirements.txt` a **Start Command** na `uvicorn app.main:app --host 0.0.0.0 --port $PORT`,  
   **alebo**
2. Nechaj Root Directory prázdny, ale nastav **Start Command** napr. na:  
   `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT`  
   a **Build Command** na:  
   `cd backend && pip install -r requirements.txt`.

V koreňovom `render.yaml` je ukážka Blueprintu s `rootDir: backend` (pri novom prepojení repozitára cez Blueprint).

## Render: aby sa nestrácali dáta po reštarte

Backend používa SQLite súbor. Na Renderi musí byť uložený na persistent disku:

1. V backend službe vytvor **Persistent Disk** (napr. mount path `/var/data`).
2. Do backend Environment Variables nastav:
   - `SMARTHUB_DB_PATH=/var/data/procurement.db`
3. Redeploy backend.

Bez tohto nastavenia sa po reštarte služby vrátia staré/čisté dáta.

## Neon (free) ako produkčná DB

Backend vie bežať aj na PostgreSQL cez `DATABASE_URL` (odporúčané pre free plán bez Render disku).

1. V Neon vytvor databázu a skopíruj connection string.
2. V Render backend service nastav ENV:
   - `DATABASE_URL=postgresql+psycopg://...?...sslmode=require`
3. Redeploy backend.

Poznámky:
- Ak je nastavené `DATABASE_URL`, backend ignoruje lokálnu SQLite cestu.
- Pri prvom štarte sa tabuľky vytvoria automaticky (`create_all`).
- Dáta zo starej SQLite sa do Neon nepresunú automaticky (treba znovu import Excelu alebo urobiť jednorazový export/import).

## Co je pripravene

- Dashboard layout so sidebarom
- Stranka `Vyhladavanie` s filtrami, compact tabulkou a rozbalenim detailu dodavatelov
- Stranka `Dodavatelia` s kartami a password inputom (eye toggle)
- Stranka `Parovanie` so split-screen mapovanim a zelenym feedbackom po sparovani
- API skeleton pre vyhladavanie produktov a akciu add-to-cart
