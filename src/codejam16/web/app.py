"""Local Flask app for the CodeJam16 staff workflow."""

from __future__ import annotations

from functools import wraps
import os
from pathlib import Path
import secrets
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, url_for

from codejam16.live import FeedState
from codejam16.store import IncidentStore
from codejam16.venue import DEFAULT_VENUE_PATH, load_venue


DEFAULT_INVITES = {
    "responder-ada": ("responder-ada", "Ada Chen", "responder"),
    "responder-jordan": ("responder-jordan", "Jordan Lee", "responder"),
    "supervisor-morgan": ("supervisor-morgan", "Morgan Ellis", "supervisor"),
}


def create_app(database_path: str | None = None, *, seed_demo: bool = True, venue=None,
               feed: FeedState | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    default_db = Path(__file__).resolve().parents[3] / "local" / "staff.sqlite3"
    db_path = database_path or os.environ.get("CODEJAM_DATABASE", str(default_db))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    app.config.update(
        SESSION_COOKIE_NAME="codejam_staff",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 7,
        OVERDUE_SECONDS=int(os.environ.get("CODEJAM_OVERDUE_SECONDS", "300")),
        INVITE_TOKENS=os.environ.get("CODEJAM_INVITE_TOKENS", ""),
    )
    store = IncidentStore(db_path, app.config["OVERDUE_SECONDS"])
    app.extensions["incident_store"] = store
    venue = venue or load_venue()
    feed = feed or FeedState()
    app.extensions["venue"] = venue
    app.extensions["feed_state"] = feed

    invites = dict(DEFAULT_INVITES)
    # Optional production-style tokens map to the same local demo staff identities.
    configured = [token.strip() for token in app.config["INVITE_TOKENS"].split(",") if token.strip()]
    for (staff_id, name, role), token in zip(DEFAULT_INVITES.values(), configured):
        invites[token] = (staff_id, name, role)
    for token, (staff_id, name, role) in invites.items():
        store.ensure_invitation(token, staff_id, name, role)
    if seed_demo:
        store.seed_demo()

    def snapshot(viewer):
        result = store.snapshot(viewer, feed.snapshot())
        result["zones"] = venue.to_public()
        return result

    def current_user():
        return store.get_session(request.cookies.get(app.config["SESSION_COOKIE_NAME"]))

    def require_user(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("join_landing"))
            return view(user, *args, **kwargs)
        return wrapped

    def safe_origin():
        origin = request.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.netloc == request.host and parsed.scheme in {"http", "https"}

    def require_action_guard():
        if not safe_origin():
            return jsonify({"error": "Request origin did not match this app"}), 403
        token = request.cookies.get(app.config["SESSION_COOKIE_NAME"])
        if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), store.get_csrf(token) or ""):
            return jsonify({"error": "Refresh the page and try again"}), 403
        return None

    @app.get("/join")
    def join_landing():
        return render_template("join.html", invite=None, error=None)

    @app.route("/join/<token>", methods=["GET", "POST"])
    def join(token):
        invite = store.invitation(token)
        if not invite:
            return render_template("join.html", invite=None, error="This invite is invalid or has expired."), 404
        if request.method == "GET":
            return render_template("join.html", invite=invite, token=token, error=None)
        session_token = store.create_session(token)
        if not session_token:
            return render_template("join.html", invite=None, error="This invite is invalid or has expired."), 404
        raw, _csrf = session_token
        destination = "supervisor" if invite["role"] == "supervisor" else "home"
        response = make_response(redirect(url_for(destination)))
        response.set_cookie(app.config["SESSION_COOKIE_NAME"], raw, max_age=7 * 24 * 60 * 60,
                            httponly=True, secure=app.config["SESSION_COOKIE_SECURE"], samesite="Lax",
                            path="/")
        return response

    @app.post("/logout")
    def logout():
        response = make_response(redirect(url_for("join_landing")))
        response.delete_cookie(app.config["SESSION_COOKIE_NAME"], path="/")
        return response

    @app.get("/")
    @require_user
    def home(user):
        return render_template("app.html", viewer=user, initial_view="queue")

    @app.get("/supervisor")
    @require_user
    def supervisor(user):
        if user["role"] != "supervisor":
            return redirect(url_for("home"))
        return render_template("app.html", viewer=user, initial_view="map")

    @app.get("/api/state")
    @require_user
    def state(_user):
        raw = request.cookies.get(app.config["SESSION_COOKIE_NAME"])
        current = store.get_session(raw)
        result = snapshot(current)
        result["csrf_token"] = store.get_csrf(raw)
        return jsonify(result)

    @app.post("/api/checks/<incident_id>/actions")
    @require_user
    def action(user, incident_id):
        denied = require_action_guard()
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        ok, message, incident = store.transition(incident_id, user, body.get("action", ""), body.get("outcome"))
        if not ok:
            code = 404 if incident is None else 409
            return jsonify({"error": message, "check": incident}), code
        state = snapshot(user)
        state["csrf_token"] = store.get_csrf(request.cookies.get(app.config["SESSION_COOKIE_NAME"]))
        return jsonify({"check": incident, "state": state})

    return app


app = create_app()


def predictor_loader(weights: Path):
    """Return a loader that refuses to fall back to downloading missing weights."""
    def load():
        if not weights.is_file():
            raise FileNotFoundError(f"pose weights not found: {weights}")
        from codejam16 import vision_cli
        return vision_cli._load_pose_predictor(weights)
    return load


def main(argv=None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the local CodeJam16 staff app")
    parser.add_argument("--host", default=os.environ.get("CODEJAM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CODEJAM_PORT", "5000")))
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--camera-index", type=int, help="Run the detector on this macOS camera")
    source.add_argument("--replay", type=Path, help="Run the detector on a labelled rehearsal clip")
    source.add_argument("--no-detector", action="store_true", help="Serve sample checks only (default)")
    parser.add_argument("--fallback-clip", type=Path, help="Clip to replay if the camera fails before a check")
    parser.add_argument("--venue", type=Path, default=DEFAULT_VENUE_PATH)
    parser.add_argument("--camera-id", default="demo-camera")
    parser.add_argument("--weights", type=Path, default=Path("local/yolo26n-pose.pt"))
    args = parser.parse_args(argv)

    venue = load_venue(args.venue)
    feed = FeedState()
    server = create_app(venue=venue, feed=feed)
    if args.camera_index is not None or args.replay is not None:
        from codejam16.live import DetectorWorker

        DetectorWorker(venue=venue, camera_id=args.camera_id, feed=feed,
                       deliver=server.extensions["incident_store"].create_incident,
                       load_predictor=predictor_loader(args.weights),
                       camera_index=args.camera_index, replay_path=args.replay,
                       fallback_clip=args.fallback_clip).start()
    server.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
