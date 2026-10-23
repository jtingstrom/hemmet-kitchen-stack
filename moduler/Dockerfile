FROM python:3.12-slim

WORKDIR /app

# tzdata behovs for att TZ-miljovariabeln (satt i docker-compose.yml) ska ge
# ratt svensk tid i containern - annars rattar sig t.ex. "dagens datum" i
# databasen (Bjudningsloggens automatiska planerad->genomford-logik) efter
# UTC istallet, vilket kan slanga om en middag nagra timmar fel runt midnatt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Inofficiell ICA-klient (community). Fastlast version - ICA:s privata API
# kan andras, och da vill vi uppdatera medvetet, inte av misstag. --no-deps:
# vi behover bara klienten (ica_mcp.client), inte MCP-servern runt den.
RUN pip install --no-cache-dir --no-deps ica-mcp==0.5.0

COPY main.py .
COPY receptvy/ ./receptvy/
COPY bjudningslogg/ ./bjudningslogg/
COPY planering/ ./planering/
COPY startsida/ ./startsida/

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
