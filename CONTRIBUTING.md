# Contributing

Thanks for taking an interest! This is a **hobby project** that runs a real
household kitchen, so I answer issues and pull requests when I have time, and
I may say no to changes that don't fit the project. Nothing is merged without
my approval.

## Before you start

- **Never post secrets.** Do not paste Mealie tokens, ICA credentials, your
  `.env` or `secrets.env` into issues, pull requests or logs. Remove them
  first.
- The UI is in Swedish. Translations and improvements to other languages are
  welcome, but please open an issue first so we can agree on the approach.

## Reporting a bug

Open an issue and include:

1. What you did and what you expected.
2. What actually happened (error message, screenshot).
3. How you run it: OS, Docker version, and the output of
   `docker compose logs moduler` (with secrets removed).

## Suggesting an idea

Open an issue describing the problem you want solved before writing code.
That avoids work on something that doesn't fit.

## Sending a change (pull request)

1. Fork the repository and create a branch (`git checkout -b my-change`).
2. Keep the change small and focused: one idea per pull request.
3. Follow the style of the surrounding code (plain FastAPI + SQLite on the
   backend, a single HTML file with vanilla JavaScript per module, no build
   step, no new heavy dependencies without discussion).
4. Test it: `docker compose build moduler && docker compose up -d moduler`,
   then click through the page in both light and dark mode.
5. Open the pull request and say what you changed and why.

### Adding a new module

Follow the checklist in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): a new
folder with `main.py` and `static/index.html`, a few lines in
`moduler/main.py`, a `COPY` line in the Dockerfile and a card on the start
page. Keep the data in its own tables in the shared SQLite file and create
them idempotently (`CREATE TABLE IF NOT EXISTS`).

### Things that are out of scope

- Adding authentication or exposing the apps to the internet is a bigger
  design decision; please discuss it in an issue first.
- Features that depend on private/unofficial APIs (like the ICA client) are
  kept optional and isolated; I may decline further additions of that kind.

By contributing you agree that your contribution is licensed under the
project's [MIT license](LICENSE).

---

## Svenska (kort)

Det här är ett hobbyprojekt; jag svarar när jag hinner och kan tacka nej till
ändringar som inte passar. Inget slås ihop utan mitt godkännande.

- **Lägg aldrig ut hemligheter** (Mealie-token, ICA-uppgifter, `.env`,
  `secrets.env`) i issues, pull requests eller loggar.
- **Fel:** öppna en issue med vad du gjorde, vad som hände och
  `docker compose logs moduler` (utan hemligheter).
- **Idéer:** öppna en issue först, innan du skriver kod.
- **Ändringar:** forka, gör en egen gren, håll ändringen liten och testa den
  (`docker compose build moduler && docker compose up -d moduler`), öppna sedan
  en pull request.
- **Ny modul:** följ checklistan i [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
