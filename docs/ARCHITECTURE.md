# Architecture and design principles

## Overview

Two containers, one data folder:

| Container | Image | Role |
|---|---|---|
| `mealie` | official `ghcr.io/mealie-recipes/mealie` (pinned) | Recipe database and editor. Has its own database. Never modified. |
| `moduler` | built from `./moduler` | One Python (FastAPI) process hosting every custom web app. |

`moduler` reaches Mealie over the internal Docker network
(`http://mealie:9000`) using an API token. The browser never sees the token;
it only gets Mealie's *public* URL (`MEALIE_PUBLIC_URL`) for images and links.

## Core idea: a module is a folder

```
moduler/
├── main.py            root app: mounts every module
├── Dockerfile
├── requirements.txt
├── receptvy/          ┐
│   ├── __init__.py    │ one module =
│   ├── main.py        │   - a FastAPI app (`app`) with its API
│   └── static/        │   - optional `init_db()`
│       └── index.html ┘   - a single-file UI (HTML + CSS + vanilla JS)
├── bjudningslogg/
├── planering/
└── startsida/static/  just static files, no API
```

The root app mounts each module under a prefix:

```python
app.mount("/recept", receptvy_app)
app.mount("/bjudning", bjudningslogg_app)
app.mount("/plan", planering_app)
app.mount("/", StaticFiles(directory=STARTSIDA_DIR, html=True))  # must be LAST
```

### Adding a module (checklist)

1. Create `moduler/mymodule/` with an empty `__init__.py`, a `main.py` and
   `static/index.html`. Minimal `main.py`:

   ```python
   import os, sqlite3
   from contextlib import contextmanager
   from fastapi import FastAPI
   from fastapi.staticfiles import StaticFiles

   DB_PATH = os.environ.get("DB_PATH", "/app/data/bjudningslogg.db")
   app = FastAPI(title="My module")

   @contextmanager
   def db():
       conn = sqlite3.connect(DB_PATH)
       conn.row_factory = sqlite3.Row
       conn.execute("PRAGMA foreign_keys = ON")
       try:
           yield conn
           conn.commit()
       finally:
           conn.close()

   def init_db() -> None:          # idempotent: safe to run on every start
       with db() as conn:
           conn.execute("CREATE TABLE IF NOT EXISTS my_items "
                        "(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")

   @app.get("/api/items")
   def items():
       with db() as conn:
           return [dict(r) for r in conn.execute("SELECT * FROM my_items")]

   STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
   app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
   ```

2. In `moduler/main.py`: import `app` and `init_db`, call `init_db()` in the
   startup handler, add a redirect route for the bare prefix, and mount it
   **before** the `/` mount.
3. In `moduler/Dockerfile`: `COPY mymodule/ ./mymodule/`.
4. Add a card to `moduler/startsida/static/index.html`.
5. `docker compose build moduler && docker compose up -d moduler`.

### Gotchas we hit (and the fixes)

- **Sub-app startup events do not fire** when an app is mounted. The root app
  therefore calls each module's `init_db()` explicitly in its own startup
  handler.
- **Bare prefix 404.** The catch-all `/` static mount swallows `/recept`
  (no trailing slash) before Starlette's slash-redirect can run. Each prefix
  gets an explicit redirect route (`/recept` → `/recept/`).
- **Mount order.** Specific prefixes first, `/` last.
- **Relative URLs in the frontend.** `fetch("api/...")` (no leading slash) so
  the module works under any prefix.
- **Time zones.** Containers run on UTC. Set `TZ`, install `tzdata`, use
  SQLite's `date('now','localtime')`, and in JavaScript build dates from local
  components instead of `toISOString()` (which converts to UTC and can shift a
  date by a day near midnight).

## Data

- One shared SQLite file (`./data/bjudningslogg/bjudningslogg.db`, bind-mounted
  to `/app/data`). Modules own separate tables, so they can later read each
  other's data.
- Schemas are created with `CREATE TABLE IF NOT EXISTS` at startup – no
  migration tool. Later schema additions use additive, idempotent steps.
- `PRAGMA foreign_keys = ON`, `ON DELETE CASCADE`, `COLLATE NOCASE` on names.
- Backups: copy the `data/` folder.

## Frontend principles

- One HTML file per module: inline CSS and JavaScript, no framework, no build,
  no external requests (works offline on the LAN).
- Mobile/tablet first (used in the kitchen); large touch targets.
- CSS custom properties with a light and a dark theme
  (`prefers-color-scheme`).
- Information is never colour-only (labels/numbers accompany colours).
- Every module links back to the start page.

## Mealie integration

- Recipes are read through Mealie's REST API with a bearer token that lives in
  an environment variable on the server.
- Modules store only a recipe reference (id/slug/title), never copies of
  recipes, so Mealie remains the single source of truth.
- Buttons deep-link into Mealie (open / create recipe) using
  `MEALIE_PUBLIC_URL` and `MEALIE_GROUP_SLUG`.

## ICA shopping-list push (optional)

- Uses the unofficial `ica-mcp` client, installed with `--no-deps` because only
  the HTTP client is needed, not the MCP server around it. Version pinned.
- Auth: OAuth2/OIDC against ICA's identity service; the session is cached in
  `ica_state.json` next to the database.
- Credentials come from `secrets.env` (loaded through `env_file` with
  `required: false`, so the stack starts without it).
- When sending, items already on the ICA list are de-duplicated.
- Isolated in one module and optional by design, since the API is private and
  can change or disappear.

## Operations and testing

- Deploy = replace files, `docker compose build moduler`, `docker compose up -d moduler`.
- Mealie is upgraded by changing the pinned image tag.
- Testing approach used during development: `py_compile` for syntax, a
  throw-away `uvicorn` plus `curl` for the APIs, and Playwright for the UI
  (light and dark), with fake Mealie/ICA servers where the real ones are not
  reachable.

## Design principles

1. One layer per responsibility: Mealie owns recipes, `moduler` owns the rest.
2. Simplicity over frameworks: no build step, one API file and one HTML file
   per module.
3. Shared database, separate tables.
4. Local first: no cloud services, LAN only.
5. Deliberate dependencies: pinned versions (Mealie, ICA client).
6. Consistent look and navigation across modules.

## Open questions / ideas

- Authentication or VPN for access from outside the home network.
- Automated backups of `data/`.
- Tighter links between Planering and Bjudningsloggen.
- Replacing the unofficial ICA client if a public API appears.
- Whether SQLite remains enough with more modules or users.
