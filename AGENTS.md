# Repository Guidelines

## Project Structure

CodeJam16 is a venue welfare demo. [README.md](README.md) is the project entry point. The [engineering plan](docs/designs/live-venue-welfare-checks.md) records the architecture, build order, and rehearsal criteria. Vision code (capture + detector) lives in `vision/` with its tests in `tests/vision/`. The staff web app lives in `backend/` (Flask + SQLite) and `frontend/` (staff screens).

Start with the local Python detector. Use OpenCV to read recorded or live video and pretrained YOLO26n-pose to estimate body points. Keep frame capture, episode detection, and incident storage distinct. The planned Flask app will use SQLite and serve the responder queue and supervisor map.

## Development Commands

- `git status --short` shows your working changes. `git diff --check` checks tracked edits for whitespace errors.
- `python3 -m venv .venv` creates a local environment once a compatible Python version is selected. Activate it with `source .venv/bin/activate` on macOS.

The repository has no application launch command, dependency manifest, or test runner yet. When adding one, document the exact setup, launch, and test commands in the README.

## Coding and Testing

Use four spaces for Python indentation, `snake_case` for modules and functions, and `PascalCase` for classes. Name timing values with their units, such as `confirmation_seconds`. Keep timestamps consistent across live frames and replay. Introduce formatting or linting tools alongside their configuration and usage instructions.

When adding Python tests, use names such as `tests/test_detector.py`. Cover sitting and bending, one alert per down episode, and sustained upright posture before another alert can fire. Rehearse on fresh staged footage and the live iPhone feed. For the staff workflow, verify that two simultaneous claims produce one winner and that refresh preserves the assigned responder.

## Commits and Pull Requests

Git history currently contains only `Initial commit`. Use short imperative subjects such as `Add person-down replay detector`. In pull requests, describe the change, link the relevant issue if one exists, and report what you verified. Include a screenshot or short recording for visible changes.

Use staged footage with consenting teammates. Keep local databases, credentials, and raw camera recordings out of Git.
