# Hosted staff app refactor execution plan

> **For implementers**
> Use the Superpowers executing-plans workflow. The linked implementation plan supplies the task-level interfaces and tests. Apply the corrections and release gates below where the documents differ.

**Goal**
Let guests on venue Wi-Fi and cellular scan a QR, join by name, receive a live person-down alert and coordinate one shared welfare check.

**Architecture**
A static frontend uses Supabase Auth, Postgres RPCs and Realtime. The Mac runs the existing local detector and sends event metadata and feed health. The existing Flask/SQLite app remains the rehearsal-network fallback.

**Tech stack**
Supabase CLI and pgTAP, static HTML/CSS and pinned JavaScript dependencies, Node tests, Python 3.12 and pytest.

**Source documents**
[Supabase implementation plan](2026-11-21-supabase-live-alerts.md) · [Supabase design](../specs/2026-11-21-supabase-live-alerts-design.md) · [Original welfare-check design](../../designs/live-venue-welfare-checks.md). Review the last document and `CLAUDE.md` at the fetched `origin/main` revision.

## 1. Settle the implementation baseline

- [ ] Fetch both branches again at execution time. At planning time `origin/main` is `64e4a6b` and `feat/join-by-name` is `f536149`. Start hosted implementation from current `origin/main` on a new feature branch, as the source plan directs. Bring over the two Supabase documents and this companion plan. Use the join branch's store, web and worker tests as behavioral references.
- [ ] Record Nicolas's approval of hosted Supabase and removal of the map in the project guidance. Update active scope and launch guidance in `CLAUDE.md` and `README.md` on the implementation branch. The historical design still describes the original LAN demo. Coordinate shared-file changes with the integration owner and publisher changes with David.
- [ ] Use `origin/main:fixtures/person-down.json` as the contract. It carries `signal: "possible_person_down"` and `zone_id: "demo-area"`. The join branch has an older fixture with `"person_down"` and `"east-bar"`. Exercise the canonical fixture in SQL, publisher and mock tests.
- [ ] Establish the local gate. Confirm Docker, the Supabase CLI, Node and Python 3.12 are available. Run the existing vision suite from the main-based branch and the Flask store and web tests from the fallback checkout. Record the commands and results before implementation.

## 2. Build the local vertical slice

- [ ] **Tasks 0–4, database and auth.** Build `backend/supabase/` with migrations, pgTAP tests and the independent-session race harness. Create incident state and history in one transaction. Use a guarded claim update and derive the actor from `auth.uid()`. A repeat claim by the owner returns the existing check. A losing claim identifies the current owner. Arrived and Resolve require the owner and the expected preceding state. Resolve stores one of the three specified outcomes and its actor and time. Reuse `episode_id` across retries; append the creation history once.
- [ ] Put RPC grants and RLS in place before applying migrations to a hosted project. Explicitly revoke default function execution before granting each intended role. Give joined members only the needed reads and RPCs, keep settings private, and restrict ingest RPCs to the service role. Use a membership check that avoids recursive policies on `people`. Provision a real room code through private settings. Local tests can use a known fixture code. Verify unjoined and unauthenticated access, invalid or missing room codes, forbidden table writes, transactional rollback and 50 two-session races.
- [ ] **Tasks 5–6, join and alert.** Keep Supabase calls in `frontend/js/api.js`, state in `state.js`, and rendering and takeover behavior in their own modules. Use the existing staff CSS tokens and the hosted design's sections. Join by name and keep the anonymous session across refresh; a rename retains the person ID and updates only open owner labels. Restore existing membership after refresh without requiring the QR again. Acquire the invite through the joined-only RPC for Change name and the QR, so both work after the query string is removed. A browser without a valid session or invite returns to Join.
- [ ] Keep waiting, in-progress and closed checks on one shared model. Reconcile Realtime and snapshots on subscribe, reconnect, tab visibility and the five-second safety poll. Invalidate any fetch started before a newer Realtime change so it cannot resurrect a taken check. A waiting check found in a recovery snapshot can still alert. Dismiss by check ID for that phone, preserve the waiting row, and keep an in-progress resolution form usable when another check appears. Unlock sound from the Join gesture and test it on the actual phones, including a return visit after refresh. Disarm takeover actions while disconnected and restore them from current state.
- [ ] Prove the slice with a mock event and two separate browser profiles on the local stack. Verify both receive it, one wins, the other sees the winner, the owner still owns it after refresh and rename, other users cannot advance it, and resolution moves it into Closed with the chosen outcome. Verify hostile-looking display names render as text and the app works with touch and keyboard.

## 3. Finish presence, camera and vision

- [ ] **Task 7, presence and status.** Use a private Presence channel gated by joined membership. Track identity by user ID across tabs; render names from authoritative member records. Load the current feed row on initial sync and reconciliation as well as consuming Realtime updates. A timer makes the UI show **Camera offline** after 40 seconds even if no further messages arrive. Test both live FPS and labeled replay. Build **Bring someone in** from the canonical site URL and the joined-only invite RPC. Pin the Supabase and QR libraries and keep the shell readable while dependencies load. [Supabase Realtime authorization](https://supabase.com/docs/guides/realtime/authorization)
- [ ] **Task 8, publisher and mock.** Add `vision/publish.py` and `tools/mock_check.py`, then wire the opt-in publisher at the existing event emission boundary in `vision/run.py`. Keep network work off the frame loop with bounded delivery and request timeouts. Retry event delivery with the original ID, surface exhaustion, and send feed updates on transitions and at most once per 15 seconds for heartbeats. Cover startup failure, capture loss, replay completion and clean shutdown. A stopped publisher falls back to the heartbeat expiry. Preserve the current JSONL output and detector behavior without `--publish`. Agree on key type before wiring headers. Current Supabase secret keys use `apikey`; the source plan's bearer header refers to legacy JWT keys. [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys)
- [ ] Keep the two fallback paths from the original design. If capture fails before a check, start a fresh run on a labeled staged clip. If capture fails after a check, show the feed offline and leave the check actionable. Brief lost pose evidence and staff resolution leave detector rearm independent. Verify one check per staged episode, none for sitting, bending or crouching, and a second check after sustained upright rearm.
- [ ] Measure Realtime delivery from database creation to first display using a server-clock estimate. Record detection-to-display separately, including Mac clock offset and publisher time, so the one-second Realtime target and end-to-end timing remain distinguishable.

## 4. Deploy and rehearse

- [ ] Confirm the hosted Supabase project and owner, final place label, expected shared-IP join volume and CAPTCHA decision before configuring the hosted project. Use **Near the stage** as the local placeholder. Confirm whether rotating the QR should invalidate prior memberships. The proposed default is that established sessions keep their assignments and rotation affects new joins. A cleared browser session requires a fresh join and cannot silently reclaim an existing assignment. [Supabase anonymous sign-in and limits](https://supabase.com/docs/guides/auth/auth-anonymous)
- [ ] Apply the tested migrations, provision the private room code, confirm Realtime and publish the static frontend at its stable HTTPS address. Keep the publisher credential in the Mac's environment and check the built frontend and Git changes for secret material. Print the QR after validating the deployed address and current code. Document the launch, test, mock, rotation and fallback procedures without recording the code.
- [ ] Record actual results in the rehearsal log. On fresh phones test name entry in under 10 seconds on Wi-Fi and cellular, alert delivery around one second with sound, simultaneous claim, owner-only transitions and all three outcomes, owner refresh and rename, denied access without membership, and reconnection within five seconds. Send a mock event, then use consented staged footage and the real iPhone camera. Capture FPS and latency and rehearse the LAN fallback with two independent browser sessions.
- [ ] Before requesting review, run `supabase db reset`, `supabase test db`, the independent-session race harness, Node frontend tests, the main-based Python suite and `git diff --check`. Include a screenshot or short recording of the visible workflow and the rehearsal results with the implementation PR.

## Review before implementation

- [ ] A fresh install can be tested against local Supabase and the mock even while hosted decisions are pending.
- [ ] The implementation preserves the original demo's safety and workflow invariants: possible person-down wording, local footage, one check per episode, one owner, staff verification and independent detector rearm.
- [ ] Release evidence covers the points that local unit tests cannot establish: phone audio, cellular access, venue Wi-Fi join capacity, live camera behavior and actual latency.
