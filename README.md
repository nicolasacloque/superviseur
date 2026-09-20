# Superviseur BACnet/IP

Superviseur GTB open source : découverte et lecture de points BACnet/IP, historisation, alarmes,
synoptiques HTML. Cahier des charges : [SPEC.md](SPEC.md).

**Avancement : Jalon 1 (Socle).** Au programme : PostgreSQL + TimescaleDB, Redis, API FastAPI
(`/health`), migrations Alembic, CI. Le collecteur BACnet arrive au Jalon 2.

## Démarrage

```bash
cp .env.example .env        # puis remplacer les mots de passe "change-me"
docker compose up -d --build --wait
curl http://127.0.0.1:8000/health
```

Réponse attendue :

```json
{"status": "ok", "services": {"db": "ok", "redis": "ok"}}
```

Documentation OpenAPI : http://127.0.0.1:8000/api/docs

Arrêt : `docker compose down` (ajouter `-v` pour supprimer les données).

## Variables d'environnement

| Variable | Rôle |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Base PostgreSQL (obligatoires) |
| `DATABASE_URL`, `REDIS_URL` | Construites par Compose ; à définir en développement local |
| `JWT_SECRET` | Signature des JWT (utilisé dès le Jalon 3) |
| `LOG_LEVEL` | Niveau de logs (défaut `INFO`), format JSON sur stdout |
| `RETENTION_DAYS` | Rétention de l'historique (défaut 730) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | Notifications email (Jalon 5) |
| `BACNET_BIND_IP` | Interface du collecteur (Jalon 2) |

## Développement

Python 3.12 requis.

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check . && ruff format --check .
mypy
pytest
```

Les tests de migration exigent une base TimescaleDB dont le nom contient « test » (ils suppriment
les tables) ; sans `TEST_DATABASE_URL` ils sont ignorés :

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/supervisor_test pytest
```

Nouvelle migration : `alembic revision --autogenerate -m "description"` depuis `backend/`
(avec `DATABASE_URL` défini).

## Structure

```
docker-compose.yml     db, redis, api
backend/app/common/    configuration, logs JSON, enums partagés
backend/app/db/        modèles SQLAlchemy, session
backend/app/api/       application FastAPI, /health
backend/alembic/       migrations (0001 = schéma initial + rôles)
docs/QUESTIONS.md      décisions par défaut à confirmer
```
