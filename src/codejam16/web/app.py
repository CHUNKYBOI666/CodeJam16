"""Local Flask app for the CodeJam16 staff workflow."""

from __future__ import annotations

from functools import wraps
import os
from pathlib import Path
import secrets
import socket
from urllib.parse import urlparse

from flask import Flask, jsonify, make_response, redirect, render_template, request, url_for

from codejam16.live import FeedState
from codejam16.store import IncidentStore
from codejam16.venue import DEFAULT_VENUE_PATH, load_venue


DEFAULT_PORT = 8016
COOKIE_SECONDS = 7 * 24 * 60 * 60


def lan_address() -> str:
    """Best guess at this Mac's address on the demo Wi-Fi."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def create_app(database_path: str | None = None, *, seed_demo: bool = False, venue=None,
               feed: FeedState | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    default_db = Path(__file__).resolve().parents[3] / "local" / "staff.sqlite3"
    db_path = database_path or os.environ.get("CODEJAM_DATABASE", str(default_db))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    app.config.update(
        SESSION_COOKIE_NAME="codejam_staff",
        OVERDUE_SECONDS=int(os.environ.get("CODEJAM_OVERDUE_SECONDS", "300")),
    )
    store = IncidentStore(db_path, app.config["OVERDUE_SECONDS"])
    app.extensions["incident_store"] = store
    venue = venue or load_venue()
    feed = feed or FeedState()
    app.extensions["venue"] = venue
    app.extensions["feed_state"] = feed
    if seed_demo:
        store.seed_demo()

    cookie_name = app.config["SESSION_COOKIE_NAME"]

    def snapshot(viewer):
        result = store.snapshot(viewer, feed.snapshot())
        result["zones"] = venue.to_public()
        return result

    def session_token():
        return request.cookies.get(cookie_name)

    def require_user(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = store.get_session(session_token())
            if not user:
                return redirect(url_for("join_form"))
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
        if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""),
                                      store.get_csrf(session_token()) or ""):
            return jsonify({"error": "Refresh the page and try again"}), 403
        return None

    @app.get("/join")
    @app.get("/join/<path:_old_invite>")
    def join_form(_old_invite=None):
        user = store.get_session(session_token())
        return render_template("join.html", name=user["name"] if user else "", error=None,
                               has_session=bool(user))

    @app.post("/join")
    def join():
        if not safe_origin():
            return render_template("join.html", name="", error="That request came from another site.",
                                   has_session=False), 403
        name = request.form.get("name", "")
        joined = store.join_by_name(name, session_token())
        if not joined:
            return render_template("join.html", name="", error="Type your name to join.",
                                   has_session=bool(store.get_session(session_token()))), 400
        raw, _csrf, _person = joined
        response = make_response(redirect(url_for("home")))
        response.set_cookie(cookie_name, raw, max_age=COOKIE_SECONDS, httponly=True,
                            secure=False, samesite="Lax", path="/")
        return response

    @app.get("/")
    @require_user
    def home(user):
        return render_template("app.html", viewer=user, initial_tab="checks")

    @app.get("/map")
    @require_user
    def map_view(user):
        return render_template("app.html", viewer=user, initial_tab="map")

    @app.get("/api/state")
    @require_user
    def state(user):
        result = snapshot(user)
        result["csrf_token"] = store.get_csrf(session_token())
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
        result = snapshot(user)
        result["csrf_token"] = store.get_csrf(session_token())
        return jsonify({"check": incident, "state": result})

    return app


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
    parser.add_argument("--host", default=os.environ.get("CODEJAM_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CODEJAM_PORT", str(DEFAULT_PORT))))
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--camera-index", type=int, help="Run the detector on this macOS camera")
    source.add_argument("--replay", type=Path, help="Run the detector on a labelled rehearsal clip")
    source.add_argument("--no-detector", action="store_true", help="Serve without a detector (default)")
    parser.add_argument("--fallback-clip", type=Path, help="Clip to replay if the camera fails before a check")
    parser.add_argument("--with-sample", action="store_true", help="Also seed the overdue Main floor sample check")
    parser.add_argument("--venue", type=Path, default=DEFAULT_VENUE_PATH)
    parser.add_argument("--camera-id", default="demo-camera")
    parser.add_argument("--weights", type=Path, default=Path("local/yolo26n-pose.pt"))
    args = parser.parse_args(argv)

    venue = load_venue(args.venue)
    if args.camera_id not in venue.cameras:
        parser.error(f"camera {args.camera_id} is not in {args.venue}")
    feed = FeedState()
    server = create_app(venue=venue, feed=feed, seed_demo=args.with_sample)
    if args.camera_index is not None or args.replay is not None:
        from codejam16.live import DetectorWorker

        DetectorWorker(venue=venue, camera_id=args.camera_id, feed=feed,
                       deliver=server.extensions["incident_store"].create_incident,
                       load_predictor=predictor_loader(args.weights),
                       camera_index=args.camera_index, replay_path=args.replay,
                       fallback_clip=args.fallback_clip).start()
    print(f"Join at http://{lan_address()}:{args.port}/join", flush=True)
    server.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
