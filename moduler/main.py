"""
Hemmet - roten som binder ihop alla egna moduler i en och samma container:

    /recept    -> Receptvy    (receptvy/main.py)
    /bjudning  -> Bjudningsloggen (bjudningslogg/main.py)
    /plan      -> Planering   (planering/main.py)
    /          -> Startsidan "Köket" (rena statiska filer, ingen egen app)

Mealie är fortsatt sin egen, officiella container - den rör vi aldrig vid,
den uppdateras genom att hämta en ny version av hela dess image och allt
eget vi skulle lägga däri skulle försvinna vid nästa uppdatering.

Receptvy och Bjudningsloggen är däremot vår egen kod, så de kan köras som
"under-appar" i samma process via FastAPI/Starlettes app.mount(). Det ger
en enda container, en enda IP-adress, och en delad databasfil istället för
en per modul - och gör det enkelt att lägga till fler moduler senare: en
ny mapp med en egen main.py, och en rad här som monterar in den.

Viktigt att känna till: en under-app som monteras in så här får INTE sin
egen @app.on_event("startup") avfyrad automatiskt (testat och verifierat
separat) - därför anropar vi Bjudningsloggens init_db() explicit nedan
istället för att lita på att den körs av sig själv.
"""

import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from receptvy.main import app as receptvy_app
from bjudningslogg.main import app as bjudningslogg_app, init_db as bjudningslogg_init_db
from planering.main import app as planering_app, init_db as planering_init_db

app = FastAPI(title="Hemmet")


@app.on_event("startup")
def on_startup() -> None:
    bjudningslogg_init_db()
    planering_init_db()


# Explicita omdirigeringar for "/recept" och "/bjudning" UTAN avslutande
# snedstreck - mAste finnas. Utan dem fangar startsidans "fanga allt"-
# montering pa "/" langst ner upp just den exakta sokvagen (dess regex
# matchar bokstavligen allt som borjar med "/") INNAN Starlettes egen
# inbyggda "lagg till ett snedstreck och forsok igen"-logik hinner trigga
# - den koras bara om INGEN rutt alls matchade forst. Testat och
# verifierat separat: utan dessa tva rader ger t.ex. en adress som
# slutar med bara "/recept" (ingen avslutande /) ett 404-fel istallet
# for att ta dig till sidan.
@app.get("/recept", include_in_schema=False)
def redirect_recept() -> RedirectResponse:
    return RedirectResponse(url="/recept/")


@app.get("/bjudning", include_in_schema=False)
def redirect_bjudning() -> RedirectResponse:
    return RedirectResponse(url="/bjudning/")


@app.get("/plan", include_in_schema=False)
def redirect_plan() -> RedirectResponse:
    return RedirectResponse(url="/plan/")


# Viktigt: specifika sökvägar (/recept, /bjudning, /plan) måste monteras FÖRE den
# breda "/" längst ner, annars skulle startsidans StaticFiles-montering
# fånga upp allt och de andra två aldrig nås.
app.mount("/recept", receptvy_app)
app.mount("/bjudning", bjudningslogg_app)
app.mount("/plan", planering_app)

STARTSIDA_DIR = os.path.join(os.path.dirname(__file__), "startsida", "static")
app.mount("/", StaticFiles(directory=STARTSIDA_DIR, html=True), name="startsida")
