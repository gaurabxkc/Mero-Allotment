from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import (
    Blueprint,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)

from .ipo_service import check_single_boid, get_companies

bp = Blueprint("main", __name__)
MAX_CHECK_WORKERS = max(1, min(8, int(os.environ.get("IPO_CHECK_WORKERS", "4"))))


def normalize_boid(value: str) -> str:
    return "".join(ch for ch in value.strip() if ch.isdigit())


def parse_check_payload(
    payload: dict,
) -> tuple[int | None, str | None, list[dict[str, str]], list[dict[str, str]]]:
    company_id = payload.get("company_id")
    boids = payload.get("boids", [])

    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        return None, "Choose a valid IPO company.", [], []

    if not isinstance(boids, list) or not boids:
        return None, "Select at least one BOID.", [], []

    valid_items: list[dict[str, str]] = []
    immediate_results: list[dict[str, str]] = []

    for item in boids:
        label = str(item.get("label", "")).strip() if isinstance(item, dict) else ""
        boid = (
            normalize_boid(str(item.get("boid", ""))) if isinstance(item, dict) else ""
        )

        if not label:
            continue
        if len(boid) != 16:
            immediate_results.append(
                {
                    "label": label,
                    "boid": boid,
                    "result": "BOID must be exactly 16 digits.",
                }
            )
            continue

        valid_items.append({"label": label, "boid": boid})

    return company_id, None, valid_items, immediate_results


def run_check(company_id: int, item: dict[str, str]) -> dict[str, str]:
    try:
        outcome = check_single_boid(item["boid"], company_id)
    except Exception as e:
        outcome = f"Server error: {str(e)}"
    return {"label": item["label"], "boid": item["boid"], "result": outcome}


@bp.route("/", methods=["GET"])
def index():
    companies = get_companies()
    return render_template("index.html", companies=companies)

@bp.route("/debug", methods=["GET"])
def debug_route():
    from .ipo_service import _bridge, _API_DATA
    try:
        raw = _bridge.api_get(_API_DATA)
        return jsonify({"success": True, "raw": raw})
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


@bp.route("/check", methods=["GET", "POST"])
@bp.route("/boids", methods=["GET", "POST"])
@bp.route("/boids/<int:boid_id>/delete", methods=["GET", "POST"])
@bp.route("/boids/<int:boid_id>/update", methods=["GET", "POST"])
def legacy_routes(boid_id: int | None = None):
    return redirect(url_for("main.index"))


@bp.route("/api/check", methods=["GET"])
def api_check_get():
    return redirect(url_for("main.index"))


@bp.route("/api/check", methods=["POST"])
def api_check_post():
    payload = request.get_json(silent=True) or {}
    company_id, error, valid_items, results = parse_check_payload(payload)
    if error:
        return jsonify({"error": error}), 400

    if valid_items:
        for item in valid_items:
            results.append(run_check(company_id, item))

    if not results:
        return jsonify({"error": "No valid BOIDs were provided."}), 400

    return jsonify({"results": results})


@bp.route("/api/check/stream", methods=["POST"])
def api_check_stream():
    payload = request.get_json(silent=True) or {}
    company_id, error, valid_items, immediate_results = parse_check_payload(payload)
    if error:
        return jsonify({"error": error}), 400

    @stream_with_context
    def generate():
        emitted = 0

        for item in immediate_results:
            emitted += 1
            yield json.dumps({"type": "result", "item": item}) + "\n"

        if valid_items:
            for item in valid_items:
                emitted += 1
                yield json.dumps({"type": "result", "item": run_check(company_id, item)}) + "\n"

        if emitted == 0:
            yield json.dumps({"error": "No valid BOIDs were provided."}) + "\n"
            return

        yield json.dumps({"type": "done"}) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")
