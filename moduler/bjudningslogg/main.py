"""
Bjudningsloggen - en egen liten app for att halla koll pa middagar:
vilka gaster som varit hos er, vem i familjen som var vard, vilka ratter
som serverades (garna lankat till era Mealie-recept) och vad ni drack.

Egen SQLite-databas, skild fran Mealies egen. Databasen delas numera med
ovriga egna moduler (samma fil, samma container) - se moduler/main.py.

Datamodellen ar medvetet uppdelad i egna tabeller (middagar, gaster,
ratter, drycker) sa att det gar att bygga pa senare - t.ex. allergier per
gast eller ett vinlager - utan att rita om nagot som redan finns.

Monteras som en under-app pa /bjudning av moduler/main.py (se den filen).
Observera: den filens startup-logik anropar init_db() nedan EXPLICIT -
denna moduls egen @app.on_event("startup") nedan triggas INTE automatiskt
nar appen monteras in i en storre huvudapp (testat separat), sa den
behover finnas kvar har bara for att fungera om modulen nagon gang kors
helt fristaende igen.
"""

import asyncio
import os
import sqlite3
from contextlib import contextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DB_PATH = os.environ.get("DB_PATH", "/app/data/bjudningslogg.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Samma Mealie-koppling som Receptvy anvander, atervandbar har for att
# kunna soka fram befintliga recept nar man lagger till en ratt.
MEALIE_URL = os.environ.get("MEALIE_URL", "http://mealie:9000").rstrip("/")
MEALIE_PUBLIC_URL = os.environ.get("MEALIE_PUBLIC_URL", "http://localhost:9000").rstrip("/")
MEALIE_API_TOKEN = os.environ.get("MEALIE_API_TOKEN", "")
MEALIE_GROUP_SLUG = os.environ.get("MEALIE_GROUP_SLUG", "home")
MEALIE_HEADERS = {"Authorization": f"Bearer {MEALIE_API_TOKEN}"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS dinners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    title TEXT,
    host TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'planerad',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS guests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    is_child INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dinner_guests (
    dinner_id INTEGER NOT NULL REFERENCES dinners(id) ON DELETE CASCADE,
    guest_id INTEGER NOT NULL REFERENCES guests(id) ON DELETE CASCADE,
    stayed_over INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (dinner_id, guest_id)
);

CREATE TABLE IF NOT EXISTS dishes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dinner_id INTEGER NOT NULL REFERENCES dinners(id) ON DELETE CASCADE,
    course TEXT,
    name TEXT NOT NULL,
    mealie_slug TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS drinks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dinner_id INTEGER NOT NULL REFERENCES dinners(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    producer TEXT,
    vintage TEXT,
    price TEXT,
    note TEXT,
    link TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS toppings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    category TEXT
);

CREATE TABLE IF NOT EXISTS dish_toppings (
    dish_id INTEGER NOT NULL REFERENCES dishes(id) ON DELETE CASCADE,
    topping_id INTEGER NOT NULL REFERENCES toppings(id) ON DELETE CASCADE,
    PRIMARY KEY (dish_id, topping_id)
);
"""


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # Infor varje anrop: flytta automatiskt over middagar vars datum har
        # passerat fran "planerad" till "genomford" - sa man aldrig behover
        # andra status for hand nar en middag har varit. Paverkar bara
        # middagar som fortfarande star som "planerad"; en middag man
        # medvetet satt till "genomford" i fortid (eller "planerad" for en
        # framtida middag) rors aldrig. "localtime" sa grannsen racknas i
        # svensk tid (se TZ i docker-compose.yml), inte UTC.
        # try/except: tabellen finns annu inte forsta gangen nagonsin denna
        # funktion anropas (inifran init_db(), innan SCHEMA korts).
        try:
            conn.execute(
                "UPDATE dinners SET status = 'genomförd' "
                "WHERE status = 'planerad' AND date <= date('now', 'localtime')"
            )
        except sqlite3.OperationalError:
            pass
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_column(conn, table: str, column: str, ddl_type: str, default_expr: str) -> None:
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type} DEFAULT {default_expr}")


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        # Migrering for databaser som skapades innan statusfaltet fanns.
        # NULL (inte 'planerad') som standard har - annars far alla
        # befintliga rader det vardet direkt av ALTER TABLE, och backfillen
        # nedan (som bara ror rader utan status) skulle aldrig traffa nagot.
        ensure_column(conn, "dinners", "status", "TEXT", "NULL")
        # Rimlig backfill: middagar med dagens datum eller tidigare rakas
        # som redan genomforda, allt annat som planerat.
        conn.execute(
            "UPDATE dinners SET status = 'genomförd' "
            "WHERE (status IS NULL OR status = '') AND date <= date('now', 'localtime')"
        )
        conn.execute(
            "UPDATE dinners SET status = 'planerad' WHERE status IS NULL OR status = ''"
        )
        # Migrering for overnattningsfunktionen. 0/NULL ar korrekta
        # standardvarden aven for befintliga rader (ingen backfill behovs,
        # till skillnad fran status ovan) - ingen har markerats som barn
        # eller loggats som overnattningsgast innan faltet fanns.
        ensure_column(conn, "guests", "is_child", "INTEGER", "0")
        ensure_column(conn, "dinner_guests", "stayed_over", "INTEGER", "0")
        ensure_column(conn, "dinners", "overnight_food", "TEXT", "NULL")
        # Migrering for familjefunktionen. NULL (ingen familj) ar korrekt
        # standard for alla befintliga gaster - ingen backfill behovs.
        ensure_column(
            conn, "guests", "family_id",
            "INTEGER REFERENCES families(id) ON DELETE SET NULL", "NULL",
        )


app = FastAPI(title="Bjudningsloggen")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------- Indata-modeller ----------

class DishIn(BaseModel):
    course: Optional[str] = None
    name: str
    mealieSlug: Optional[str] = None
    toppings: list[str] = Field(default_factory=list)


class ToppingIn(BaseModel):
    name: str
    category: Optional[str] = None


class DrinkIn(BaseModel):
    name: str
    producer: Optional[str] = None
    vintage: Optional[str] = None
    price: Optional[str] = None
    note: Optional[str] = None
    link: Optional[str] = None


class FamilyIn(BaseModel):
    name: str


class GuestIn(BaseModel):
    name: str
    familyId: Optional[int] = None
    isChild: Optional[bool] = False


class DinnerIn(BaseModel):
    date: str
    title: Optional[str] = None
    host: Optional[str] = None
    notes: Optional[str] = None
    status: str = "planerad"
    guests: list[str] = Field(default_factory=list)
    overnightGuests: list[str] = Field(default_factory=list)
    overnightFood: Optional[str] = None
    dishes: list[DishIn] = Field(default_factory=list)
    drinks: list[DrinkIn] = Field(default_factory=list)


def normalize_status(status: Optional[str]) -> str:
    return status if status in ("planerad", "genomförd") else "planerad"


# ---------- Hjalpfunktioner ----------

def get_or_create_guest(conn, name: str) -> int:
    name = name.strip()
    row = conn.execute(
        "SELECT id FROM guests WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO guests (name) VALUES (?)", (name,))
    return cur.lastrowid


def get_or_create_topping(conn, name: str) -> int:
    name = name.strip()
    row = conn.execute(
        "SELECT id FROM toppings WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO toppings (name, category) VALUES (?, NULL)", (name,))
    return cur.lastrowid


def save_dinner_relations(conn, dinner_id: int, payload: DinnerIn) -> None:
    conn.execute("DELETE FROM dinner_guests WHERE dinner_id = ?", (dinner_id,))
    # dish_toppings stads bort automatiskt via ON DELETE CASCADE nar
    # dishes-raderna nedan tas bort.
    conn.execute("DELETE FROM dishes WHERE dinner_id = ?", (dinner_id,))
    conn.execute("DELETE FROM drinks WHERE dinner_id = ?", (dinner_id,))

    # Overnattningsgaster maste vara en delmangd av middagens ovriga gaster -
    # det racker darfor att jamfora namn har (case-insensitive); ett namn i
    # overnightGuests som inte finns i guests blir aldrig infogat nagonstans.
    overnight_names = {n.strip().lower() for n in payload.overnightGuests if n.strip()}

    seen_guests = set()
    for name in payload.guests:
        clean = name.strip()
        if not clean or clean.lower() in seen_guests:
            continue
        seen_guests.add(clean.lower())
        guest_id = get_or_create_guest(conn, clean)
        stayed_over = 1 if clean.lower() in overnight_names else 0
        conn.execute(
            "INSERT OR IGNORE INTO dinner_guests (dinner_id, guest_id, stayed_over) VALUES (?, ?, ?)",
            (dinner_id, guest_id, stayed_over),
        )

    for i, dish in enumerate(payload.dishes):
        if not dish.name.strip():
            continue
        cur = conn.execute(
            "INSERT INTO dishes (dinner_id, course, name, mealie_slug, sort_order) "
            "VALUES (?, ?, ?, ?, ?)",
            (dinner_id, dish.course, dish.name.strip(), dish.mealieSlug, i),
        )
        dish_id = cur.lastrowid
        seen_toppings = set()
        for tname in dish.toppings:
            clean_topping = tname.strip()
            if not clean_topping or clean_topping.lower() in seen_toppings:
                continue
            seen_toppings.add(clean_topping.lower())
            topping_id = get_or_create_topping(conn, clean_topping)
            conn.execute(
                "INSERT OR IGNORE INTO dish_toppings (dish_id, topping_id) VALUES (?, ?)",
                (dish_id, topping_id),
            )

    for i, drink in enumerate(payload.drinks):
        if not drink.name.strip():
            continue
        conn.execute(
            "INSERT INTO drinks (dinner_id, name, producer, vintage, price, note, link, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dinner_id,
                drink.name.strip(),
                drink.producer,
                drink.vintage,
                drink.price,
                drink.note,
                drink.link,
                i,
            ),
        )


def dinner_summary_row(conn, row) -> dict:
    guests = [
        r["name"]
        for r in conn.execute(
            "SELECT g.name FROM guests g "
            "JOIN dinner_guests dg ON dg.guest_id = g.id "
            "WHERE dg.dinner_id = ? ORDER BY g.name COLLATE NOCASE",
            (row["id"],),
        )
    ]
    dish_names = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM dishes WHERE dinner_id = ? ORDER BY sort_order",
            (row["id"],),
        )
    ]
    drink_count = conn.execute(
        "SELECT COUNT(*) c FROM drinks WHERE dinner_id = ?", (row["id"],)
    ).fetchone()["c"]
    overnight_count = conn.execute(
        "SELECT COUNT(*) c FROM dinner_guests WHERE dinner_id = ? AND stayed_over = 1",
        (row["id"],),
    ).fetchone()["c"]
    return {
        "id": row["id"],
        "date": row["date"],
        "title": row["title"],
        "host": row["host"],
        "status": row["status"],
        "guests": guests,
        "dishNames": dish_names,
        "drinkCount": drink_count,
        "overnightCount": overnight_count,
    }


def dinner_detail(conn, dinner_id: int) -> dict:
    row = conn.execute("SELECT * FROM dinners WHERE id = ?", (dinner_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Middagen hittades inte")
    summary = dinner_summary_row(conn, row)
    dishes = []
    for r in conn.execute(
        "SELECT * FROM dishes WHERE dinner_id = ? ORDER BY sort_order", (dinner_id,)
    ):
        toppings = [
            tr["name"]
            for tr in conn.execute(
                "SELECT t.name FROM toppings t "
                "JOIN dish_toppings dt ON dt.topping_id = t.id "
                "WHERE dt.dish_id = ? ORDER BY t.name COLLATE NOCASE",
                (r["id"],),
            )
        ]
        dishes.append(
            {
                "id": r["id"],
                "course": r["course"],
                "name": r["name"],
                "mealieSlug": r["mealie_slug"],
                "toppings": toppings,
            }
        )
    drinks = [
        {
            "id": r["id"],
            "name": r["name"],
            "producer": r["producer"],
            "vintage": r["vintage"],
            "price": r["price"],
            "note": r["note"],
            "link": r["link"],
        }
        for r in conn.execute(
            "SELECT * FROM drinks WHERE dinner_id = ? ORDER BY sort_order", (dinner_id,)
        )
    ]
    overnight_guests = [
        r["name"]
        for r in conn.execute(
            "SELECT g.name FROM guests g "
            "JOIN dinner_guests dg ON dg.guest_id = g.id "
            "WHERE dg.dinner_id = ? AND dg.stayed_over = 1 "
            "ORDER BY g.name COLLATE NOCASE",
            (dinner_id,),
        )
    ]
    summary.update({
        "notes": row["notes"],
        "dishes": dishes,
        "drinks": drinks,
        "overnightGuests": overnight_guests,
        "overnightFood": row["overnight_food"],
    })
    return summary


# ---------- API ----------

@app.get("/api/config")
def config() -> dict:
    return {"mealiePublicUrl": MEALIE_PUBLIC_URL, "mealieGroupSlug": MEALIE_GROUP_SLUG}


@app.get("/api/dinners")
def list_dinners() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM dinners ORDER BY date DESC, id DESC").fetchall()
        return [dinner_summary_row(conn, r) for r in rows]


@app.get("/api/dinners/{dinner_id}")
def get_dinner(dinner_id: int) -> dict:
    with db() as conn:
        return dinner_detail(conn, dinner_id)


@app.post("/api/dinners", status_code=201)
def create_dinner(payload: DinnerIn) -> dict:
    if not payload.date:
        raise HTTPException(400, "Datum saknas")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO dinners (date, title, host, notes, status, overnight_food) VALUES (?, ?, ?, ?, ?, ?)",
            (
                payload.date, payload.title, payload.host, payload.notes,
                normalize_status(payload.status), payload.overnightFood,
            ),
        )
        dinner_id = cur.lastrowid
        save_dinner_relations(conn, dinner_id, payload)
        return dinner_detail(conn, dinner_id)


@app.put("/api/dinners/{dinner_id}")
def update_dinner(dinner_id: int, payload: DinnerIn) -> dict:
    with db() as conn:
        existing = conn.execute("SELECT id FROM dinners WHERE id = ?", (dinner_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Middagen hittades inte")
        conn.execute(
            "UPDATE dinners SET date = ?, title = ?, host = ?, notes = ?, status = ?, overnight_food = ? WHERE id = ?",
            (
                payload.date, payload.title, payload.host, payload.notes,
                normalize_status(payload.status), payload.overnightFood, dinner_id,
            ),
        )
        save_dinner_relations(conn, dinner_id, payload)
        return dinner_detail(conn, dinner_id)


@app.delete("/api/dinners/{dinner_id}", status_code=204)
def delete_dinner(dinner_id: int) -> Response:
    with db() as conn:
        existing = conn.execute("SELECT id FROM dinners WHERE id = ?", (dinner_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Middagen hittades inte")
        conn.execute("DELETE FROM dinners WHERE id = ?", (dinner_id,))
    return Response(status_code=204)


GUEST_SELECT = (
    "SELECT g.id AS id, g.name AS name, g.is_child AS is_child, "
    "g.family_id AS family_id, f.name AS family_name, "
    "COUNT(dg.dinner_id) AS cnt, MAX(d.date) AS last_date "
    "FROM guests g "
    "LEFT JOIN dinner_guests dg ON dg.guest_id = g.id "
    "LEFT JOIN dinners d ON d.id = dg.dinner_id "
    "LEFT JOIN families f ON f.id = g.family_id "
)


def guest_dict(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "isChild": bool(row["is_child"]),
        "familyId": row["family_id"], "familyName": row["family_name"],
        "count": row["cnt"], "lastDate": row["last_date"],
    }


@app.get("/api/guests")
def list_guests() -> list[dict]:
    with db() as conn:
        # LEFT JOIN (inte JOIN) sa aven gaster utan nagon middag lasta syns -
        # annars gar det inte att hitta och sta bort en gast som blivit
        # overbliven efter att ha tagits bort ur alla middagar den lades pa.
        rows = conn.execute(
            GUEST_SELECT + "GROUP BY g.id ORDER BY g.name COLLATE NOCASE"
        ).fetchall()
        return [guest_dict(r) for r in rows]


@app.post("/api/guests", status_code=201)
def create_guest(payload: GuestIn) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Namn saknas")
    with db() as conn:
        clash = conn.execute(
            "SELECT id FROM guests WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if clash:
            raise HTTPException(409, "En gäst med det namnet finns redan")
        if payload.familyId is not None:
            fam = conn.execute(
                "SELECT id FROM families WHERE id = ?", (payload.familyId,)
            ).fetchone()
            if not fam:
                raise HTTPException(400, "Familjen hittades inte")
        cur = conn.execute(
            "INSERT INTO guests (name, is_child, family_id) VALUES (?, ?, ?)",
            (name, 1 if payload.isChild else 0, payload.familyId),
        )
        row = conn.execute(
            GUEST_SELECT + "WHERE g.id = ? GROUP BY g.id", (cur.lastrowid,)
        ).fetchone()
        return guest_dict(row)


class GuestUpdate(BaseModel):
    name: Optional[str] = None
    isChild: Optional[bool] = None
    familyId: Optional[int] = None


@app.put("/api/guests/{guest_id}")
def update_guest(guest_id: int, payload: GuestUpdate) -> dict:
    with db() as conn:
        existing = conn.execute("SELECT id FROM guests WHERE id = ?", (guest_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Gästen hittades inte")
        if payload.name is not None:
            new_name = payload.name.strip()
            if not new_name:
                raise HTTPException(400, "Namn saknas")
            clash = conn.execute(
                "SELECT id FROM guests WHERE name = ? COLLATE NOCASE AND id != ?",
                (new_name, guest_id),
            ).fetchone()
            if clash:
                raise HTTPException(409, "En gäst med det namnet finns redan")
            conn.execute("UPDATE guests SET name = ? WHERE id = ?", (new_name, guest_id))
        if payload.isChild is not None:
            conn.execute(
                "UPDATE guests SET is_child = ? WHERE id = ?",
                (1 if payload.isChild else 0, guest_id),
            )
        # familyId far medvetet satts till null for att ta bort gasten fran
        # sin familj - darfor kollar vi om faltet skickades med i det hela
        # taget (model_fields_set), inte bara om vardet skiljer sig fran
        # None, annars gar det inte att skilja "rensa familjen" fran
        # "faltet skickades inte med" (t.ex. vid ett rent namnbyte).
        if "familyId" in payload.model_fields_set:
            family_id = payload.familyId
            if family_id is not None:
                fam = conn.execute(
                    "SELECT id FROM families WHERE id = ?", (family_id,)
                ).fetchone()
                if not fam:
                    raise HTTPException(400, "Familjen hittades inte")
            conn.execute(
                "UPDATE guests SET family_id = ? WHERE id = ?", (family_id, guest_id)
            )
        row = conn.execute(
            GUEST_SELECT + "WHERE g.id = ? GROUP BY g.id", (guest_id,)
        ).fetchone()
        return guest_dict(row)


@app.delete("/api/guests/{guest_id}", status_code=204)
def delete_guest(guest_id: int) -> Response:
    with db() as conn:
        existing = conn.execute("SELECT id FROM guests WHERE id = ?", (guest_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Gästen hittades inte")
        in_use = conn.execute(
            "SELECT COUNT(*) c FROM dinner_guests WHERE guest_id = ?", (guest_id,)
        ).fetchone()["c"]
        if in_use:
            raise HTTPException(409, "Gästen är kopplad till minst en middag och kan inte tas bort")
        conn.execute("DELETE FROM guests WHERE id = ?", (guest_id,))
    return Response(status_code=204)


@app.get("/api/families")
def list_families() -> list[dict]:
    with db() as conn:
        families = conn.execute(
            "SELECT id, name FROM families ORDER BY name COLLATE NOCASE"
        ).fetchall()
        members = conn.execute(
            "SELECT id, name, is_child, family_id FROM guests "
            "WHERE family_id IS NOT NULL ORDER BY name COLLATE NOCASE"
        ).fetchall()
        by_family: dict[int, list[dict]] = {}
        for m in members:
            by_family.setdefault(m["family_id"], []).append(
                {"id": m["id"], "name": m["name"], "isChild": bool(m["is_child"])}
            )
        return [
            {"id": f["id"], "name": f["name"], "members": by_family.get(f["id"], [])}
            for f in families
        ]


@app.post("/api/families", status_code=201)
def create_family(payload: FamilyIn) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Namn saknas")
    with db() as conn:
        clash = conn.execute(
            "SELECT id FROM families WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if clash:
            raise HTTPException(409, "En familj med det namnet finns redan")
        cur = conn.execute("INSERT INTO families (name) VALUES (?)", (name,))
        return {"id": cur.lastrowid, "name": name, "members": []}


@app.put("/api/families/{family_id}")
def update_family(family_id: int, payload: FamilyIn) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Namn saknas")
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM families WHERE id = ?", (family_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Familjen hittades inte")
        clash = conn.execute(
            "SELECT id FROM families WHERE name = ? COLLATE NOCASE AND id != ?",
            (name, family_id),
        ).fetchone()
        if clash:
            raise HTTPException(409, "En familj med det namnet finns redan")
        conn.execute("UPDATE families SET name = ? WHERE id = ?", (name, family_id))
        members = conn.execute(
            "SELECT id, name, is_child FROM guests WHERE family_id = ? "
            "ORDER BY name COLLATE NOCASE",
            (family_id,),
        ).fetchall()
        return {
            "id": family_id, "name": name,
            "members": [
                {"id": m["id"], "name": m["name"], "isChild": bool(m["is_child"])}
                for m in members
            ],
        }


@app.delete("/api/families/{family_id}", status_code=204)
def delete_family(family_id: int) -> Response:
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM families WHERE id = ?", (family_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Familjen hittades inte")
        # Medlemmarna blockerar INTE borttagning (till skillnad fran hur
        # toppings/gaster hanteras nar de anvands pa en middag) - de blir
        # kvar som vanliga gaster, bara utan familj, via ON DELETE SET NULL
        # pa guests.family_id. Rimligt att kunna ta bort en felskapad
        # familj utan att forst behova plocka bort varje medlem for hand.
        conn.execute("DELETE FROM families WHERE id = ?", (family_id,))
    return Response(status_code=204)


@app.get("/api/guests/{name}/dinners")
def guest_history(name: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT d.* FROM dinners d "
            "JOIN dinner_guests dg ON dg.dinner_id = d.id "
            "JOIN guests g ON g.id = dg.guest_id "
            "WHERE g.name = ? COLLATE NOCASE "
            "ORDER BY d.date DESC",
            (name,),
        ).fetchall()
        return [dinner_summary_row(conn, r) for r in rows]


@app.get("/api/hosts")
def list_hosts() -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT host FROM dinners "
            "WHERE host IS NOT NULL AND host != '' "
            "ORDER BY host COLLATE NOCASE"
        ).fetchall()
        return [r["host"] for r in rows]


@app.get("/api/toppings")
def list_toppings() -> list[dict]:
    """
    Hela topping-biblioteket (t.ex. pizza-toppings), grupperat per
    kategori i svaret genom sorteringen - fronten grupperar visuellt.
    Fristaende fran middagarna, sa listan kan byggas upp i forvag.
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT t.id, t.name, t.category, COUNT(dt.dish_id) AS uses "
            "FROM toppings t "
            "LEFT JOIN dish_toppings dt ON dt.topping_id = t.id "
            "GROUP BY t.id "
            "ORDER BY COALESCE(NULLIF(t.category, ''), 'Övrigt') COLLATE NOCASE, t.name COLLATE NOCASE"
        ).fetchall()
        return [
            {"id": r["id"], "name": r["name"], "category": r["category"], "uses": r["uses"]}
            for r in rows
        ]


@app.post("/api/toppings", status_code=201)
def create_topping(payload: ToppingIn) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Namn saknas")
    category = (payload.category or "").strip() or None
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM toppings WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing:
            raise HTTPException(409, "Den toppingen finns redan")
        cur = conn.execute(
            "INSERT INTO toppings (name, category) VALUES (?, ?)", (name, category)
        )
        return {"id": cur.lastrowid, "name": name, "category": category, "uses": 0}


@app.delete("/api/toppings/{topping_id}", status_code=204)
def delete_topping(topping_id: int) -> Response:
    with db() as conn:
        existing = conn.execute("SELECT id FROM toppings WHERE id = ?", (topping_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Toppingen hittades inte")
        in_use = conn.execute(
            "SELECT COUNT(*) c FROM dish_toppings WHERE topping_id = ?", (topping_id,)
        ).fetchone()["c"]
        if in_use:
            raise HTTPException(409, "Toppingen används i minst en middag och kan inte tas bort")
        conn.execute("DELETE FROM toppings WHERE id = ?", (topping_id,))
    return Response(status_code=204)


async def mealie_get_recipe_ingredients(slug: str) -> list[str]:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(f"{MEALIE_URL}/api/recipes/{slug}", headers=MEALIE_HEADERS)
        except httpx.HTTPError:
            return []
    if resp.status_code != 200:
        return []
    data = resp.json()
    ingredients = []
    for ing in data.get("recipeIngredient", []):
        text = ing.get("display") or ing.get("note") or ing.get("originalText") or ""
        if text:
            ingredients.append(text)
    return ingredients


@app.get("/api/dinners/{dinner_id}/shopping-list")
async def shopping_list(dinner_id: int) -> dict:
    """
    Samlar ihop ingredienserna fran alla ratter i middagen som ar kopplade
    till ett Mealie-recept, grupperat per ratt - sa man kan handla infor en
    kommande middag utan att oppna varje recept for sig.
    """
    with db() as conn:
        exists = conn.execute("SELECT id FROM dinners WHERE id = ?", (dinner_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Middagen hittades inte")
        dish_rows = conn.execute(
            "SELECT name, mealie_slug FROM dishes WHERE dinner_id = ? ORDER BY sort_order",
            (dinner_id,),
        ).fetchall()

    async def build_group(name: str, slug: Optional[str]) -> dict:
        if not slug:
            return {"dishName": name, "mealieSlug": None, "linked": False, "ingredients": []}
        ingredients = await mealie_get_recipe_ingredients(slug)
        return {"dishName": name, "mealieSlug": slug, "linked": True, "ingredients": ingredients}

    groups = await asyncio.gather(*[build_group(r["name"], r["mealie_slug"]) for r in dish_rows])
    return {"dinnerId": dinner_id, "groups": list(groups)}


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
                params={"search": q, "perPage": 10},
            )
        except httpx.HTTPError:
            return []
    if resp.status_code != 200:
        return []
    data = resp.json()
    items = data.get("items", []) if isinstance(data, dict) else data
    return [{"name": r["name"], "slug": r["slug"]} for r in items]


# Statisk frontend - matchas sist, sa /api/* ovan gar fore. Sokvagen racknas
# ut relativt till den har filen sa det fungerar oavsett var processen
# startas ifran (viktigt nu nar appen monteras in i en storre huvudapp).
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
