# Superviseur BACnet/IP

Superviseur GTB open source : découverte et lecture de points BACnet/IP, historisation, alarmes,
synoptiques HTML. Cahier des charges : [SPEC.md](SPEC.md).

**Avancement : Jalon 2 (simulateur + collecteur).** PostgreSQL + TimescaleDB, Redis, API FastAPI
(`/health`), migrations Alembic, CI, collecteur BACnet/IP et simulateur de 2000 points.

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

## Collecteur BACnet et simulateur

Le collecteur (`collector`) est le seul composant qui parle BACnet. Il tourne en `network_mode: host`
pour recevoir les broadcasts du bon sous-réseau. Sa configuration est dans
[config/collector.yaml](config/collector.yaml) (interface, plage Who-Is, polling, COV) ;
`BACNET_BIND_IP` dans `.env` remplace `network.bind_ip`.

Essai complet sur le simulateur (10 devices x 200 points, un port UDP par device) :

```bash
echo "COLLECTOR_CONFIG=/config/collector.sim.yaml" >> .env
docker compose --profile sim up -d --build --wait
docker compose exec db psql -U supervisor -d supervisor -c "select count(*) from point_latest"
```

Les valeurs sont publiées sur Redis (`point.value.<uuid du point>`) et historisées selon la deadband et
`max_interval_s` de chaque point. Une écriture se fait en ajoutant une commande au stream `cmd.write`
(l'API le fera au Jalon 3) :

```bash
docker compose exec redis redis-cli XADD cmd.write '*' data \
  '{"command_id":"c1","point_id":"<uuid>","value":21.5,"priority":8,"user_id":"<uuid operator>"}'
```

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
pip install -e ../simulator -e ".[dev]"

ruff check . && ruff format --check .
mypy
pytest
```

Les tests de migration et d'intégration (collecteur contre le simulateur) exigent une base dont le nom
contient « test » (ils suppriment les tables) ; sans `TEST_DATABASE_URL` ils sont ignorés. `TEST_REDIS_URL`
est facultatif (sinon Redis est simulé en mémoire) :

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/supervisor_test \
TEST_REDIS_URL=redis://localhost:6379/0 pytest
```

Nouvelle migration : `alembic revision --autogenerate -m "description"` depuis `backend/`
(avec `DATABASE_URL` défini).

## Structure

```
docker-compose.yml     db, redis, api, collector, simulator (profil sim)
config/                collector.yaml (réseau réel), collector.sim.yaml (simulateur)
simulator/             simulateur BACnet (bacpypes3), défauts injectables
backend/app/collector/ driver BACnet, découverte, polling, COV, écriture, historisation
backend/app/common/    configuration, logs JSON, enums partagés
backend/app/db/        modèles SQLAlchemy, session
backend/app/api/       application FastAPI, /health
backend/alembic/       migrations (0001 = schéma initial + rôles)
docs/QUESTIONS.md      décisions par défaut à confirmer
```
