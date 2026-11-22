# Design: Hosted staff app on Supabase, with a live alert for everyone signed in

Written 21 Nov 2026. **Status: approved to start (user asked to push for implementation, 21 Nov 2026). The four open decisions at the end still need answers.** Builds on the approved [live venue welfare checks design](../../designs/live-venue-welfare-checks.md) and `CLAUDE.md` on `origin/main`, which override this file where they differ. It supersedes the Flask/SQLite/LAN staff app for the demo and replaces the walk-up-join Flask design ([2026-11-21-walk-up-join-design.md](2026-11-21-walk-up-join-design.md)) for everything except its intent.

## Intent

In the demo, strangers in an open room open the app on their own phones, some on venue Wi-Fi and some on cellular data. The moment the camera confirms a person down, every phone that has the app open must show an unmissable alert, and one person must be able to take the check with everyone else seeing who has it.

Phones on cellular cannot reach a laptop on the venue Wi-Fi, so the app has to be hosted. The vision program on the Mac stays local and only sends the small event it already produces.

**The moment that matters:** a stranger scans a QR, types a name, and within ten seconds is on the screen that takes over, with a sound, when the camera sees someone go down.

## Goals

- One public address that works from Wi-Fi and cellular and can be printed as a QR code in advance.
- Join with a name only. No roles, no invite links, no passwords.
- Live alert on every open phone within about one second of the check being created.
- One winner when two people tap Accept, owner-only Arrived and Resolve, and the same status steps as the approved design: Needs responder, Accepted, Arrived, Resolved.
- A "Here now" list and a camera status, both live.
- A mock signal command, so the whole path can be rehearsed with no camera.
- Minimal, sectioned UI (layout C below).

## Non-goals

Supervisor or admin roles, a floor map and calibrated zones, the overdue badge (cut by `CLAUDE.md`), native or background push notifications, more than one camera, any video or frame leaving the Mac, the Gemini description (stretch in `CLAUDE.md`), and a per-person history beyond the closed list.

## Anti-goals (failure even if it works)

- An alert a person cannot dismiss, or that blocks the person who is mid-way through resolving another check.
- A check nobody can finish, for example because someone renamed themselves or reloaded.
- A false alert on stage. Detector behaviour is unchanged and owned by `vision/`.
- The Supabase secret (service role) key in the browser, in Git, or in a screenshot.
- A join that needs anything beyond the QR and a name.

## Constraints

- **Time:** hackathon, about 18 hours remaining at the time of writing.
- **Devices:** guests' own phones on Wi-Fi or cellular. The Mac needs an outbound internet connection.
- **Stack on `main`:** `vision/` (David) already exists. `backend/` and `frontend/` do not exist yet and are free to define. `CLAUDE.md` says Flask and SQLite. **This design changes that stack for the demo.** Nicolas owns the staff app, so he must agree (see Open decisions).
- **Supabase free plan** (checked 21 Nov 2026 against Supabase's docs; re-check before the demo): 200 concurrent Realtime connections, 100 messages per second, 20 Presence messages per second, a free project pauses after a week of low activity, and the anonymous sign-in limit defaults to 30 requests per hour per IP address. Venue Wi-Fi may share one public IP, so raise that limit in the dashboard.
- **Privacy:** only event metadata (id, time, a label, a source) leaves the Mac. No frames.

## Approaches considered

| | Approach | Trade-off | Verdict |
| --- | --- | --- | --- |
| A | Keep the Flask app on the Mac and expose it through a tunnel (Cloudflare, ngrok, Tailscale Funnel) | No rewrite and data stays local. The public address changes unless an account or domain is reserved, so a QR printed in advance can break. The Mac is a single point of failure for everyone. | Rejected |
| B | Host the Flask app and SQLite on a small cloud host | Reuses the tested code. Needs a host with long-lived connections and a persistent disk, exactly one instance, and we still own push, presence and sessions. | Rejected |
| C | **Supabase only:** a static page plus Supabase for the database, live push, presence and sessions. Vision posts events to it. | Rewrites the backend as SQL and rewrites the frontend. In exchange there is nothing to host or keep running, and push, presence and sessions come built in. | **Chosen** |

The user chose C after seeing all three.

## Decisions

### D1. Architecture

```text
Mac: iPhone camera -> vision/ ──HTTPS POST (secret key)──> Supabase Postgres
                                                              │  Realtime push
phones (Wi-Fi or cellular) <── static page, supabase-js ──────┘  + Presence
```

- Vision is a client. It calls two Postgres functions over Supabase's REST interface: `create_check` and `report_feed`. They are not callable by browsers.
- Browsers are clients. They sign in anonymously, call `join_room`, read `checks`, subscribe to changes on `checks` and `feed_status`, and call the three action functions.
- `episode_id` is the idempotency key. Posting the same event twice returns the saved check.

### D2. Sign-in

- The QR code encodes `https://<site>/?room=<code>`. The page reads the code, signs the browser in anonymously, and shows a single name field and **Join**.
- `join_room(name, room_code)` checks the code against a server-side setting, stores `(auth user id, name)` in `people`, and returns the person. A wrong or missing code joins nobody.
- A browser keeps its identity across refresh through the Supabase session. **Change name** calls `join_room` again with the same user: the person is the same, the name changes, and the name on that person's open checks changes with it. Closed checks keep the name they were closed under.
- Names are trimmed and cut at 40 characters. A blank name is refused. Duplicate names are allowed.
- Roles are removed. Everyone who has joined can Accept.

### D3. Data and write rules

Tables (names are a proposal; the builder may adjust column details):

- `people(id uuid primary key references auth.users, name text, joined_at)`
- `checks(id uuid primary key, episode_id text unique, signal text, camera_id text, place text, source text, detected_at timestamptz, created_at timestamptz default now(), status text, owner_id uuid, owner_name text, outcome text, resolved_at timestamptz)`
- `check_events(id, check_id, event, actor_id, actor_name, note, created_at)`, the history
- `feed_status` (one row): `status`, `label`, `fps`, `source`, `updated_at`
- `app_settings(key, value)` holding the room code

Rules:

- Browsers cannot insert, update or delete any table directly. Row-level security allows `select` only, and only to users who exist in `people`. All writes go through functions.
- `create_check(event)` and `report_feed(...)` are executable only by the service role.
- `accept_check(id)`: a single guarded update from `needs_responder` to `accepted` that sets the owner from `auth.uid()`, plus the history row, in one transaction. If the check is no longer available, it raises `<Name> accepted this first.`
- `arrive_check(id)`: owner only, from `accepted`. `resolve_check(id, outcome)`: owner only, from `arrived`, with the outcomes `Confirmed person down`, `False alarm`, `Couldn't verify`.
- The actor is always derived from the session. No function accepts an actor name from the client.

### D4. Live updates

- **Alert:** clients subscribe to Realtime changes on `checks`. An insert with status `needs_responder` raises the alert. An update that moves the check out of `needs_responder` dismisses it on every other phone.
- **Safety net:** on subscribe, reconnect, a tab becoming visible again, and every five seconds, the client refetches current `checks` and replaces its state. A missed event cannot leave a stale button. A stale response must not replace a newer one (carry the sequencing rule from `staff.js`).
- **Here now:** Realtime Presence on one channel, with the person's name.
- **Camera:** `feed_status` changes drive the Camera section. Vision calls `report_feed` on a change and as a heartbeat no more often than every 15 seconds. The UI shows "Camera offline" when the last update is older than 40 seconds. The interval is chosen so that heartbeats do not consume the free message quota with 200 listeners.

### D5. The alert (screen A)

- Full-screen coral takeover: "Needs a responder", "Possible person down", the place label, and how long ago. One large **Accept** and a quiet **Not now**.
- A sound plays when the alert appears. Browsers block audio until a person taps, so the Join tap unlocks it. Vibration is used where the browser supports it. The page title flashes while a check is waiting.
- **Not now** hides the takeover on that phone only. The check stays coral in Checks. It does not re-alert.
- If someone else accepts first, the takeover closes everywhere else and the row reads "<Name> is on it". A person who lost the race sees "<Name> accepted this first."
- Alerts reach only phones with the page open. This is accepted.

### D6. Screens (layout C)

```text
┌ Alex                Change name ┐
│ Someone may need help           │  banner: teal "All quiet", coral when a check waits
│ Checks                          │
│   Possible person down  [Accept]│
│   Jordan is on it · 3 min       │
│ Camera     ● Live · 12 fps      │
│ Here now · 3   Alex  Jordan  Sam│
│ Bring someone in                │  large QR and the plain address
└─────────────────────────────────┘
```

- Sections, not cards-inside-cards. One accent: teal means someone is on it and coral marks only what is waiting. DM Sans, light and dark themes, tokens as in the existing `staff.css`. The mockups are in `.superpowers/brainstorm/` for reference and are not part of the repo.
- No map. The place is a single label from configuration, for example "Near the stage". There is no time to calibrate in an open room.
- **Bring someone in** shows a QR and the address, generated in the browser, so a signed-in phone or the projector laptop can bring in the next person.
- Fonts and scripts load from a pinned source, or are vendored. The page must still render if a CDN is slow.

### D7. Where it lives on `main`

```text
backend/supabase/migrations/   schema, functions, policies, seed
backend/supabase/tests/        database tests (single winner, owner-only, idempotency)
frontend/                      static page: index.html, app.js, styles.css, alert.js
vision/                        David: add a publisher and a --publish flag to run.py
tools/mock_check.py            send a mock signal to the hosted project
```

- The Flask app under `src/codejam16/web/` and its store stay on `feat/join-by-name` as the reference. Its tests for the one-winner race, owner-only steps, rename, and idempotency are the acceptance spec for the new functions. It also remains a LAN fallback if Supabase or the internet fails. No new features go into it.
- `vision/` is unchanged apart from the publisher. The contract is the fixture `fixtures/person-down.json` (`signal` is `possible_person_down`).

## Success criteria

1. A fresh phone scans the QR, types a name, and is on Checks in under ten seconds, on Wi-Fi and on cellular.
2. `mock_check` creates a check, and every open phone shows the takeover with a sound within about one second. Record the measured time.
3. Two phones tap Accept together: one owner, and the other phone shows "<Name> accepted this first." Both Checks lists agree.
4. The owner refreshes, still owns the check, and can mark Arrived and then Resolve. Others cannot.
5. Change name on the owner's phone keeps ownership and updates the displayed name.
6. A client with no room code, a wrong code, or no session cannot read checks or call any action.
7. Turning off Wi-Fi on one phone and back on leaves it showing current state within five seconds.
8. The real camera, through vision, creates one check for one staged episode, and posting the same event twice creates one.
9. The secret key appears nowhere in the repository or the frontend.

## Left to the builder

The exact SQL and column types; the sound and alert animation; the QR library; the static host (Netlify or Vercel are fine choices, GitHub Pages needs a public repository); whether database tests use pgTAP through the Supabase CLI or another Postgres harness; the polling interval within the stated limits; how detection-to-screen time is measured given that the Mac clock and the phone clock differ (suggest a server-time function); and the file layout inside `frontend/`.

**My calls, marked for you to check:** `create_check` takes the place label from the event's `zone_id` looked up in `app_settings`, falling back to a `default_place` setting, so vision needs no label of its own; the room code is a short random string rotated by editing one row; the audio unlock happens on the Join tap; the free-tier numbers above come from Supabase's published docs and not from a test of our project.

## Open decisions (theirs)

1. **Nicolas agrees to the stack change** from Flask/SQLite to Supabase, since `CLAUDE.md` names Flask and SQLite.
2. **A Supabase project and an owner** for its dashboard and secret key. The free plan allows two active projects.
3. **The place label** shown on a check, such as "Near the stage".
4. **Whether to raise the anonymous sign-in limit** and enable CAPTCHA. The default limit may block a room of people sharing one IP address.
