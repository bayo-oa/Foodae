# Local Food Delivery MVP

Flask + PostgreSQL food ordering & delivery platform. Roles: Customer, Vendor, Rider, Admin.

## Quick start (local)

1. `python3 -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in `DATABASE_URL`, `FLASK_SECRET_KEY`, Paystack keys.
   - For quick local testing without Postgres, you can set `DATABASE_URL=sqlite:///dev.db`
4. `flask db upgrade` (or, if no migrations folder yet: `flask db init && flask db migrate -m "init" && flask db upgrade`)
5. `flask create-admin` — creates the first admin account (prompts for email/password)
6. `flask run`

## Verified working (tested end-to-end in this build)

- Register/login for Customer, Vendor, Rider roles; role-based access control enforced server-side
- Vendor: create restaurant, manage categories/menu items, accept/prepare/ready orders
- Customer: browse restaurants, cart, checkout, address entry, Paystack payment initiation
- Payment webhook: signature verification, rejects forged/invalid webhooks
- Order state machine: enforces valid transitions only, blocks illegal jumps (e.g. can't skip straight to DELIVERED)
- Rider: accepts delivery from pool, pickup → out for delivery → delivered
- Customer: live-ish status tracking page (polling), post-delivery rating
- Admin: dashboard stats, user/restaurant/rider approval & suspension, full order monitoring
- Cross-role authorization: a vendor cannot edit another vendor's restaurant/menu via URL manipulation (returns 403)

## Known gaps before going live

- **File uploads**: currently save to local disk (`app/static/uploads`), which does NOT persist on Render's free tier (ephemeral filesystem). Before deploying, swap `app/utils.py`'s `save_upload()` for Cloudinary (or Render's persistent disk add-on).
- **Paystack keys**: `.env.example` has placeholders. Get real test keys from your Paystack dashboard and set up a webhook URL (use ngrok for local testing).
- **No live GPS tracking** — deferred, tracking page shows status timeline only, not a live map (per MVP scope).
- **No email verification** — accounts activate immediately on register (matches MVP scope decision).

## Deploying to Render

1. Push this repo to GitHub.
2. New Web Service on Render, connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn 'app:create_app()'`
5. Add a Render Postgres instance, copy its connection string into `DATABASE_URL` env var (Render gives `postgres://`, this app auto-converts it to `postgresql://`).
6. Set `FLASK_SECRET_KEY`, `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY`, `APP_BASE_URL` (your Render URL) as env vars.
7. Run `flask db upgrade` and `flask create-admin` via Render's shell.

## Note on psycopg2-binary and Python 3.14

If you're on a very new Python version (e.g. 3.14), `psycopg2-binary` may not have a
prebuilt wheel yet and will fail to install unless you have PostgreSQL's `pg_config`
on your PATH. It's now split out of the main `requirements.txt`:

- `pip install -r requirements.txt` — everything needed for local dev with SQLite.
- `pip install -r requirements-postgres.txt` — adds `psycopg2-binary`, only needed
  when you actually connect to a real PostgreSQL database (e.g. before deploying,
  or if you want to test against local Postgres).
