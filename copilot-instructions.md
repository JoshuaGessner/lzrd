# Copilot Instructions for LZRD

## Project Scope
LZRD is a local-first PC security tripwire and remote-control app.

Core behavior:
- Runs as a desktop process with a system tray icon.
- Serves a mobile-first PWA over Flask.
- Watches for mouse movement when armed.
- Broadcasts real-time state/alerts to web clients via Server-Sent Events (SSE).
- Supports remote actions: arm/disarm, lock screen, lock/unlock mouse, shutdown, restart, display message, launch app.
- Targets Windows 10/11 fully, with Linux support paths.

Primary files:
- `lzrd.py`: backend, OS integration, SSE, Flask routes, tray behavior.
- `web/index.html`, `web/app.js`, `web/style.css`: PWA UI and client logic.
- `web/sw.js`, `web/manifest.json`: offline shell + installable app metadata.
- `tests.py`: unit tests for core behavior and API/security contracts.
- `config.ini`: runtime configuration.
- `scripts/generate_icons.py`: icon generation.

## High-Level Architecture
- Single-process Python app.
- Flask app serves static web assets and `/api/*` endpoints.
- SSE endpoint (`/api/events`) pushes state/alert events.
- Internal event fan-out uses per-client queues.
- Tray menu and web UI both control shared `LZRD` state.
- Optional reverse-proxy mode (Caddy) via `behind_proxy` + `public_url`.

## Non-Negotiable Security Rules
When changing backend/API behavior, preserve these guarantees:
- Token auth on all `/api/*` endpoints, including SSE.
- Constant-time token comparison (`hmac.compare_digest`).
- Failed-auth tracking per source IP and rate limiting (`429`) after threshold.
- Defensive headers on responses:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Content-Security-Policy: default-src 'self'`
  - `Referrer-Policy: no-referrer`
- `Cache-Control: no-store` on API JSON responses (excluding SSE behavior rules).
- Keep request body and input-size limits in place.
- Never use `shell=True` for app launch or system command execution.
- Keep insecure default token (`changeme`) blocked at startup.

## Cross-Platform Behavior Rules
- Preserve Windows and Linux code paths.
- Keep Linux fallbacks for workstation locking/notifications where applicable.
- Do not introduce Windows-only assumptions into shared logic.
- Maintain platform information in status/event payloads for UI hints.

## API and Real-Time Contract
Treat current client/server payload shape as stable unless intentionally versioned.

Expected state/event fields include:
- `type`
- `armed`
- `alert`
- `mouse_locked`
- `platform`

If you add fields:
- Add them in a backward-compatible way.
- Ensure client defaults remain safe when fields are missing.

## Frontend Guidelines
- Keep mobile-first interaction quality high (touch targets, readable labels, simple flows).
- Preserve offline/app-shell behavior in service worker unless intentionally changing cache strategy.
- Keep token handling UX straightforward (stored locally, reconnect flow intact).
- Avoid heavy dependencies for small UI tweaks.

## Coding Etiquette for This Repo
- Prefer small, focused changes over broad rewrites.
- Match existing style and naming conventions in touched files.
- Keep comments concise and useful (avoid obvious comments).
- Do not reformat unrelated sections.
- Avoid new dependencies unless there is clear value.
- Preserve public behavior unless change request explicitly asks for behavior change.

## Testing Expectations
After backend or API changes, run:
- `python -m unittest tests.py`

After UI/client contract changes:
- Validate manual flow: connect token, arm/disarm, alert banner, lock mouse toggle, dialogs, and reconnect behavior.

When modifying security/auth/rate-limit logic:
- Ensure related tests in `tests.py` still pass or update tests with explicit rationale.

## Config and Ops Notes
- Keep `config.ini` documentation aligned with supported config keys.
- Keep README behavior/setup text aligned with implemented behavior.
- If changing icon/theme assets, regenerate icons via `scripts/generate_icons.py` as needed.
- If changing proxy/SSE behavior, verify Caddy guidance still matches expected behavior (especially SSE flushing).

## Change Checklist (Use Before Finalizing)
- Scope: Does this change match LZRD's local-first remote-control/tripwire purpose?
- Security: Are auth, headers, limits, and non-shell execution still enforced?
- Compatibility: Are Windows/Linux paths still safe?
- API Contract: Does frontend still work with backend payloads?
- Tests: Did relevant tests run and pass (or were updates justified)?
- Docs/Config: Are README/config updates needed?
