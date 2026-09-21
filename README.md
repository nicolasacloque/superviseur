# Superviseur BACnet/IP

Superviseur GTB open source : découverte et lecture de points BACnet/IP, historisation, alarmes,
synoptiques HTML. Cahier des charges : [SPEC.md](SPEC.md).

**Avancement : Jalon 5 (alarmes).** PostgreSQL + TimescaleDB (hypertable compressée), Redis, collecteur
BACnet/IP, simulateur de 2000 points, API REST + WebSocket avec authentification par rôles, moteur
d'alarmes (email, webhook), widgets `trend` et `alarm_list`.

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
curl -b jar "$B/points/<id>/history?bucket=auto&max_points=500"  # tranche choisie par le serveur
curl -b jar -X PATCH -H 'Content-Type: application/json' \
     -d '{"deadband":0.5,"max_interval_s":300}' $B/points/<id>   # réglage d'un point (ingénieur)
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

## Alarmes

Le service `alarms` évalue les règles et applique la machine à états (déclenchée non acquittée, acquittée,
terminée non acquittée, retour à la normale). Six types de règles : `high`, `low` (seuil et hystérésis),
`state` (valeur attendue), `stale` (aucune valeur depuis N secondes), `comm_lost` (device hors ligne),
`bacnet_event` (Event Notification du contrôleur). Chaque règle a une temporisation (`delay_s`), une
sévérité et des canaux de notification : `email`, `email:adresse`, `webhook:https://...`.

```bash
# Ingénieur : créer une règle (ici une alarme haute avec 30 s de temporisation)
curl -b jar -H 'Content-Type: application/json' -d '{"point_id":"<uuid>","kind":"high","threshold":28,
  "hysteresis":1,"delay_s":30,"severity":"critical","name":"Soufflage trop chaud",
  "notify":["email","webhook:https://hooks.example.org/x"]}' $B/alarm-rules
curl -b jar "$B/alarms?state=open"                # alarmes ouvertes (open, active, unacked, closed, all)
curl -b jar -X POST $B/alarms/<id>/ack            # acquittement (opérateur)
```

Les transitions sont aussi poussées sur le WebSocket (`{"type":"alarm","event":{...}}`). Configuration
des emails dans `.env` (`SMTP_HOST`, `ALARM_EMAIL_TO`...). Au-delà de 20 notifications par minute, elles
sont regroupées en un seul message.

## Historique

`sample` est une hypertable TimescaleDB (chunks de 1 jour) : compression après 7 jours, rétention de
`RETENTION_DAYS` jours (730 par défaut, 0 = illimitée). Un échantillon n'est enregistré que si la valeur
change de plus que la **deadband** du point ou si `max_interval_s` s'est écoulé (réglables par
`PATCH /points/{id}`). `GET /points/{id}/history` renvoie les échantillons bruts ou une agrégation
(`bucket=5m`, `1h`... ou `auto`).

## Frontend

`frontend/` contient les widgets des synoptiques (TypeScript, Vite, uPlot) :

- `trend` : courbes de 8 points au plus, de 15 min à 30 jours, en direct par WebSocket ;
- `alarm_list` : alarmes filtrées par chemin, acquittement, mise à jour en direct.

```bash
cd frontend
npm ci
npm test              # tests unitaires (vitest)
npm run dev           # page de démonstration : http://localhost:5173 (données simulées)
npm run build         # vérification des types puis build
```

Page de démonstration sur l'API réelle : `?api=1&points=<uuid>,<uuid>` (le proxy Vite envoie `/api`
vers `127.0.0.1:8000`).

## Variables d'environnement

| Variable | Rôle |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Base PostgreSQL (obligatoires) |
| `DATABASE_URL`, `REDIS_URL` | Construites par Compose ; à définir en développement local |
| `JWT_SECRET` | Signature des JWT (obligatoire, 32 caractères minimum) |
| `COOKIE_SECURE` | Cookies de session en `Secure` (défaut `true`) |
| `WRITE_TIMEOUT_S` | Attente de la réponse du collecteur à une écriture (défaut 10) |
| `LOG_LEVEL` | Niveau de logs (défaut `INFO`), format JSON sur stdout |
| `RETENTION_DAYS` | Rétention de l'historique en jours (défaut 730, 0 = illimitée) |
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
docker-compose.yml     db, redis, api, collector, alarms, simulator (profil sim)
config/                collector.yaml (réseau réel), collector.sim.yaml (simulateur)
simulator/             simulateur BACnet (bacpypes3), défauts injectables
backend/app/collector/ driver BACnet, découverte, polling, COV, écriture, historisation
backend/app/alarms/    moteur d'alarmes : règles, machine à états, notifications
backend/app/common/    configuration, logs JSON, enums partagés
backend/app/db/        modèles SQLAlchemy, session
backend/app/api/       application FastAPI : routes REST, WebSocket, audit
backend/app/auth/      mots de passe argon2id, JWT, rôles, CLI utilisateurs
backend/alembic/       migrations (0001 = schéma + rôles, 0002 = hypertable et compression)
frontend/              widgets des synoptiques (widget trend), page de démonstration
docs/QUESTIONS.md      décisions par défaut à confirmer
```
