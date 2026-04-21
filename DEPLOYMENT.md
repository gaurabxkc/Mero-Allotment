# Deployment

This app is ready to host as a standard Flask project.

For public use, the app can be opened to anyone on the web, and the saved BOIDs stay attached to the same device/browser through a localStorage-backed owner token. A visitor can close the browser and come back later on the same browser and still see the same saved BOIDs, while other devices/browsers get their own lists.

## Required environment variables

- `IPO_WEB_SECRET`: set this to a long random string.
- `PORT`: set by most hosts automatically.
- `IPO_DB_PATH`: optional. Use this if your host gives you a persistent disk path.

## Install

```bash
pip install -r requirements.txt
```

## Run locally

```bash
python app.py
```

## Run in production

Use the production command:

```bash
gunicorn app:app
```

If your host needs an explicit port, use:

```bash
gunicorn --bind 0.0.0.0:$PORT app:app
```

## Render setup

The included `render.yaml` is the easiest path for public hosting.

1. Push this repo to GitHub.
2. Create a new Render Blueprint from the repo.
3. Keep the generated `IPO_WEB_SECRET`.
4. Make sure the attached disk is enabled so `instance/ipo_boids.db` survives restarts.
5. Deploy the service.

## Storage note

BOIDs are stored in SQLite under `/var/data/ipo_boids.db` on Render via the mounted disk, or under `instance/ipo_boids.db` locally by default. For real hosting, attach persistent disk or set `IPO_DB_PATH` to a writable persistent location.

## Public-use note

If you want users to sign in and keep BOIDs across devices, add authentication and tie the BOID rows to a real user account instead of the current anonymous browser owner ID.