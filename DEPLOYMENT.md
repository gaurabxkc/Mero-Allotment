# Deployment

This app is ready to host as a standard Flask project.

For public use, the app can be opened to anyone on the web. BOIDs are stored in browser localStorage only, so each browser keeps its own saved list.

## Required environment variables

- `IPO_WEB_SECRET`: set this to a long random string.
- `PORT`: set by most hosts automatically.

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
gunicorn wsgi:app
```

If your host needs an explicit port, use:

```bash
gunicorn --bind 0.0.0.0:$PORT wsgi:app
```

## Render setup

The included `render.yaml` is the easiest path for public hosting.

1. Push this repo to GitHub.
2. Create a new Render Blueprint from the repo.
3. Keep the generated `IPO_WEB_SECRET`.
4. Deploy the service.

## Storage note

Saved BOIDs are browser-local only. The server does not store BOIDs.

## Public-use note

If you want users to keep BOIDs across devices, add authentication and a backend database in a future version.