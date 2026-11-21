# Design: Walk-up join, everyone sees the map

Written 21 Nov 2026. This replaces the invite-link join and the Supervisor role. It builds on the [office-hours walk-up join draft](#source) and the [finish-and-demo spec](2026-11-21-codejam16-finish-and-demo-design.md). The detector, the incident store's one-winner claim, and the locked light UI are unchanged.

## Intent

Joining is the part of the demo that fails in a room. Today each person needs a private invite link. A used link, a link preview, or a second tab blocks the next person. The goal is for a stranger to scan one QR, type a name, and be in the Accept race within 10 seconds. Everyone then sees the same check list and the same venue map.

**The whoa is the race:** two phones tap Accept on one East bar check, one name wins, and every screen shows that name and a teal pin.

### Goals

- One page (`/join`): a name and a Join button. Nothing else to choose.
- Every joined person can see the check list and the map, can Accept, and can finish only what they own.
- A phone stays one person across refresh and re-join.

### Non-goals

Supervisor or admin roles, a live roster, duplicate-name suffixes, QR generation in the app, a timeline with seconds, public hosting.

### Anti-goals

- A check that nobody can finish.
- A join that needs anything except a name.
- A phone layout where the map squeezes the Accept button off screen.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | **No roles.** Everyone who joins is a responder. The Supervisor role and the role picker are removed. | Both roles could Accept, so the role only chose the landing page. Fewer choices at the door. The projector laptop joins with a name such as "Floor desk" and keeps the Map tab open. |
| D2 | **A phone keeps its person.** Joining again in a browser that already has a valid session keeps the same person id and session cookie, and only updates the display name. | If re-join created a new person, a check owned by the old one could never be Arrived or Resolved. Checks already accepted keep the name they were accepted under. |
| D3 | **Names:** trimmed, cut at 40 characters. A blank name re-shows the form with an error and creates nobody. Duplicate names are allowed as-is. | Fits the phone header, and there's no roster to clash with. |
| D4 | **Old links:** `GET /join` and `GET /join/<anything>` both show the form. Invite tokens are no longer read. | A used or previewed link can't block anyone. |
| D5 | **Origin check:** `POST /join` is rejected with 403 when the `Origin` host doesn't match the app. | Keeps the existing cross-site protection on the new form. |
| D6 | **Screens:** after Join, everyone lands on **Checks**. A laptop (≥ 768 px) shows the existing sidebar with Checks and Map. A phone shows a two-tab bar, **Checks · Map**, pinned at the top under the header. Tapping a pin or map row opens that check's detail with its Accept, Arrived or Resolve button. | One shared picture for everyone. A phone can't fit the map beside the list. |
| D7 | **Port 8016 is the default**, matching the QR address `http://<mac-lan-ip>:8016/join`. | One less flag on demo day. |

Unchanged: the one-winner claim, owner-only Arrived and Resolve, the three outcomes, the overdue flag, the cookie (HttpOnly, SameSite=Lax), CSRF on actions, the detector and the feed.

## Approaches considered

- **A. Name-only form with no roles, and everyone sees the map (chosen).** It removes the role code paths instead of adding a picker.
- B. Name plus a Responder/Supervisor picker (the office-hours draft). Rejected: under D1 the role does nothing but choose a landing page.
- C. Two QR codes, one per role. Rejected in the draft, and moot without roles.

## Review decisions (added after the usability pass)

| # | Decision |
| --- | --- |
| D8 | **No sample check in the live demo.** Only camera checks exist, so strangers race on the real one. A `--with-sample` launch flag brings the overdue Main floor sample back for rehearsals. |
| D9 | **"Change name"** replaces logout. It opens `/join`, which renames the same person (D2). There is no logout. |
| D10 | **Renaming also updates the name on that person's open checks.** Closed checks keep the name they were closed under. |
| D11 | **Your own phone says "You".** An owned check reads "You are on it" for the owner and "<Name> is on it" for everyone else. |
| D12 | **The loser's message names the winner:** "<Name> accepted this first." |
| D13 | **Empty state:** "Waiting for the camera. A check appears here when someone goes down at the East bar." |
| D14 | **The app prints its join address on startup:** `Join at http://<lan-ip>:8016/join`. |
| D15 | **Minimal UI**, per the approved mockup (https://claude.ai/artifact/AwNzxTVD11RD8i6zdkdtb6): one accent, text tabs, one big place name per screen. Phones show Checks · Map tabs. From 900 px wide, Checks and Map sit side by side with no tabs (projector laptop). Tapping a pin opens that check on the Checks tab. The old sidebar, stats, filters and history timeline are removed. |

## Success criteria

1. `/join` and `/join/old-token` both show the form, and neither shows an error.
2. A blank or whitespace-only name creates nobody.
3. Two phones join with two names, both land on Checks, and both are still those people after refresh.
4. Both see the camera-created East bar check, on Checks and on Map. Two Accepts give one owner, and the other phone shows the winner's name.
5. Re-joining as a new name on the owner's phone still lets that phone tap Arrived and Resolve.
6. A `POST /join` from another origin returns 403.

## Left to the builder

Exact copy on the join page (the sketch gives the content: "Join the welfare check", "Your name", "This phone stays {name}. Another person needs their own phone."), tab styling within the locked light UI, and how the role column is retired in SQLite (keep it and set it to `responder`).

## Source

Office-hours draft "Walk-up join for the welfare demo", 21 Nov 2026, plus the sketch at `~/.gstack/projects/CHUNKYBOI666-CodeJam16/designs/walk-up-join-sketch.png`. The user decided D1 and D2 in conversation.
