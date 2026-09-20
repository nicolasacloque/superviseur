# Superviseur BACnet/IP

Superviseur GTB open source : découverte et lecture de points BACnet/IP, historisation, alarmes,
synoptiques HTML. Cahier des charges : [SPEC.md](SPEC.md).

**Avancement : Jalon 3 (API, temps réel, écriture).** PostgreSQL + TimescaleDB, Redis, collecteur
BACnet/IP, simulateur de 2000 points, API REST + WebSocket avec authentification par rôles.

## Démarrage

```bash
cp .env.example .env        # mots de passe "change-me" à remplacer ; JWT_SECRET : openssl rand -hex 32
docker compose up -d --build --wait
curl http://127.0.0.1:8000/health
```

Réponse attendue :

```json
{"status": "ok", "services": {"db": "ok", "redis": "ok"}}
```

Documentation OpenAPI : http://127.0.0.1:8000/api/docs

## API

Premier utilisateur (le mot de passe est demandé ; 10 caractères minimum) :

```bash
docker compose exec api python -m app.auth.cli create-user admin --role admin
```

Rôles : `viewer` (lecture), `operator` (+ consignes, acquittement), `engineer`, `admin` (+ audit).
La session repose sur deux cookies `HttpOnly` / `SameSite=Strict` ; en HTTP local, mettre
`COOKIE_SECURE=false` dans `.env`.

```bash
B=http://127.0.0.1:8000/api/v1
curl -c jar -H 'Content-Type: application/json' -d '{"login":"admin","password":"..."}' $B/auth/login
curl -b jar "$B/points?q=temp&limit=5"                      # recherche paginée (path, tag, device, q)
curl -b jar "$B/points/<id>/history?bucket=1h"              # moyenne, min, max par tranche
curl -b jar -H 'Content-Type: application/json' \
     -d '{"value":21.5,"priority":8}' $B/points/<id>/write   # "value": null relâche la priorité
```

Temps réel : `ws://127.0.0.1:8000/api/v1/ws` (cookie de session). Messages client :
`{"action":"subscribe","points":["<uuid>", ...]}`, `unsubscribe`, `ping` (500 points au maximum par
connexion). Le serveur envoie la dernière valeur connue à la souscription, puis
`{"type":"value","point":"<uuid>","ts":"...","value":21.4,"status":"ok"}` à chaque changement.
Code de fermeture 4401 : session expirée, appeler `POST /auth/refresh` puis se reconnecter.

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
| `JWT_SECRET` | Signature des JWT (obligatoire, 32 caractères minimum) |
| `COOKIE_SECURE` | Cookies de session en `Secure` (défaut `true`) |
| `WRITE_TIMEOUT_S` | Attente de la réponse du collecteur à une écriture (défaut 10) |
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
backend/app/api/       application FastAPI : routes REST, WebSocket, audit
backend/app/auth/      mots de passe argon2id, JWT, rôles, CLI utilisateurs
backend/alembic/       migrations (0001 = schéma initial + rôles)
docs/QUESTIONS.md      décisions par défaut à confirmer
```
