"""
Planeringen - egen middagsplanering (ersatter Mealies matplan).

En middag per dag. En post ar antingen en Mealie-recept (valjs genom sokning),
en egen ratt i fritext, eller bada - en post som borjat som fritext ("Nagon
pasta") kan senare kopplas till ett recept utan att planen behover goras om.

Samma SQLite-fil som Bjudningsloggen (egen tabell: plan_entries) - se
moduler/main.py, som ocksa anropar init_db() nedan EXPLICIT vid uppstart
(en monterad under-apps egen startup-handler kors inte automatiskt).

Mealie rors bara lasande (soka recept, hamta ingredienser till inkopslistan).
Mealies egen matplan lamnas ifred.
"""

import asyncio
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "/app/data/bjudningslogg.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

MEALIE_URL = os.environ.get("MEALIE_URL", "http://mealie:9000").rstrip("/")
MEALIE_PUBLIC_URL = os.environ.get("MEALIE_PUBLIC_URL", "http://localhost:9000").rstrip("/")
MEALIE_API_TOKEN = os.environ.get("MEALIE_API_TOKEN", "")
MEALIE_GROUP_SLUG = os.environ.get("MEALIE_GROUP_SLUG", "home")
MEALIE_HEADERS = {"Authorization": f"Bearer {MEALIE_API_TOKEN}"}

# ICA (valfritt). Uppgifterna kommer fran secrets.env via docker-compose.yml.
ICA_USER = os.environ.get("ICA_PERSONNUMMER", "").strip()
ICA_PASS = os.environ.get("ICA_PASSWORD", "")
# Inloggningssessionen sparas i datamappen sa vi inte loggar in pa nytt vid
# varje anrop (och inte efter varje omstart).
ICA_STATE_FILE = os.path.join(os.path.dirname(DB_PATH), "ica_state.json")
_ica_client = None
_ica_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    mealie_slug TEXT,
    mealie_name TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Egna varor man lagger till for hand i veckans inkopslista (utover
-- ingredienserna fran recepten). Hor till en vecka via veckans mandag.
CREATE TABLE IF NOT EXISTS plan_extras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)


app = FastAPI(title="Planering")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class ExtraIn(BaseModel):
    weekStart: str
    text: str


class ExtraUpdate(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None


class PlanIn(BaseModel):
    title: str
    mealieSlug: Optional[str] = None
    mealieName: Optional[str] = None
    notes: Optional[str] = None


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "Ogiltigt datum - anvand formatet AAAA-MM-DD")


def entry_dict(row) -> dict:
    return {
        "date": row["date"],
        "title": row["title"],
        "mealieSlug": row["mealie_slug"],
        "mealieName": row["mealie_name"],
        "notes": row["notes"],
        "mealieUrl": (
            f"{MEALIE_PUBLIC_URL}/g/{MEALIE_GROUP_SLUG}/r/{row['mealie_slug']}"
            if row["mealie_slug"] else None
        ),
    }


def entries_between(conn, start: date, end: date) -> list:
    return conn.execute(
        "SELECT * FROM plan_entries WHERE date >= ? AND date <= ? ORDER BY date",
        (start.isoformat(), end.isoformat()),
    ).fetchall()


@app.get("/api/config")
def config() -> dict:
    return {"mealiePublicUrl": MEALIE_PUBLIC_URL, "mealieGroupSlug": MEALIE_GROUP_SLUG}


@app.get("/api/plan")
def get_plan(start: str, days: int = 7) -> list[dict]:
    first = parse_date(start)
    days = max(1, min(days, 62))
    with db() as conn:
        return [entry_dict(r) for r in entries_between(conn, first, first + timedelta(days=days - 1))]


@app.put("/api/plan/{day}")
def put_plan(day: str, payload: PlanIn) -> dict:
    d = parse_date(day).isoformat()
    title = payload.title.strip()
    if not title:
        raise HTTPException(400, "Ange vad som ska ätas")
    slug = (payload.mealieSlug or "").strip() or None
    # Receptnamnet sparas bredvid sa vi kan visa "Recept: X" aven om titeln
    # ar nagot eget - och behalls inte om kopplingen tas bort.
    mealie_name = (payload.mealieName or "").strip() or None if slug else None
    notes = (payload.notes or "").strip() or None
    with db() as conn:
        conn.execute(
            "INSERT INTO plan_entries (date, title, mealie_slug, mealie_name, notes) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET title = excluded.title, "
            "mealie_slug = excluded.mealie_slug, mealie_name = excluded.mealie_name, "
            "notes = excluded.notes",
            (d, title, slug, mealie_name, notes),
        )
        row = conn.execute("SELECT * FROM plan_entries WHERE date = ?", (d,)).fetchone()
        return entry_dict(row)


@app.delete("/api/plan/{day}", status_code=204)
def delete_plan(day: str) -> Response:
    d = parse_date(day).isoformat()
    with db() as conn:
        conn.execute("DELETE FROM plan_entries WHERE date = ?", (d,))
    return Response(status_code=204)


@app.get("/api/mealie/search")
async def mealie_search(q: str = "") -> list[dict]:
    q = q.strip()
    if not q or not MEALIE_API_TOKEN:
        return []
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"{MEALIE_URL}/api/recipes",
                headers=MEALIE_HEADERS,
                params={"search": q, "perPage": 8},
            )
        except httpx.HTTPError:
            return []
    if resp.status_code != 200:
        return []
    data = resp.json()
    items = data.get("items", []) if isinstance(data, dict) else data
    return [
        {
            "name": r["name"],
            "slug": r["slug"],
            "image": f"{MEALIE_PUBLIC_URL}/api/media/recipes/{r['id']}/images/min-original.webp"
            if r.get("id") else None,
        }
        for r in items
    ]


async def mealie_ingredients(slug: str) -> list[str]:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(f"{MEALIE_URL}/api/recipes/{slug}", headers=MEALIE_HEADERS)
        except httpx.HTTPError:
            return []
    if resp.status_code != 200:
        return []
    out = []
    for ing in resp.json().get("recipeIngredient", []):
        text = ing.get("display") or ing.get("note") or ing.get("originalText") or ""
        if text:
            out.append(text)
    return out


@app.get("/api/shopping-list")
async def shopping_list(start: str, days: int = 7) -> dict:
    """Ingredienser fran alla Mealie-kopplade middagar under perioden, per dag,
    plus veckans egna (manuellt tillagda) varor."""
    first = parse_date(start)
    days = max(1, min(days, 62))
    with db() as conn:
        rows = entries_between(conn, first, first + timedelta(days=days - 1))

    async def build(r) -> dict:
        if not r["mealie_slug"]:
            return {"date": r["date"], "title": r["title"], "linked": False, "ingredients": []}
        return {
            "date": r["date"], "title": r["title"], "linked": True,
            "ingredients": await mealie_ingredients(r["mealie_slug"]),
        }

    groups = await asyncio.gather(*[build(r) for r in rows])
    with db() as conn:
        extras = conn.execute(
            "SELECT * FROM plan_extras WHERE week_start = ? ORDER BY id",
            (first.isoformat(),),
        ).fetchall()
    return {"groups": list(groups), "extras": [extra_dict(e) for e in extras]}


def extra_dict(row) -> dict:
    return {"id": row["id"], "text": row["text"], "done": bool(row["done"])}


@app.post("/api/extras", status_code=201)
def create_extra(payload: ExtraIn) -> dict:
    week = parse_date(payload.weekStart).isoformat()
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "Skriv vad som ska köpas")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO plan_extras (week_start, text) VALUES (?, ?)", (week, text)
        )
        row = conn.execute("SELECT * FROM plan_extras WHERE id = ?", (cur.lastrowid,)).fetchone()
        return extra_dict(row)


@app.put("/api/extras/{extra_id}")
def update_extra(extra_id: int, payload: ExtraUpdate) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM plan_extras WHERE id = ?", (extra_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Varan hittades inte")
        if payload.text is not None:
            text = payload.text.strip()
            if not text:
                raise HTTPException(400, "Skriv vad som ska köpas")
            conn.execute("UPDATE plan_extras SET text = ? WHERE id = ?", (text, extra_id))
        if payload.done is not None:
            conn.execute(
                "UPDATE plan_extras SET done = ? WHERE id = ?",
                (1 if payload.done else 0, extra_id),
            )
        row = conn.execute("SELECT * FROM plan_extras WHERE id = ?", (extra_id,)).fetchone()
        return extra_dict(row)


@app.delete("/api/extras/{extra_id}", status_code=204)
def delete_extra(extra_id: int) -> Response:
    with db() as conn:
        conn.execute("DELETE FROM plan_extras WHERE id = ?", (extra_id,))
    return Response(status_code=204)


# ---------- ICA ----------
# Inofficiell koppling (ica-mcp-paketets klient mot ICA-appens privata API).
# Kan sluta fungera om ICA andrar sitt API. Se secrets.env.

def get_ica():
    global _ica_client
    if not (ICA_USER and ICA_PASS):
        raise HTTPException(400, "ICA-uppgifter saknas - fyll i secrets.env och starta om containern")
    try:
        from ica_mcp.client import IcaClient
    except ImportError:
        raise HTTPException(500, "ICA-klienten (ica-mcp) ar inte installerad i containern")
    with _ica_lock:
        if _ica_client is None:
            _ica_client = IcaClient(
                username=ICA_USER, password=ICA_PASS, state_file=ICA_STATE_FILE
            )
        return _ica_client


def ica_errors(fn):
    """Kor fn() och oversatt ICA-/natverksfel till lasbara HTTP-fel."""
    import requests
    try:
        return fn()
    except HTTPException:
        raise
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        raise HTTPException(
            502,
            f"ICA svarade med fel ({code}). Kontrollera personnummer/losenord i "
            "secrets.env - eller sa har ICA andrat sitt API.",
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"Kunde inte na ICA: {type(e).__name__}")
    except Exception as e:  # IcaError m.fl. - bar redan ett lasbart meddelande
        raise HTTPException(502, f"ICA: {e}")


class IcaSendIn(BaseModel):
    items: list[str]
    listId: Optional[str] = None          # befintlig lista (offlineId) ...
    newListTitle: Optional[str] = None    # ... eller skapa en ny med detta namn


@app.get("/api/ica/status")
def ica_status() -> dict:
    return {"configured": bool(ICA_USER and ICA_PASS)}


@app.get("/api/ica/lists")
def ica_lists() -> list[dict]:
    def run():
        client = get_ica()
        return [
            {"id": L.get("offlineId"), "title": L.get("title") or "(utan namn)"}
            for L in client.get_lists()
            if L.get("offlineId")
        ]
    return ica_errors(run)


@app.post("/api/ica/send")
def ica_send(payload: IcaSendIn) -> dict:
    items = [i.strip() for i in payload.items if i and i.strip()]
    if not items:
        raise HTTPException(400, "Inga varor att skicka")

    def run():
        client = get_ica()
        if payload.listId:
            lst = client.get_list_raw(payload.listId)
            offline_id = payload.listId
        else:
            title = (payload.newListTitle or "").strip() or "Veckans middagar"
            lst = client.create_list(title)
            offline_id = lst.get("offlineId")
        # Hoppa over varor som redan ligger (och inte ar overstrukna) i
        # listan - sa att man kan skicka igen utan att fa dubbletter.
        existing = {
            (r.get("productName") or "").strip().lower()
            for r in (lst.get("rows") or [])
            if not r.get("isStrikedOver")
        }
        new = [i for i in items if i.lower() not in existing]
        if new:
            client.add_rows(offline_id, new)
        return {
            "added": len(new),
            "skipped": len(items) - len(new),
            "listTitle": lst.get("title"),
        }
    return ica_errors(run)


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
