# Walk-up Join Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace invite-link join and the Supervisor role with a name-only walk-up join, and replace the staff UI with the approved minimal Checks · Map design.

**Architecture:** Same Flask app, same `/api/state` and `/api/checks/<id>/actions`. The store gains `join_by_name`, loses invitations and the role check. The templates, CSS and JS are rewritten small. The detector, worker, venue config and one-winner claim are untouched.

**Tech Stack:** Python 3.12, Flask, SQLite, vanilla JS, `unittest`.

**Spec:** [2026-11-21-walk-up-join-design.md](../specs/2026-11-21-walk-up-join-design.md). Mockup: https://claude.ai/artifact/AwNzxTVD11RD8i6zdkdtb6

## Global Constraints

- Test command, green after every task: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- Everyone who joins has role `responder`. The `role` column stays and always holds `responder`.
- Names: trimmed, cut at 40 characters, blank refused. Duplicates allowed.
- Session cookie unchanged: name `codejam_staff`, HttpOnly, SameSite=Lax, 7 days. CSRF header `X-CSRF-Token` and Origin check unchanged on actions.
- Status words: `needs_responder` → "Needs a responder"; `accepted`/`arrived` → "<Name> is on it" ("You are on it" for the owner). Outcomes, exact: `Confirmed person down`, `False alarm`, `Couldn't verify`.
- Default port 8016. No sample check unless `--with-sample`.
- Keep `static/latency.js` and its test.

## Review Focus

1. **Re-join on the owner's phone** must keep the same person id so Arrive and Resolve still work (Task 1, `test_rejoin_keeps_person_and_ownership`).
2. **Rename updates open checks' owner name** but not closed ones (Task 1).
3. **A cross-origin `POST /join`** is refused with 403 and creates nobody (Task 2).
4. **An old `/join/<token>` link** shows the form with 200, not an error (Task 2).
5. **A stale cookie after a database reset** sends the person to `/join` and does not crash (Task 2).

## File map

| File | Task |
| --- | --- |
| `src/codejam16/store.py`, `tests/test_store.py` | 1 |
| `src/codejam16/web/app.py`, `tests/test_web.py` | 2 |
| `src/codejam16/web/templates/join.html`, `templates/app.html`, `static/staff.css`, `static/staff.js` | 3 |
| `README.md`, `.claude/launch.json` | 4 |

---

### Task 1: Join by name, equal roles, winner message

**Files:** Modify `src/codejam16/store.py`, `tests/test_store.py`.

**Interfaces:**
- Produces: `IncidentStore.join_by_name(name: str, existing_token: str | None = None) -> tuple[str, str, dict] | None`. Returns `(raw_session_token, csrf, person)` where `person = {"id", "name", "role": "responder"}`. Returns `None` for a blank name. With a valid `existing_token`, keeps that person's id and session, updates the name, and returns the same raw token.
- Produces: `IncidentStore.transition(...)` unchanged signature. Any person may accept. Losing accept message is `"<owner_name> accepted this first."`.
- Removes: `ensure_invitation`, `invitation`, `create_session`, the `invitations` table.

- [ ] **Step 1: Write failing tests** in `tests/test_store.py`. Delete `test_supervisor_cannot_act` and the `MORGAN` actor. Add class `JoinTests(StoreTestCase)`:
  - `test_blank_name_refused`: `join_by_name("   ")` is `None` and `get_session` has no new rows.
  - `test_name_trimmed_and_capped`: `join_by_name("  " + "x" * 60 + " ")` person name is `"x" * 40`.
  - `test_two_people_same_name_are_distinct`: two joins of `"Alex"` give different ids, both named Alex.
  - `test_rejoin_keeps_person_and_ownership`: join Alex, accept a check, re-join with the same token as `"Sam"`: same id and raw token, `get_session` name is `"Sam"`; Sam can `arrive` and `resolve`.
  - `test_rename_updates_open_checks_only`: Alex owns one open check and one resolved check, then renames to Sam: the open check's `owner_name` is `"Sam"`, the resolved check still `"Alex"`.
  - `test_invalid_existing_token_creates_new_person`: a bogus `existing_token` yields a new person.
  Update `ClaimTests.test_concurrent_accept_has_one_winner` to expect the loser's message `f"{winner_name} accepted this first."`; add `test_anyone_can_accept`: a person with role `supervisor` (in the actor dict) can accept.
- [ ] **Step 2: Run** `PYTHONPATH=src:tests .venv/bin/python -m unittest test_store -v`. Expected: FAIL (`join_by_name` missing).
- [ ] **Step 3: Implement** `join_by_name` in `store.py` in one `BEGIN IMMEDIATE` transaction: resolve the existing session row by hash; if found, `UPDATE sessions SET name=?` and `UPDATE incidents SET owner_name=? WHERE owner_id=? AND status IN ('accepted','arrived')`; else insert a new session with `staff_id = "p-" + secrets.token_hex(6)`, role `responder`, a new raw token and csrf. Remove the role check from `transition`, change the accept-lost message, remove invitation code and its table.
- [ ] **Step 4: Run the full suite.** `test_web.py` will fail until Task 2; run only `test_store` here. Expected: PASS.
- [ ] **Step 5: Commit** `"Join by name with equal roles"`.

### Task 2: Join routes, origin check, port, startup address

**Files:** Modify `src/codejam16/web/app.py`, `tests/test_web.py`.

**Interfaces:**
- Consumes: `join_by_name` (Task 1).
- Produces: `GET /join` and `GET /join/<anything>` → 200 form. `POST /join` (form field `name`) → 302 to `/` and sets the cookie; blank name → 400 with the form and an error; bad Origin → 403. `GET /` and `GET /map` → the app page (`initial_tab` `checks` / `map`); no valid session → 302 `/join`. `/supervisor`, `/logout` removed. `create_app(..., seed_demo=False)` default. `main()` default port 8016, new `--with-sample` flag, prints `Join at http://<lan-ip>:8016/join`.
- Produces: `lan_address() -> str` in `app.py` (UDP-connect trick to 10.255.255.255, falling back to `127.0.0.1`).

- [ ] **Step 1: Rewrite `tests/test_web.py`'s helpers and tests.** `join(client, name="Ada")` posts `/join` with `data={"name": name}` and returns `(response, csrf)`. Replace the token tests:
  - `test_old_token_link_shows_form`: `GET /join/responder-ada` is 200 and contains `name="name"`.
  - `test_join_sets_cookie_and_lands_on_checks`: 302 to `/`, cookie HttpOnly.
  - `test_blank_name_creates_nobody_400`.
  - `test_cross_origin_join_403`: `Origin: http://evil.test`, no cookie set.
  - `test_refresh_keeps_person`: joined client's `/api/state` viewer name survives a second request.
  - `test_rejoin_same_browser_renames_and_keeps_ownership`: accept as Ada, re-join as "Sam", Sam can arrive.
  - `test_other_phone_unchanged_after_rejoin`: two clients, rename one, the other's viewer unchanged.
  - `test_stale_cookie_redirects_to_join`: set cookie `codejam_staff=bogus`, `GET /` is 302 to `/join`.
  - `test_map_route_serves_app`: `/map` 200 for a joined client.
  - `test_two_clients_race_one_owner` loser body error is `"<Winner> accepted this first."`.
  - Delete `test_supervisor_action_409` and `test_invalid_join_404`. `WorkflowTests` uses `join(client, "Ada")` / `"Jordan"`.
  - `test_lan_address_is_an_ip`: `lan_address()` matches `^\d+\.\d+\.\d+\.\d+$`.
- [ ] **Step 2: Run** `PYTHONPATH=src:tests .venv/bin/python -m unittest test_web`. Expected: FAIL.
- [ ] **Step 3: Implement** the routes above. `POST /join` reads the existing cookie and passes it as `existing_token`; the response sets the cookie with the returned raw token. Remove `DEFAULT_INVITES`, `INVITE_TOKENS`, `/supervisor`, `/logout`. `app.html` receives `viewer` and `initial_tab`.
- [ ] **Step 4: Run the full suite** (templates still old, the pages render with the old template, which is fine for tests). Expected: PASS.
- [ ] **Step 5: Commit** `"Walk-up join routes, port 8016 and startup address"`.

### Task 3: Minimal UI

**Files:** Rewrite `templates/join.html`, `templates/app.html`, `static/staff.css`, `static/staff.js`. Keep `static/latency.js`.

**Interfaces:** Consumes `GET /api/state` (`viewer`, `checks`, `recent`, `feed`, `zones`, `server_time`, `csrf_token`) and `POST /api/checks/<id>/actions` (`{action, outcome?}` → `{check, state}` or `{error, check}`).

Design (from the approved mockup): tokens `--bg #f6f7f4 --screen #fff --ink #161a17 --mute #737b75 --line #e4e8e2 --own #0f766e --wait #d4604a`, with dark-mode redefinitions; DM Sans; one big place name per screen; text tabs; phones show Checks · Map tabs; from 900 px wide both panes sit side by side and the tabs hide.

- [ ] **Step 1: `join.html`**: title "Join the welfare check", one `name` input (`maxlength="40"`, `autocomplete="off"`, `autofocus`, `required`), a Join button, the note "This phone stays <current name or 'the name you type'>. Another person needs their own phone.", an error line when present, a prefilled name when the viewer already has a session. No role control.
- [ ] **Step 2: `app.html`**: header with the viewer name and a **Change name** link to `/join`, the tab bar, a `#checks` pane, a `#map` pane, a feed line (label and fps, small, at the bottom), the `latency.js` and `staff.js` scripts, `data-initial-tab`.
- [ ] **Step 3: `staff.js`** (small, one file, under about 250 lines):
  - Poll `/api/state` every 1200 ms; ignore a response older than the newest request; mark Reconnecting on failure; keep the `X-CSRF-Token` from the snapshot.
  - Checks pane: empty state text from D13; each check shows state dot and word, the zone name large, landmark, and the button for the viewer: `needs_responder` → **Accept**; owner and `accepted` → **I have arrived**; owner and `arrived` → three outcome buttons; non-owner → "<Owner> is on it" and no button. The owner sees "You are on it".
  - A failed accept shows the server's error line ("<Name> accepted this first.") inline and refreshes state.
  - Map pane: one pin per `snapshot.zones` entry (SVG), coral when its checks need a responder, teal when being handled, with a count; tapping a pin switches to the Checks tab and scrolls to that check.
  - Detection → screen from `latency.js` shown small under the zone name for live checks (as today).
  - Escape all inserted text.
- [ ] **Step 4: `staff.css`**: styles for the above, tokens in bare `:root` plus the two dark blocks, focus-visible outlines, 44 px tap targets, no horizontal scroll at 360 px.
- [ ] **Step 5: Browser check** (`preview_start`, two profiles are simulated with two cookie jars via the API for the second person): at 375 px and at 1200 px; screenshots of join, waiting, race win and loss, map. No console errors.
- [ ] **Step 6: Run the full suite. Commit** `"Replace staff UI with minimal Checks and Map design"`.

### Task 4: Docs and launch

- [ ] **Step 1: README**: replace the Staff app section with the walk-up join flow (one address, name, Join), `--with-sample` and `--no-detector`, port 8016, the "Join at http://…" startup line, and the laptop fallback (two Chrome profiles, each joins with its own name). Update the Quick start launch commands to the new defaults.
- [ ] **Step 2: `.claude/launch.json`** uses port 8016 and `--with-sample`.
- [ ] **Step 3: Run the full suite. Commit** `"Document walk-up join and launch flags"`.
