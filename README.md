# Mero Allotment

Mero Allotment is a Flask web app for saving BOIDs with labels and checking IPO allotment results.

## Project Layout

- `app.py` - Flask app, BOID storage, and check routes
- `tempFile.py` - IPO result checker and CAPTCHA handling
- `templates/` - HTML templates
- `static/` - CSS and other static assets
- `instance/` - local SQLite data for development
- `render.yaml` - Render deployment blueprint
- `Procfile` - production start command
- `DEPLOYMENT.md` - hosting notes

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

## Environment Variables

- `IPO_WEB_SECRET` - required for hosted deployments
- `IPO_DB_PATH` - optional path to a persistent SQLite file

## Hosting

The repo includes a Render blueprint in `render.yaml`.

For public hosting:

1. Push this repo to GitHub.
2. Create a new Render Blueprint from the repository.
3. Keep the generated `IPO_WEB_SECRET`.
4. Keep the attached disk enabled so the SQLite file survives restarts.
5. Open the public URL Render gives you.

## Notes

- BOIDs are stored per browser/device using a persistent owner token.
- The current setup is public-facing, but it does not yet include user accounts.
- If you want BOIDs to follow a person across devices, add login/authentication later.