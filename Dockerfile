FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*

# Poetry (utilise pyproject.toml + poetry.lock)
RUN pip install --no-cache-dir poetry \
  && poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock /app/
RUN poetry install --only main --no-root --no-interaction --no-ansi

COPY claude_code_internal /app/claude_code_internal

# Optionnel: un dossier vide pour les artefacts (peut être monté en volume)
RUN mkdir -p /app/install_artifacts

EXPOSE 8001 8002 8080

# Par défaut, ne lance rien (compose choisit la commande).
CMD ["python", "-c", "print('Use docker compose to run services')"]

