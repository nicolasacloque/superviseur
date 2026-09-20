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

Stack complète : `cp .env.example .env` puis `docker compose up -d --build --wait`.

## État

Jalons 1 (Socle), 2 (simulateur + collecteur) et 3 (API, WebSocket, écriture) livrés ; le Jalon 4 n'est pas commencé.
Voir README.md et docs/QUESTIONS.md.
