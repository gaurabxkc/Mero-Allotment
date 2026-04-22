# Mero Allotment

Mero Allotment is a Flask web app for checking IPO allotment results.

BOIDs are saved in browser localStorage only. The server does not persist BOIDs in a database.

## Project Layout

- `app.py` - local development entrypoint
- `wsgi.py` - production WSGI entrypoint
- `webapp/__init__.py` - app factory and Flask configuration
- `webapp/routes.py` - page route and result API route
- `webapp/ipo_service.py` - IPO API calls and CAPTCHA/OCR checker
- `webapp/templates/` - HTML templates
- `webapp/static/` - CSS and media assets
- `render.yaml` - Render blueprint
- `Procfile` - production start command
- `DEPLOYMENT.md` - deployment notes

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

## Environment Variables

- `IPO_WEB_SECRET` - recommended for hosted deployments

## Hosting

The repo includes a Render blueprint in `render.yaml`.

For public hosting:

1. Push this repo to GitHub.
2. Create a new Render Blueprint from the repository.
3. Keep the generated `IPO_WEB_SECRET`.
4. Deploy and open the Render URL.

## Notes

- BOIDs stay on the same browser/device because localStorage is used.
- If you clear browser data or switch devices, saved BOIDs do not carry over.