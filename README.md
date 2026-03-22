# Ice Machine Scheduler

Web-based dispatch and routing tool for technician scheduling.

This project lets you:
- create and manage work orders
- manage technicians and activity types
- route submitted work orders into technician schedules
- visualize schedules in time slots
- approve/revert statuses (`submitted`, `pending`, `scheduled`, `complete`)
- use Google Maps travel times (optional, via API key)

## Tech Stack

- Python 3.11+ (tested with Python 3.13)
- Django 5.x
- Django REST Framework
- Vanilla HTML/CSS/JavaScript frontend templates

## Project Structure

- `core/`: business models (customer, lease, machine, work order)
- `scheduling/`: scheduler logic, APIs, templates, and scheduling models
- `ice_machine_system/`: Django settings and URL config
- `manage.py`: Django entry point

## Quick Start (Local)

### 1. Clone and enter project

```bash
git clone <your-repo-url>
cd ice_machine_project
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Minimal `.env` values:
- `DJANGO_SECRET_KEY` (set to any non-empty value for local dev)
- Optional Google keys:
  - `GOOGLE_MAPS_API_KEY` (backend travel-time lookups)
  - `GOOGLE_MAPS_DAILY_CALL_LIMIT` (default `0`, blocks paid API calls)
  - `GOOGLE_MAPS_DEFAULT_TRAVEL_MINUTES` (fallback travel time when key/limit is unavailable)

If Google keys are missing, the app still runs; travel-time lookups fall back to defaults so users can still test scheduling behavior.
The schedule map uses OpenStreetMap in secure demo mode, so no browser API key is exposed.
When a backend Google key is present, route overlays and leg durations are fetched server-side (key remains hidden) and rendered on top of the map.
For public resume demos, keep `GOOGLE_MAPS_DAILY_CALL_LIMIT=0` to prevent accidental charges.

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Load demo data (recommended)

```bash
python manage.py seed_demo_data --reset
```

This seeds a ready-to-demo dataset:
- 50 total work orders
- 25 `scheduled` (already placed on technician schedules)
- 25 `submitted` (unscheduled, ready to route)

### 7. Start the server

```bash
python manage.py runserver
```

Open:
- Scheduler: `http://127.0.0.1:8000/schedule/`
- Work Orders: `http://127.0.0.1:8000/work-orders/`
- Technicians: `http://127.0.0.1:8000/technicians/`
- Admin: `http://127.0.0.1:8000/admin/`

## Optional: Create Admin User

```bash
python manage.py createsuperuser
```

## Database

Default is SQLite (`db.sqlite3`) for easy local setup.

To use PostgreSQL, set these in `.env`:
- `DB_ENGINE=postgres`
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`

## Notes for GitHub/Collaboration

- Do **not** commit `.env`.
- `scheduling/travel_times.json` is a runtime cache and is gitignored.
- Virtualenvs and generated static files are intentionally excluded.
- API keys are optional and loaded from `.env` only.
- External travel-time API calls are capped by `GOOGLE_MAPS_DAILY_CALL_LIMIT`; when the cap is reached, fallback travel times are used.

## Common Commands

```bash
python manage.py check
python manage.py makemigrations
python manage.py migrate
python manage.py seed_demo_data --reset
python manage.py runserver
```

## Troubleshooting

### No routes generated
- Make sure technicians and work orders have matching `activity_type` values.
- Ensure work orders are in `submitted` status before routing.

### Map features not working
- Add valid Google Maps keys to `.env`.

### Import/Module errors
- Confirm virtualenv is active and dependencies were installed from `requirements.txt`.
