"""SQLite-backed staff sessions and welfare check workflow."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import sqlite3


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def presentation(signal):
    if signal == "possible_retching":
        return "Guest may need assistance", "Possible retching motion", "Assistance provided"
    return "Possible person down", "Possible person down detected", "Confirmed person down"


class IncidentStore:
    def __init__(self, path: str, overdue_seconds: int = 300):
        self.path = path
        self.overdue_seconds = overdue_seconds
        self._initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL UNIQUE,
                    signal TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    zone_id TEXT NOT NULL,
                    zone_name TEXT NOT NULL,
                    landmark TEXT NOT NULL,
                    source TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner_id TEXT,
                    owner_name TEXT,
                    outcome TEXT,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL REFERENCES incidents(id),
                    event TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS invitations (
                    token_hash TEXT PRIMARY KEY,
                    staff_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    staff_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    csrf TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS history_incident_idx
                    ON history(incident_id, id);
                CREATE INDEX IF NOT EXISTS incident_status_idx ON incidents(status);
            """)

    def ensure_invitation(self, token: str, staff_id: str, name: str, role: str) -> None:
        expires = iso(utc_now() + timedelta(days=7))
        with self.connect() as db:
            db.execute("""INSERT INTO invitations(token_hash, staff_id, name, role, expires_at)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(token_hash) DO UPDATE SET
                staff_id=excluded.staff_id, name=excluded.name, role=excluded.role,
                expires_at=excluded.expires_at""", (_hash(token), staff_id, name, role, expires))

    def invitation(self, token: str):
        with self.connect() as db:
            row = db.execute("SELECT staff_id, name, role, expires_at FROM invitations WHERE token_hash=?",
                             (_hash(token),)).fetchone()
        if not row or row["expires_at"] <= iso(utc_now()):
            return None
        return dict(row)

    def create_session(self, invite_token: str) -> str | None:
        identity = self.invitation(invite_token)
        if not identity:
            return None
        raw = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            valid = db.execute("SELECT 1 FROM invitations WHERE token_hash=? AND expires_at>?",
                               (_hash(invite_token), iso(utc_now()))).fetchone()
            if not valid:
                db.rollback()
                return None
            db.execute("INSERT INTO sessions(token_hash, staff_id, name, role, csrf, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (_hash(raw), identity["staff_id"], identity["name"], identity["role"], csrf,
                        iso(utc_now() + timedelta(days=7))))
            db.commit()
        return raw, csrf

    def get_session(self, raw_token: str | None):
        if not raw_token:
            return None
        with self.connect() as db:
            row = db.execute("SELECT staff_id AS id, name, role, expires_at FROM sessions WHERE token_hash=?",
                             (_hash(raw_token),)).fetchone()
        if not row or row["expires_at"] <= iso(utc_now()):
            return None
        return {"id": row["id"], "name": row["name"], "role": row["role"]}

    def get_csrf(self, raw_token: str | None):
        if not raw_token:
            return None
        with self.connect() as db:
            row = db.execute("SELECT csrf FROM sessions WHERE token_hash=? AND expires_at>?",
                             (_hash(raw_token), iso(utc_now()))).fetchone()
        return row["csrf"] if row else None

    def create_incident(self, event: dict) -> dict:
        detected_at = event.get("detected_at") or iso(utc_now())
        incident_id = event.get("episode_id") or secrets.token_hex(12)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM incidents WHERE episode_id=?", (incident_id,)).fetchone()
            if existing:
                db.commit()
                return dict(existing)
            db.execute("""INSERT INTO incidents
                (id, episode_id, signal, camera_id, zone_id, zone_name, landmark, source, detected_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'needs_responder')""",
                       (incident_id, incident_id, event.get("signal", "person_down"),
                        event.get("camera_id", "demo-camera"), event.get("zone_id", "east-bar"),
                        event.get("zone_name", "East bar"), event.get("landmark", "Beside the side stairs"),
                        event.get("source", "live"), detected_at))
            self._history(db, incident_id, "created", "camera", "Camera", presentation(event.get("signal"))[1])
            db.commit()
        return self.get_incident(incident_id)

    def get_incident(self, incident_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return dict(row) if row else None

    def transition(self, incident_id: str, actor: dict, action: str, outcome: str | None = None):
        actions = {"accept", "arrive", "resolve"}
        if action not in actions:
            return False, "Unknown action", None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
            if not row:
                db.rollback()
                return False, "Check not found", None
            if actor["role"] != "responder":
                db.rollback()
                return False, "Only responders can update a check", dict(row)
            if action == "accept":
                if row["status"] != "needs_responder":
                    db.rollback()
                    return False, "This check already has an owner", dict(row)
                db.execute("UPDATE incidents SET status='accepted', owner_id=?, owner_name=? WHERE id=? AND status='needs_responder'",
                           (actor["id"], actor["name"], incident_id))
                status, event = "accepted", "accepted"
            elif action == "arrive":
                if row["owner_id"] != actor["id"] or row["status"] != "accepted":
                    db.rollback()
                    return False, "Only the assigned responder can mark arrival", dict(row)
                db.execute("UPDATE incidents SET status='arrived' WHERE id=? AND owner_id=? AND status='accepted'",
                           (incident_id, actor["id"]))
                status, event = "arrived", "arrived"
            else:
                allowed = {presentation(row["signal"])[2], "False alarm", "Couldn't verify"}
                if outcome not in allowed:
                    db.rollback()
                    return False, "Choose a resolution outcome", dict(row)
                if row["owner_id"] != actor["id"] or row["status"] != "arrived":
                    db.rollback()
                    return False, "Only the assigned responder can resolve an arrived check", dict(row)
                db.execute("UPDATE incidents SET status='resolved', outcome=?, resolved_at=? WHERE id=? AND owner_id=? AND status='arrived'",
                           (outcome, iso(utc_now()), incident_id, actor["id"]))
                status, event = "resolved", "resolved"
            self._history(db, incident_id, event, actor["id"], actor["name"], outcome)
            db.commit()
        return True, "", self.get_incident(incident_id)

    def _history(self, db, incident_id, event, actor_id, actor_name, note=None):
        db.execute("INSERT INTO history(incident_id, event, actor_id, actor_name, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (incident_id, event, actor_id, actor_name, note, iso(utc_now())))

    def snapshot(self, viewer: dict, feed: dict):
        now = utc_now()
        with self.connect() as db:
            incidents = [dict(row) for row in db.execute(
                "SELECT * FROM incidents WHERE status != 'resolved' ORDER BY detected_at ASC").fetchall()]
            recent = [dict(row) for row in db.execute(
                "SELECT * FROM incidents WHERE status='resolved' ORDER BY resolved_at DESC LIMIT 8").fetchall()]
            history = [dict(row) for row in db.execute(
                "SELECT incident_id, event, actor_id, actor_name, note, created_at FROM history ORDER BY id DESC LIMIT 40").fetchall()]
        for item in incidents + recent:
            item["title"], item["observation"], outcome = presentation(item["signal"])
            item["outcomes"] = [outcome, "False alarm", "Couldn't verify"]
        for item in incidents:
            try:
                age = (now - datetime.fromisoformat(item["detected_at"].replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                age = 0
            item["overdue"] = item["status"] == "needs_responder" and age >= self.overdue_seconds
        return {"viewer": viewer, "feed": feed, "checks": incidents, "recent": recent,
                "history": history, "server_time": iso(now), "overdue_seconds": self.overdue_seconds}

    def count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]

    def seed_demo(self) -> None:
        if self.count():
            return
        self.create_incident({"episode_id": "sample-east-bar-001", "signal": "person_down",
                              "camera_id": "demo-camera", "zone_id": "east-bar", "zone_name": "East bar",
                              "landmark": "Beside the side stairs", "source": "rehearsal",
                              "detected_at": iso(utc_now() - timedelta(minutes=4))})
        self.create_incident({"episode_id": "sample-main-floor-002", "signal": "person_down",
                              "camera_id": "demo-camera", "zone_id": "main-floor", "zone_name": "Main floor",
                              "landmark": "Near the north tables", "source": "rehearsal",
                              "detected_at": iso(utc_now() - timedelta(seconds=42))})
