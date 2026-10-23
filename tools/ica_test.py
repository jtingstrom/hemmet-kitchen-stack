"""
ICA-test (bara LASANDE): loggar in och skriver ut dina inkopslistor.
Skapar, andrar och tar bort ingenting hos ICA.

Anvander ica-mcp-paketets klient (inofficiellt, community-gjort):
https://github.com/kanylbullen/ica-mcp

Laser ICA_PERSONNUMMER och ICA_PASSWORD ur secrets.env i repots rotmapp.
Kors i en tillfallig Docker-container - inget installeras pa din dator.

Read-only ICA login test (Swedish messages). Reads secrets.env from the repo
root. See the README ("ICA integration") for the docker run command.
"""
import os
import sys


def load_env(path):
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            values[key.strip()] = val
    return values


secrets_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets.env")
if not os.path.exists(secrets_path):
    sys.exit("Hittar inte secrets.env i repots rotmapp (kopiera secrets.env.example).")

env = load_env(secrets_path)
user = env.get("ICA_PERSONNUMMER", "")
password = env.get("ICA_PASSWORD", "")
if not user or not password:
    sys.exit("ICA_PERSONNUMMER / ICA_PASSWORD ar tomma i secrets.env - fyll i dem forst.")

import requests  # noqa: E402
from ica_mcp.client import IcaClient, IcaError  # noqa: E402

# Skriver INTE ut sjalva uppgifterna - bara form/langd, sa du kan se om
# nagot blivit fel vid inlasningen (t.ex. extra tecken eller mellanslag).
print("Inlasta uppgifter (utan att visa dem):")
print(f"    personnummer: {len(user)} tecken, bara siffror: {user.isdigit()}, "
      f"innehaller bindestreck: {'-' in user}, innehaller mellanslag: {' ' in user}")
print(f"    losenord:     {len(password)} tecken, "
      f"borjar/slutar med mellanslag: {password != password.strip()}")

# Sessionen sparas bara i containern (/tmp) och forsvinner nar den stoppas.
client = IcaClient(username=user, password=password, state_file="/tmp/ica_state.json")

try:
    print("1/2 Loggar in hos ICA ...", flush=True)
    client.authenticate(force=True)
    name = client.whoami()
    print("    OK - inloggad som:", name or "(namn okant)")

    print("2/2 Hamtar dina inkopslistor ...")
    lists = client.get_lists()
    if not lists:
        print("    Inga inkopslistor hittades (men anslutningen fungerar).")
    for lst in lists:
        title = lst.get("title") or "(utan namn)"
        rows = lst.get("rows") or []
        print(f"    - {title}: {len(rows)} varor")
    print("\nKLART - allt lasande fungerade.")
except requests.HTTPError as e:
    body = (e.response.text or "").strip().replace("\n", " ")[:400] if e.response is not None else ""
    sys.exit(f"\nMISSLYCKADES: {e}\nSvar fran ICA: {body or '(inget)'}")
except IcaError as e:
    sys.exit(f"\nMISSLYCKADES: {e}")
except Exception as e:  # noqa: BLE001
    sys.exit(f"\nMISSLYCKADES ({type(e).__name__}): {e}")
