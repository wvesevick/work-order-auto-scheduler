# Work Order Auto Scheduler

Dispatch-focused scheduling software that assigns service work orders to technicians while balancing fairness and travel efficiency.

## Project Summary

This project showcases full-stack product execution for field operations:
- Designed and built a constraint-aware scheduler that balances technician workload by day/week while minimizing travel.
- Implemented real dispatch constraints including locked jobs, activity-type matching, and customer time-slot windows.
- Delivered a complete workflow across data model, APIs, UI, demo data seeding, and cost-aware routing integration.

## What It Does

- Manages work orders with statuses: `submitted`, `pending`, `scheduled`, `complete`
- Supports single-work-order routing and batch scheduling
- Presents a weekly planning board with 4 fixed slots per day
- Shows per-technician map routes with segment times
- Supports technician specialization by activity type
- Handles out-of-office slots and blocked availability

## Scheduling Approach

The routing logic scores candidate placements using:
- fairness targets by technician per day and per week
- activity-type compatibility
- customer time-window fit
- lock preservation for already-placed jobs
- travel-time minimization with API-aware fallback behavior

## Security and API Cost Controls

- Secrets are read from `.env` only and excluded from git.
- No browser-exposed API key is required to run the demo UI.
- Paid travel-time calls run server-side only.
- Daily paid-call usage can be capped with `GOOGLE_MAPS_DAILY_CALL_LIMIT`.
- Local demo works without paid API access (fallback behavior included).

## Stack

- Python 3.11+
- Django 5.x
- Django REST Framework
- Vanilla JavaScript + Django templates
- Leaflet + OpenStreetMap for mapping

## Quick Start

```bash
git clone https://github.com/wvesevick/work-order-auto-scheduler.git
cd work-order-auto-scheduler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py seed_demo_data --reset
python manage.py runserver
```

Open in browser:
- Scheduler: `http://127.0.0.1:8000/schedule/`
- Work Orders: `http://127.0.0.1:8000/work-orders/`
- Technicians: `http://127.0.0.1:8000/technicians/`

Demo seed includes:
- 50 total work orders
- 25 pre-scheduled
- 25 unscheduled/submitted

## Environment Variables

Required for local startup:
- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=true`
- `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1`

Optional routing controls:
- `GOOGLE_MAPS_API_KEY=`
- `GOOGLE_MAPS_DAILY_CALL_LIMIT=0`
- `GOOGLE_MAPS_DEFAULT_TRAVEL_MINUTES=15`

## Repository Structure

- `core/` core models and migrations
- `scheduling/` scheduling models, API views, routing utilities, templates
- `ice_machine_system/` Django project settings and URL wiring
- `scheduling/management/commands/seed_demo_data.py` deterministic demo dataset loader

## Reviewer Path (2 Minutes)

1. Run quick start.
2. Open Scheduler and inspect a technician route/day.
3. Open Work Orders and test status updates/routing actions.
4. Review `scheduling/utils.py` and `scheduling/views.py` for routing behavior.

## Common Commands

```bash
python manage.py check
python manage.py migrate
python manage.py seed_demo_data --reset
python manage.py test
python manage.py runserver
```

## License

MIT (see `LICENSE`).
