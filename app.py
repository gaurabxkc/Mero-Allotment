from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from datetime import timedelta
import uuid

from flask import Flask, flash, g, redirect, render_template, request, url_for

from tempFile import check_single_boid, fetch_data


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

DATABASE_PATH = os.environ.get(
    "IPO_DB_PATH", os.path.join(INSTANCE_DIR, "ipo_boids.db")
)
SECRET_KEY = os.environ.get("IPO_WEB_SECRET")
OWNER_COOKIE_NAME = "boid_owner_id"
OWNER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

app = Flask(__name__)
app.secret_key = SECRET_KEY or "dev-only-change-me"
app.permanent_session_lifetime = timedelta(days=365)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with closing(get_db()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS boids_by_owner (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                label TEXT NOT NULL,
                boid TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, boid)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_boids_by_owner_owner_id ON boids_by_owner(owner_id)"
        )
        connection.commit()


def normalize_boid(value: str) -> str:
    return "".join(character for character in value.strip() if character.isdigit())


@app.before_request
def load_owner_id() -> None:
    owner_id = request.cookies.get(OWNER_COOKIE_NAME)

    if not owner_id:
        owner_id = uuid.uuid4().hex

    g.owner_id = owner_id


@app.after_request
def persist_owner_id(response):
    owner_id = getattr(g, "owner_id", None)
    if owner_id:
        response.set_cookie(
            OWNER_COOKIE_NAME,
            owner_id,
            max_age=OWNER_COOKIE_MAX_AGE,
            path="/",
            samesite="Lax",
            secure=not app.debug,
        )
    return response


def get_owner_id() -> str:
    owner_id = getattr(g, "owner_id", None)
    if not owner_id:
        owner_id = uuid.uuid4().hex
        g.owner_id = owner_id
    return owner_id


def load_boids() -> list[sqlite3.Row]:
    owner_id = get_owner_id()
    with closing(get_db()) as connection:
        return connection.execute(
            """
            SELECT id, label, boid, created_at, updated_at
            FROM boids_by_owner
            WHERE owner_id = ?
            ORDER BY label COLLATE NOCASE, boid
            """,
            (owner_id,),
        ).fetchall()


def get_boid_by_id(boid_id: int) -> sqlite3.Row | None:
    owner_id = get_owner_id()
    with closing(get_db()) as connection:
        return connection.execute(
            """
            SELECT id, label, boid, created_at, updated_at
            FROM boids_by_owner
            WHERE id = ? AND owner_id = ?
            """,
            (boid_id, owner_id),
        ).fetchone()


def save_boid(label: str, boid: str) -> None:
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    owner_id = get_owner_id()
    with closing(get_db()) as connection:
        connection.execute(
            """
            INSERT INTO boids_by_owner (owner_id, label, boid, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_id, boid) DO UPDATE SET
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (owner_id, label, boid, timestamp, timestamp),
        )
        connection.commit()


def delete_boid(boid_id: int) -> None:
    owner_id = get_owner_id()
    with closing(get_db()) as connection:
        connection.execute(
            "DELETE FROM boids_by_owner WHERE id = ? AND owner_id = ?",
            (boid_id, owner_id),
        )
        connection.commit()


def get_companies() -> list[dict[str, str | int]]:
    body = fetch_data()
    if not body:
        return []
    return body.get("companyShareList", [])


@app.route("/", methods=["GET"])
def index():
    get_owner_id()
    companies = get_companies()
    boids = load_boids()
    edit_boid = None
    edit_boid_id = request.args.get("edit_id", type=int)
    if edit_boid_id is not None:
        edit_boid = get_boid_by_id(edit_boid_id)
    return render_template(
        "index.html",
        companies=companies,
        boids=boids,
        edit_boid=edit_boid,
        selected_company_id=request.args.get("company_id", type=int),
        results=[],
    )


@app.route("/boids", methods=["POST"])
def create_boid():
    label = request.form.get("label", "").strip()
    boid = normalize_boid(request.form.get("boid", ""))

    if not label:
        flash("Add a label for this BOID.", "error")
        return redirect(url_for("index"))

    if len(boid) != 16:
        flash("BOID must be exactly 16 digits.", "error")
        return redirect(url_for("index"))

    save_boid(label, boid)
    flash(f"Saved {label}.", "success")
    return redirect(url_for("index"))


@app.route("/boids/<int:boid_id>/delete", methods=["POST"])
def remove_boid(boid_id: int):
    delete_boid(boid_id)
    flash("BOID removed.", "success")
    return redirect(url_for("index"))


@app.route("/boids/<int:boid_id>/update", methods=["POST"])
def update_boid(boid_id: int):
    owner_id = get_owner_id()
    label = request.form.get("label", "").strip()
    boid = normalize_boid(request.form.get("boid", ""))

    if not label:
        flash("Add a label for this BOID.", "error")
        return redirect(url_for("index", edit_id=boid_id))

    if len(boid) != 16:
        flash("BOID must be exactly 16 digits.", "error")
        return redirect(url_for("index", edit_id=boid_id))

    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    try:
        with closing(get_db()) as connection:
            result = connection.execute(
                """
                UPDATE boids_by_owner
                SET label = ?, boid = ?, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (label, boid, timestamp, boid_id, owner_id),
            )
            connection.commit()
    except sqlite3.IntegrityError:
        flash("That BOID is already saved on another row.", "error")
        return redirect(url_for("index", edit_id=boid_id))

    if result.rowcount == 0:
        flash("That BOID could not be found.", "error")
        return redirect(url_for("index"))

    flash("BOID updated.", "success")
    return redirect(url_for("index"))


@app.route("/check", methods=["POST"])
def check():
    get_owner_id()
    companies = get_companies()
    boids = load_boids()
    results: list[dict[str, str]] = []

    selected_company_id = request.form.get("company_id", type=int)
    if selected_company_id is None:
        flash("Choose an IPO company first.", "error")
        return render_template(
            "index.html",
            companies=companies,
            boids=boids,
            selected_company_id=None,
            results=[],
        )

    selected_ids = {
        value for value in request.form.getlist("boid_ids") if value.isdigit()
    }
    selected_rows = [row for row in boids if str(row["id"]) in selected_ids]

    if not selected_rows:
        flash("Select at least one saved BOID to check.", "error")
        return render_template(
            "index.html",
            companies=companies,
            boids=boids,
            selected_company_id=selected_company_id,
            results=[],
        )

    company_name = next(
        (
            company["name"]
            for company in companies
            if str(company.get("id")) == str(selected_company_id)
        ),
        "Selected company",
    )

    flash(
        f"Running checks for {company_name}. This can take a little while.", "success"
    )

    for row in selected_rows:
        outcome = check_single_boid(row["boid"], selected_company_id)
        results.append(
            {
                "label": row["label"],
                "boid": row["boid"],
                "result": outcome,
            }
        )

    return render_template(
        "index.html",
        companies=companies,
        boids=boids,
        selected_company_id=selected_company_id,
        results=results,
    )


if __name__ == "__main__":
    init_db()
    if not SECRET_KEY:
        print(
            "Warning: IPO_WEB_SECRET is not set. Using a development-only secret key."
        )
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)


init_db()
