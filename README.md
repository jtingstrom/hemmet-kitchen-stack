# Hemmet – a self-hosted home kitchen stack

Recipes, dinner planning and dinner-party logging for a household, running in
Docker on your own computer. It is **one official [Mealie](https://mealie.io)
container** (the recipe database) plus **one small custom Python container**
that hosts several home-made web apps ("modules") on a single address.

> The UI is in **Swedish** (and code comments are Swedish, written without
> åäö). The architecture is language-neutral, so it should be easy to
> translate or to reuse as a pattern. A Swedish summary is at the bottom.

## What's in it

| Path | Module | What it does |
|---|---|---|
| `/` | Start page "Köket" | Cards linking to the modules |
| `/recept` | **Receptvy** | A fast, searchable, tablet-friendly view of your Mealie recipes, with a "kitchen mode" (large text, one step at a time) and shortcuts to Mealie |
| `/bjudning` | **Bjudningsloggen** | Log dinners you host: who came, what you served and drank, guests and families, overnight stays; past dinners move to history automatically |
| `/plan` | **Planering** | Plan the week's dinners (recipes from Mealie or your own dishes), build a shopping list, add extra items by hand, optionally push it to an ICA shopping list |

Mealie stays the source of truth for recipes and is never modified. The custom
modules only *read* from it through its API.

## Architecture in one minute

```
 browser ──► moduler (FastAPI, :8080)
               ├─ /          static start page
               ├─ /recept    sub-app  ─┐
               ├─ /bjudning  sub-app  ─┼─► one shared SQLite file (./data/bjudningslogg/)
               └─ /plan      sub-app  ─┘
               └──────────► Mealie API (http://mealie:9000, token stays server-side)
 browser ──► mealie (official image, :9000)
```

- Each module is a folder with a `main.py` (small FastAPI API) and a
  `static/index.html` (single file, vanilla JS, no build step).
- The root app mounts each module with `app.mount()`; adding a module is a
  new folder plus a few lines. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

Requirements: Docker (Docker Desktop on Windows/macOS, or Docker Engine +
Compose on Linux).

1. Copy the example config:

   ```
   cp .env.example .env
   ```

2. Start Mealie first:

   ```
   docker compose up -d mealie
   ```

3. Open http://localhost:9000, sign in with Mealie's default account
   (`changeme@example.com` / `MyPassword`) and **change the password
   immediately**.
4. In Mealie: profile picture → *Profile* → *API tokens* → create a token and
   paste it as `MEALIE_API_TOKEN` in `.env`.
5. Start everything:

   ```
   docker compose up -d --build
   ```

6. Open http://localhost:8080 .

To reach the apps from a phone or tablet, set `MEALIE_BIND` / `MODULER_BIND`
to the computer's LAN IP in `.env`, and set `BASE_URL` / `MEALIE_PUBLIC_URL`
to the matching Mealie address (see comments in `.env.example`).

### Updating after changing code

```
docker compose build moduler
docker compose up -d moduler
```

Data lives in `./data/` (bind mount) and survives rebuilds. Back that folder
up; it is not part of git.

## Security notes

- **There is no authentication** in the custom modules. Run them on a trusted
  home network only; do not expose the ports to the internet.
- The Mealie API token stays on the server and is never sent to the browser.
- `.env` and `secrets.env` are git-ignored. Never commit them.

## ICA integration (optional, experimental)

Planering can push the shopping list to an ICA list, so it shows up in ICA's
handheld scanners in store. This uses the **unofficial, community-made**
[`ica-mcp`](https://github.com/kanylbullen/ica-mcp) client (pinned to
`0.5.0`), which talks to ICA's private API. Be aware that:

- it is not affiliated with or endorsed by ICA and may break at any time;
- using it may be against ICA's terms – **your responsibility**, use at your own risk;
- it needs a Swedish IP, a 12-digit personnummer and a password account
  (BankID-only accounts do not work).

To enable it, copy `secrets.env.example` to `secrets.env`, fill it in, and run
`docker compose up -d moduler`. A read-only login test is in
[`tools/ica_test.py`](tools/ica_test.py). If you don't want this, simply don't
create `secrets.env` – everything else works without it.

## Contributing / reuse

The code is MIT-licensed, so you are free to use, modify and share it. Issues
and pull requests are welcome – see [CONTRIBUTING.md](CONTRIBUTING.md) first. Not affiliated with Mealie, ICA or Coop.

---

## Sammanfattning på svenska

**Hemmet** är ett självhostat köksystem för hemmabruk: den officiella
**Mealie**-containern för recept plus **en egen Python-container** (`moduler`)
som samlar flera egna webbappar på samma adress: **Receptvy** (snygg receptvy
med kökläge), **Bjudningsloggen** (logg över middagar, gäster, mat och dryck),
**Planering** (veckans middagar, inköpslista, valfri koppling till ICA) och en
startsida.

Tekniken i korthet: FastAPI-rotapp som monterar varje modul med `app.mount()`,
en delad SQLite-fil i en bind-mount, och ett gränssnitt per modul i en enda
HTML-fil med vanilla JavaScript (inget byggsteg). Mealie lämnas orörd och
läses bara via sitt API.

Kom igång: kopiera `.env.example` till `.env`, kör `docker compose up -d mealie`,
skapa en API-token i Mealie och lägg in den i `.env`, kör sedan
`docker compose up -d --build` och öppna http://localhost:8080 .
Mer detaljer: [docs/ARKITEKTUR.md](docs/ARKITEKTUR.md).

**Viktigt:** modulerna saknar inloggning, så kör dem bara i ett betrott
hemmanätverk. ICA-kopplingen är inofficiell, kan sluta fungera och används på
eget ansvar.
