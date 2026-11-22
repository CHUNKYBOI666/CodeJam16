# Hosted staff app on Supabase: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anyone can scan a QR, type a name, and see a full-screen alert with sound the moment vision confirms a person down, then take the check with one winner.

**Architecture:** A static page (`frontend/`) talks to Supabase: anonymous sign-in, Postgres functions for every write, Realtime for push and presence. Vision on the Mac posts `create_check` and `report_feed` to the same project with the secret key. No server of our own.

**Tech Stack:** Supabase (Postgres, Realtime, anonymous auth) with the Supabase CLI and pgTAP for database tests; plain HTML/JS with `@supabase/supabase-js` v2 and Node's built-in `node:test`; Python 3.12 + pytest for the vision publisher.

**Spec:** [2026-11-21-supabase-live-alerts-design.md](../specs/2026-11-21-supabase-live-alerts-design.md). Read it first. It overrides this plan where they differ.

## Start here (for whoever picks this up)

The Flask app on `feat/join-by-name` is **reference only**. Its tests (`tests/test_store.py`, `tests/test_web.py`) describe the behaviour the new SQL functions must match: one winner, owner-only steps, rename keeps ownership, idempotent on `episode_id`.

```bash
git fetch
git switch -c feat/supabase-alerts origin/main
git checkout origin/feat/join-by-name -- docs/superpowers/specs/2026-11-21-supabase-live-alerts-design.md docs/superpowers/plans/2026-11-21-supabase-live-alerts.md
```

`main` has `vision/` (David) and no `backend/` or `frontend/`. Needs: Docker (for the local Supabase stack), the Supabase CLI, Node 20+, Python 3.12 (`uv`), and a Supabase project (free plan) with its URL, anon key and service-role key. **The service-role key lives only in `.env.local` and the Mac's shell. Never commit it and never put it in `frontend/`.** Branch conventions are in `CLAUDE.md` and the finish-and-demo plan: small commits, one PR per task, rebase on `origin/main` before pushing.

## Global Constraints

- Status values, exactly: `needs_responder`, `accepted`, `arrived`, `resolved`. Outcomes, exactly: `Confirmed person down`, `False alarm`, `Couldn't verify`.
- Copy, exactly: "Needs a responder", "You are on it" (the owner's own phone), "<Name> is on it", "<Name> accepted this first.", "Possible person down", "All quiet", "Someone may need help", "Not now", "Change name", "Bring someone in", "Camera offline".
- Names: trimmed, cut at 40 characters, blank refused, duplicates allowed. Everyone who has joined is a responder; there are no roles.
- Browsers never write tables directly. All writes go through functions; the actor always comes from `auth.uid()`.
- `create_check` and `report_feed` are executable only by the service role.
- Idempotency key is `episode_id`. Signal value from vision is `possible_person_down`.
- Timings: browser refetch every 5 s and on reconnect and tab-visible; camera heartbeat at most every 15 s; the UI treats the camera as offline after 40 s without an update.
- Pin `@supabase/supabase-js` to one exact 2.x version at least two weeks old. No unpinned CDN scripts.
- No map, no overdue badge, no roles, no frames leave the Mac.

## Review Focus

1. A name containing HTML such as `<img src=x onerror=alert(1)>` must render as text everywhere (Task 6, Task 7).
2. The same person double-tapping Accept, or open in two tabs, must get one owner and no error screen (Task 3).
3. A phone that was offline while a check was taken must not show an Accept button after reconnect, and an open takeover must close (Task 5, Task 6).
4. An event with an unknown `zone_id`, or posted twice, must create exactly one check with the default place (Task 2).
5. A missing, wrong or rotated room code, or an expired session, must show the join form with a plain message and expose nothing (Task 2, Task 4, Task 5).

---

## File structure

| Path | Responsibility |
| --- | --- |
| `backend/supabase/migrations/0001_schema.sql` | Tables, Realtime publication, seed settings |
| `backend/supabase/migrations/0002_functions.sql` | `join_room`, `create_check`, `report_feed`, `server_time`, accept/arrive/resolve |
| `backend/supabase/migrations/0003_security.sql` | RLS, grants, revokes |
| `backend/supabase/tests/*.sql` | pgTAP tests per task |
| `frontend/index.html`, `styles.css` | Page shell and the locked light/dark tokens |
| `frontend/js/state.js` | Pure state: merge snapshots, ignore stale responses, derive banner and views |
| `frontend/js/api.js` | The only file that calls Supabase |
| `frontend/js/alert.js`, `sound.js` | Takeover overlay, sound, vibration, title flash |
| `frontend/js/render.js` | Sections: banner, Checks, Camera, Here now, Bring someone in |
| `frontend/js/main.js` | Wiring |
| `frontend/test/*.test.js` | `node:test` unit tests |
| `vision/publish.py`, `tests/vision/test_publish.py` | Publisher used by `vision/run.py --publish` |
| `tools/mock_check.py` | Send a mock signal |

### Task 0: Skeleton, local stack, project

**Files:** Create `backend/supabase/config.toml` (via `supabase init`), `frontend/`, `.env.example`; modify `.gitignore` (add `.env.local`, `.superpowers/`, `node_modules/`); modify `README.md` (a "Hosted app" section with the commands below).

**Interfaces:**
- Produces: `.env.example` with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`; commands `supabase start` (from `backend/`), `supabase db reset`, `supabase test db`, `node --test frontend/test/`.

- [ ] **Step 1:** Create the branch (see Start here), run `supabase init` inside `backend/`, and commit the generated config.
- [ ] **Step 2:** Run `supabase start` from `backend/`. Expected: prints local API URL, anon key and service-role key.
- [ ] **Step 3:** Create the hosted Supabase project (free plan). In the dashboard: Authentication, enable **Allow anonymous sign-ins**; raise the anonymous sign-in rate limit above 30 per hour (venue Wi-Fi may share one IP). Record the URL and keys in `.env.local` (git-ignored).
- [ ] **Step 4:** Add a README section listing the commands in Interfaces. Commit "Add Supabase skeleton and env example".

### Task 1: Schema and seed

**Files:** Create `backend/supabase/migrations/0001_schema.sql`, `backend/supabase/tests/001_schema.test.sql`.

**Interfaces:**
- Produces tables `people`, `checks`, `check_events`, `feed_status` (single row, `id = 1`), `app_settings`, with the columns in the spec's D3. `checks.status` has a check constraint over the four statuses; `checks.episode_id` is unique. `checks` and `feed_status` are in the `supabase_realtime` publication. Seed: `app_settings` rows `room_code` (a random 8-character lowercase string), `default_place` = `Near the stage`.

- [ ] **Step 1: Write the failing test** `001_schema.test.sql` (pgTAP): `has_table` for each of the five tables; `col_is_unique('public','checks','episode_id')`; inserting a check with status `'bogus'` fails (`throws_ok`); `select count(*) from app_settings where key in ('room_code','default_place')` is 2; `checks` appears in `pg_publication_tables` for `supabase_realtime`.
- [ ] **Step 2:** Run `supabase test db`. Expected: FAIL (tables missing).
- [ ] **Step 3:** Write the migration.
- [ ] **Step 4:** Run `supabase db reset && supabase test db`. Expected: PASS. Commit "Add checks schema and seed".

### Task 2: Join, create_check, report_feed, server_time

**Files:** Create `backend/supabase/migrations/0002_functions.sql` (first half), `backend/supabase/tests/002_ingest.test.sql`.

**Interfaces:**
- Produces: `join_room(p_name text, p_room_code text) returns people` (security definer; requires `auth.uid()`; raises `Wrong room code` or `Type your name to join` on bad input; on a repeat call for the same user, updates `name` and the `owner_name` of that person's `accepted`/`arrived` checks only); `create_check(p_event jsonb) returns checks` (idempotent on `episode_id`; place from `app_settings` keyed by `p_event->>'zone_id'`, else `default_place`; inserts a `check_events` row `created`); `report_feed(p_status text, p_label text, p_fps numeric, p_source text) returns void`; `server_time() returns timestamptz`.

- [ ] **Step 1: Write the failing tests** (set the JWT with `set local request.jwt.claims` for a fake user id):
  - `join_blank_name_refused`, `join_wrong_code_refused`, `join_trims_and_caps_name_at_40`, `join_twice_renames_same_person`, `rename_updates_open_checks_not_resolved_ones`
  - `create_check_is_idempotent_on_episode_id` (call twice, one row, same id returned)
  - `create_check_unknown_zone_uses_default_place` (Review Focus 4)
  - `create_check_writes_history_row`
- [ ] **Step 2:** Run `supabase test db`. Expected: FAIL.
- [ ] **Step 3:** Implement the four functions. `create_check` uses `insert ... on conflict (episode_id) do nothing` then selects the row.
- [ ] **Step 4:** Run tests. Expected: PASS. Commit "Add join and ingest functions".

### Task 3: Accept, arrive, resolve

**Files:** Extend `0002_functions.sql`; create `backend/supabase/tests/003_actions.test.sql`.

**Interfaces:**
- Produces: `accept_check(p_id uuid) returns checks`, `arrive_check(p_id uuid) returns checks`, `resolve_check(p_id uuid, p_outcome text) returns checks`. Each requires the caller to exist in `people`, derives the actor from `auth.uid()`, writes a `check_events` row in the same transaction, and raises with the message the UI shows: `<Name> accepted this first.`, `Only the assigned responder can mark arrival`, `Choose a resolution outcome`, `Only the assigned responder can resolve an arrived check`.

- [ ] **Step 1: Write the failing tests:** `accept_sets_owner_and_history`; `second_accept_raises_winner_name` (error text equals `Jordan accepted this first.`); `owner_accepting_twice_is_not_an_error_screen` — decide in the function: a repeat accept by the owner returns the check unchanged (Review Focus 2); `non_owner_cannot_arrive`; `arrive_requires_accepted_state`; `resolve_rejects_unknown_outcome`; `resolve_requires_arrived_and_owner`; `race_two_users_one_winner` (two sessions each call accept in separate transactions using `dblink` or two connections in a small Node script `backend/supabase/tests/race.mjs` run 50 times; exactly one succeeds each time).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Accept is one `update ... where id = p_id and status = 'needs_responder' returning *`; if no row, read the current owner and raise.
- [ ] **Step 4:** Run `supabase test db` and `node backend/supabase/tests/race.mjs`. Expected: all pass, 50/50 single winners. Commit "Add check action functions".

### Task 4: Security

**Files:** Create `backend/supabase/migrations/0003_security.sql`, `backend/supabase/tests/004_security.test.sql`.

**Interfaces:**
- Produces: RLS enabled on all five tables; `select` on `checks`, `check_events`, `people`, `feed_status` only for users who exist in `people`; `app_settings` readable by nobody but the functions; no insert/update/delete policies for `anon` or `authenticated`; `revoke execute` on `create_check` and `report_feed` from `public, anon, authenticated`, with `grant execute` to `service_role`; `grant execute` on `join_room`, `server_time` and the three actions to `authenticated`.

- [ ] **Step 1: Write the failing tests** as role `authenticated` with a user not in `people`: `unjoined_user_cannot_select_checks` (zero rows); `joined_user_can_select_checks`; `client_cannot_insert_update_or_delete_checks`; `client_cannot_call_create_check` and `client_cannot_call_report_feed` (`throws_ok`); `client_cannot_read_room_code`; as `anon`: no `select` anywhere.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Write the migration. Run `supabase db reset && supabase test db`. Expected: PASS. Commit "Lock down tables and functions".
- [ ] **Step 4:** Push all migrations to the hosted project (`supabase link` then `supabase db push`) and confirm in the dashboard that Realtime is on for `checks` and `feed_status`.

### Task 5: Frontend state and API (no UI yet)

**Files:** Create `frontend/js/state.js`, `frontend/js/api.js`, `frontend/test/state.test.js`, `frontend/config.js` (public URL and anon key only).

**Interfaces:**
- `state.js` exports `createStore()` returning `{ applySnapshot(seq, checks), applyChange(change), view() }` where `view()` returns `{ banner: 'quiet'|'waiting', waiting: Check[], inProgress: Check[], closed: Check[], alertCheck: Check|null }`. `applySnapshot` ignores a snapshot whose `seq` is lower than the last applied.
- `api.js` exports `signIn()`, `join(name, roomCode)`, `fetchChecks()`, `subscribe({onChange, onPresence, onFeed, onStatus})`, `accept(id)`, `arrive(id)`, `resolve(id, outcome)`, `serverTime()`. It is the only file that imports supabase-js.

- [ ] **Step 1: Write the failing tests** (`node:test`): `stale_snapshot_is_ignored`; `insert_waiting_check_sets_alertCheck`; `update_to_accepted_clears_alertCheck` (Review Focus 3); `snapshot_after_reconnect_removes_taken_check_from_waiting`; `closed_checks_are_listed_newest_first`; `banner_is_waiting_when_any_check_needs_responder`.
- [ ] **Step 2:** Run `node --test frontend/test/`. Expected: FAIL.
- [ ] **Step 3:** Implement `state.js` as pure functions. Implement `api.js` with the pinned supabase-js import. Session storage is supabase-js's default (survives refresh). On an expired or missing session, `signIn()` creates a new anonymous user and the UI sends the person back to the join form.
- [ ] **Step 4:** Run the tests. Expected: PASS. Commit "Add frontend state and Supabase client".

### Task 6: Join, takeover and sections

**Files:** Create `frontend/index.html`, `styles.css`, `js/render.js`, `js/alert.js`, `js/sound.js`, `js/main.js`; test `frontend/test/render.test.js`.

**Interfaces:**
- Consumes: Task 5's `createStore`, `api`.
- Produces: `render.js` exports `escapeText(s)`, `renderChecks(view, viewerId)`, `renderBanner(view)`. `alert.js` exports `showTakeover(check, {onAccept, onNotNow})` and `hideTakeover()`. `sound.js` exports `unlockAudio()` (called from the Join button's click handler) and `playAlert()`.

- [ ] **Step 1: Write the failing tests:** `name_with_html_renders_as_text` (Review Focus 1: the output contains `&lt;img` and no `<img`); `owner_row_reads_You_are_on_it`; `other_row_reads_Name_is_on_it`; `waiting_row_has_Accept_button_only_for_waiting`; `owner_arrived_row_has_three_outcome_buttons_with_exact_labels`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Join page: read `?room=` once, then `history.replaceState` to remove it from the address; one name field (`maxlength=40`) and **Join**; wrong code shows "That link has expired. Ask for a new QR code." Takeover: full-screen coral per spec D5, **Accept** and **Not now**. **Not now** hides it locally only. `main.js` refetches `checks` every 5 s and on reconnect and tab-visible, tagging each fetch with an increasing `seq`. Detection-to-screen time is measured with `serverTime()` to cancel clock drift and shown small in the Camera section. Title flashes while any check is waiting. Use `navigator.vibrate` if it exists. **Change name** returns to the join form prefilled and calls `join` again. A losing Accept shows "<Name> accepted this first." inline. Styles reuse the tokens from `src/codejam16/web/static/staff.css` on `feat/join-by-name` (`--own` teal, `--wait` coral, DM Sans, dark mode blocks).
- [ ] **Step 4:** Run the tests. Then serve `frontend/` locally (`npx serve frontend`) against the local stack, join in two browser profiles, and create a check with the SQL editor. Expected: takeover on both, Accept on one closes the other. Commit "Add join, takeover and checks list".

### Task 7: Presence, camera, QR

**Files:** Modify `render.js`, `main.js`; create `frontend/js/qr.js`; test `frontend/test/camera.test.js`.

**Interfaces:**
- `render.js` adds `renderCamera(feed, nowMs)` returning `{ text, tone }` (`"Live · 12 fps"` / `"Camera offline"` when `nowMs - feed.updated_at > 40_000`) and `renderHere(people)`. `qr.js` exports `renderQr(el, url)` using one pinned QR library.

- [ ] **Step 1: Write the failing tests:** `camera_is_offline_after_40_seconds`; `camera_shows_fps_when_live`; `here_now_escapes_names` (Review Focus 1).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. "Here now" comes from Presence on one channel (`room`), tracked with the person's name after join. **Bring someone in** shows the QR of `${location.origin}/?room=<code>` and the address in plain text. The room code is read through a `join_room`-style function, not a table read: add `get_invite_code() returns text` (authenticated, joined users only) to `0002_functions.sql` with a pgTAP test `unjoined_cannot_get_invite_code`.
- [ ] **Step 4:** Run `node --test frontend/test/` and `supabase test db`. Expected: PASS. Commit "Add presence, camera status and invite QR".

### Task 8: Vision publisher and mock signal

**Files:** Create `vision/publish.py`, `tests/vision/test_publish.py`, `tools/mock_check.py`; modify `vision/run.py` (add `--publish`).

**Interfaces:**
- `vision/publish.py`: `class Publisher: def __init__(self, url: str, service_key: str, post=requests.post, sleep=time.sleep)`; `publish_check(self, event: dict) -> dict` (POST `{url}/rest/v1/rpc/create_check` with `{"p_event": event}`, headers `apikey` and `Authorization: Bearer <key>`; retries up to 5 times with the same `episode_id`; raises `PublishError` after that); `report_feed(self, status: str, label: str, fps: float, source: str) -> None` (rate-limited to once per 15 s unless `status` changed).
- `tools/mock_check.py`: `python tools/mock_check.py [--zone-id demo-area]` builds an event from `fixtures/person-down.json` with a fresh `episode_id` and calls `publish_check`.

- [ ] **Step 1: Write the failing tests** with a fake `post`: `publish_check_sends_rpc_with_service_key_header`; `publish_check_retries_with_same_episode_id`; `publish_check_raises_after_five_failures`; `report_feed_is_rate_limited_to_15_seconds`; `report_feed_sends_immediately_on_status_change`.
- [ ] **Step 2:** Run `pytest tests/vision/test_publish.py`. Expected: FAIL.
- [ ] **Step 3:** Implement. In `run.py`, `--publish` reads `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` from the environment, calls `publish_check` where the event is currently printed, and `report_feed` on status changes. Without `--publish`, behaviour is unchanged (JSON lines on stdout). The key is never logged.
- [ ] **Step 4:** Run the whole suite (`pytest`). Expected: PASS. Against the local stack, run `python tools/mock_check.py` and confirm a row appears. Commit "Publish checks and camera status to Supabase".

### Task 9: Deploy and rehearse

**Files:** Modify `README.md` (deploy steps and the rehearsal checklist); create `docs/rehearsal-log.md` entries as you go.

- [ ] **Step 1:** Deploy `frontend/` to a static host (Netlify or Vercel). Set the final URL as the Supabase Auth site URL. Record the address in the README.
- [ ] **Step 2:** Generate the printed QR from `https://<site>/?room=<code>` and keep a rotation note: to rotate, update the `room_code` row and reprint.
- [ ] **Step 3:** Run the spec's success criteria 1 to 9 on real devices: two phones on venue Wi-Fi, one on cellular, plus the Mac running `vision/run.py --publish` on the staged clip, then the live iPhone camera. Log results and the measured detection-to-screen time in `docs/rehearsal-log.md`.
- [ ] **Step 4:** Day before and morning of: open the hosted project in the dashboard to wake it if paused, run `supabase test db` against local, and send one mock check end to end. Keep the Flask LAN fallback (`feat/join-by-name`, `python -m codejam16.web.app --no-detector --with-sample`) ready.
- [ ] **Step 5:** Open the PR. Commit "Document hosted deploy and rehearsal".
