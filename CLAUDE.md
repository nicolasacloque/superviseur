# Superviseur BACnet/IP

Le cahier des charges complet est dans [SPEC.md](SPEC.md) : le lire en entier avant de coder.

## Règles clés

- Travailler jalon par jalon (section 15) ; ne pas commencer le jalon N+1 avant validation du jalon N.
- Comportement non décrit dans SPEC.md : l'ajouter à `docs/QUESTIONS.md` et choisir l'option la plus simple.
- Python 3.12, mypy strict, ruff ; commentaires en français, identifiants en anglais.
- Aucun secret dans le dépôt : tout passe par les variables d'environnement (`.env.example`).

## Commandes (depuis `backend/`)

```bash
pip install -e ../simulator -e ".[dev]"
ruff check . && ruff format --check .
mypy
pytest                       # tests unitaires
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/supervisor_test pytest   # + migrations
```

Frontend (depuis `frontend/`) : `npm ci && npm run typecheck && npm test`. Tests d'acceptation : `e2e/` (Playwright, stack + simulateur requis, voir README).

Stack complète : `cp .env.example .env`, `deploy/gen-cert.sh localhost`, puis `docker compose up -d --build --wait` (Nginx en HTTPS est le seul point d'entrée).
Scripts d'exploitation (`deploy/`) : `deploy/test-scripts.sh` (sans Docker), `deploy/security-check.sh`, `backup.sh`, `restore.sh`. `docs/API.md` se régénère avec `cd backend && python -m app.api.apidoc > ../docs/API.md`.

## État

Jalons 1 à 7 livrés (socle, simulateur + collecteur, API + WebSocket + écriture, historiques et widget `trend`, alarmes, synoptiques, production : Nginx + TLS, verrouillage de compte, utilisateurs, sauvegardes, systemd, audit de sécurité, documentation d'exploitation). Toute la spécification est implémentée ; le reste relève de `docs/QUESTIONS.md` (décision 60).
Voir README.md et docs/QUESTIONS.md.
