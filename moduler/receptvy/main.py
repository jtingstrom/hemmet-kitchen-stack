"""
Receptvy - en egen, snygg vy ovanpa Mealie.

Denna lilla backend gor tva saker:
1. Haller Mealie-API-token hemligt pa servern (aldrig i webblasaren).
2. Forenklar Mealies API-svar till precis det fronten behover, sa att
   index.html kan vara enkel och snabb.

Allt skrivande (lagga till/redigera/ta bort recept, importera fran URL)
gors fortfarande i Mealies eget granssnitt - den har appen bara visar.

Monteras som en under-app pa /recept av moduler/main.py (se den filen) -
darfor laddas "static" via en sokvag relativ till DENNA fil, inte relativt
till var processen rakar starta.
"""

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

MEALIE_URL = os.environ.get("MEALIE_URL", "http://mealie:9000").rstrip("/")
MEALIE_PUBLIC_URL = os.environ.get("MEALIE_PUBLIC_URL", "http://localhost:9000").rstrip("/")
MEALIE_API_TOKEN = os.environ.get("MEALIE_API_TOKEN", "")
# Mealies recept-sidor ligger under /g/{grupp-slug}/r/{recept-slug}. En
# fabriksny installation heter gruppen "Home" (slug "home") - stämmer det
# inte hos er, sätt MEALIE_GROUP_SLUG i .env till rätt värde istället.
MEALIE_GROUP_SLUG = os.environ.get("MEALIE_GROUP_SLUG", "home")

if not MEALIE_API_TOKEN:
    print(
        "VARNING: MEALIE_API_TOKEN ar inte satt. Satt den i .env - skapa "
        "token i Mealie under Installningar -> Min profil -> API-tokens."
    )

HEADERS = {"Authorization": f"Bearer {MEALIE_API_TOKEN}"}

app = FastAPI(title="Receptvy")


async def mealie_get(path: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{MEALIE_URL}{path}", headers=HEADERS, params=params)
    if resp.status_code == 401:
        raise HTTPException(502, "Mealie avvisade API-token - kontrollera MEALIE_API_TOKEN i .env")
    if resp.status_code >= 400:
        raise HTTPException(502, f"Mealie svarade {resp.status_code} for {path}")
    return resp.json()


def image_url(recipe_id: str, size: str = "min-original.webp") -> str:
    return f"{MEALIE_PUBLIC_URL}/api/media/recipes/{recipe_id}/images/{size}"


def first_category(recipe: dict) -> str | None:
    cats = recipe.get("recipeCategory") or []
    return cats[0]["name"] if cats else None


@app.get("/api/config")
async def config() -> dict:
    return {
        "mealiePublicUrl": MEALIE_PUBLIC_URL,
        # Knapparna i headern: oppna Mealie respektive skapa nytt recept.
        "mealieHomeUrl": f"{MEALIE_PUBLIC_URL}/g/{MEALIE_GROUP_SLUG}",
        "mealieCreateUrl": f"{MEALIE_PUBLIC_URL}/g/{MEALIE_GROUP_SLUG}/r/create/new",
    }


@app.get("/api/recipes")
async def list_recipes() -> list[dict]:
    data = await mealie_get(
        "/api/recipes",
        params={"perPage": 500, "orderBy": "created_at", "orderDirection": "desc"},
    )
    items = data.get("items", []) if isinstance(data, dict) else data
    out = []
    for r in items:
        out.append(
            {
                "id": r["id"],
                "slug": r["slug"],
                "name": r["name"],
                "category": first_category(r),
                "totalTime": r.get("totalTime"),
                "servings": r.get("recipeYield"),
                "image": image_url(r["id"], "min-original.webp"),
            }
        )
    return out


@app.get("/api/recipes/{slug}")
async def get_recipe(slug: str) -> dict:
    r = await mealie_get(f"/api/recipes/{slug}")

    ingredients = []
    for ing in r.get("recipeIngredient", []):
        text = ing.get("display") or ing.get("note") or ing.get("originalText") or ""
        if text:
            ingredients.append(text)

    steps = []
    for step in r.get("recipeInstructions", []):
        text = (step.get("text") or "").strip()
        if text:
            steps.append(text)

    return {
        "id": r["id"],
        "slug": r["slug"],
        "name": r["name"],
        "category": first_category(r),
        "totalTime": r.get("totalTime"),
        "servings": r.get("recipeYield"),
        "image": image_url(r["id"], "original.webp"),
        "ingredients": ingredients,
        "steps": steps,
        "sourceUrl": r.get("orgURL"),
        "mealieEditUrl": f"{MEALIE_PUBLIC_URL}/g/{MEALIE_GROUP_SLUG}/r/{r['slug']}?edit=true",
    }


@app.get("/api/categories")
async def categories() -> list[str]:
    data = await mealie_get("/api/organizers/categories", params={"perPage": 200})
    items = data.get("items", []) if isinstance(data, dict) else data
    names = sorted({c["name"] for c in items if c.get("name")})
    return names


# Statisk frontend - matchas sist, sa /api/* ovan gar fore. Sokvagen racknas
# ut relativt till den har filen sa det fungerar oavsett var processen
# startas ifran (viktigt nu nar appen monteras in i en storre huvudapp).
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
