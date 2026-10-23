# Arkitektur och designprinciper (svenska)

Fullständig version på engelska: [ARCHITECTURE.md](ARCHITECTURE.md). Här är
sammanfattningen.

## Två containrar, en datamapp

- **mealie**: den officiella imagen (versionen är pinnad). Receptdatabas och
  redigerare med egen databas. Vi ändrar aldrig i den.
- **moduler**: en enda egen Python-container (FastAPI) som innehåller alla
  egna appar. Den når Mealie via Dockers interna nät (`http://mealie:9000`)
  med en API-token. Webbläsaren får aldrig tokenen, bara Mealies publika
  adress för bilder och länkar.

## Kärnidén: en modul är en mapp

Varje modul är en mapp med `main.py` (litet FastAPI-API, ofta med `init_db()`)
och `static/index.html` (hela gränssnittet i en fil, vanilla JavaScript, inget
byggsteg). Rotappen monterar modulerna med `app.mount()`:

- `/` startsida (bara statiska filer, monteras **sist**)
- `/recept` Receptvy
- `/bjudning` Bjudningsloggen
- `/plan` Planering

Lägga till en modul: ny mapp, några rader i `moduler/main.py`, en rad i
Dockerfile och ett kort på startsidan. Se checklistan och kodexemplet i
[ARCHITECTURE.md](ARCHITECTURE.md).

Fällor och lösningar: under-appar får inget eget startup-event (rotappen
anropar `init_db()` explicit), prefix utan snedstreck kräver egna
redirect-rutter, specifika prefix måste monteras före `/`, frontend använder
relativa fetch-sökvägar, och tidszon hanteras med `TZ`, `tzdata`,
`date('now','localtime')` och lokala datumkomponenter i JavaScript.

## Data

En delad SQLite-fil i en bind-mount (`./data/bjudningslogg/`). Varje modul äger
egna tabeller. Scheman skapas idempotent med `CREATE TABLE IF NOT EXISTS` vid
start, utan migreringsverktyg. Säkerhetskopiera genom att kopiera `data/`.

## Frontend

En HTML-fil per modul med inbyggd CSS/JS, inga externa anrop, mobil- och
surfplattevänligt, ljust och mörkt läge via CSS-variabler, och information som
aldrig bara förmedlas med färg.

## ICA (valfritt, experimentellt)

Inofficiell communityklient (`ica-mcp`, pinnad version) mot ICA:s privata API.
Inloggningsuppgifter ligger i `secrets.env` som är valfri och ignoreras av git.
Kopplingen är isolerad i Planering så att resten fungerar utan den.

## Principer

1. Ett lager per ansvar: Mealie äger recepten, `moduler` äger resten.
2. Enkelhet före ramverk.
3. Delad databas, separata tabeller.
4. Lokalt först, bara hemmanätverk. Modulerna saknar inloggning.
5. Pinnade beroenden.
6. Gemensamt utseende och navigering.
