# Superviseur BACnet/IP

Superviseur GTB open source : découverte et lecture de points BACnet/IP, historisation, alarmes,
synoptiques HTML. Cahier des charges : [SPEC.md](SPEC.md).

**Avancement : Jalon 7 (production).** PostgreSQL + TimescaleDB (hypertable compressée), Redis, collecteur
BACnet/IP, simulateur de 2000 points, API REST + WebSocket avec authentification par rôles et verrouillage de
compte, moteur d'alarmes (email, webhook), application web (viewer, éditeur de synoptiques, administration),
Nginx en HTTPS, sauvegardes, démarrage systemd. Guides : [exploitation](docs/EXPLOITATION.md),
[synoptiques](docs/SYNOPTIQUES.md), [API](docs/API.md).

## Démarrage

```bash
cp .env.example .env          # mots de passe "change-me" à remplacer ; JWT_SECRET : openssl rand -hex 32
deploy/gen-cert.sh localhost  # certificat auto-signé pour un essai (voir docs/EXPLOITATION.md pour la production)
docker compose up -d --build --wait
docker compose exec api python -m app.auth.cli create-user admin --role admin   # 10 caractères minimum
curl -k https://localhost/health
```

Réponse attendue :

```json
{"status": "ok", "services": {"db": "ok", "redis": "ok"}}
```

L'application est sur https://localhost/ (certificat auto-signé : avertissement du navigateur). Nginx est le seul
point d'entrée : l'API n'est joignable que par lui. Documentation OpenAPI : https://localhost/api/docs.
Pour un serveur réel : [docs/EXPLOITATION.md](docs/EXPLOITATION.md) (certificat, réseau BACnet, sauvegarde, systemd).

## API

Les comptes se gèrent dans l'application (Administration) ou par `/users` ; le premier se crée par la ligne
de commande ci-dessus. Rôles : `viewer` (lecture), `operator` (+ consignes, acquittement), `engineer` (+
synoptiques, règles d'alarme, découverte), `admin` (+ utilisateurs, audit). La session repose sur deux cookies
`HttpOnly` / `Secure` / `SameSite=Strict`. Cinq échecs de connexion verrouillent le login 15 minutes (429).
Référence complète des routes et des rôles : [docs/API.md](docs/API.md) (générée depuis l'OpenAPI).

```bash
B=https://localhost/api/v1
curl -k -c jar -H 'Content-Type: application/json' -d '{"login":"admin","password":"..."}' $B/auth/login
curl -k -b jar "$B/points?q=temp&limit=5"                      # recherche paginée (path, tag, device, q)
curl -k -b jar "$B/points/<id>/history?bucket=1h"              # moyenne, min, max par tranche
curl -k -b jar "$B/points/<id>/history?bucket=auto&max_points=500"  # tranche choisie par le serveur
curl -k -b jar -X PATCH -H 'Content-Type: application/json' \
     -d '{"deadband":0.5,"max_interval_s":300}' $B/points/<id>   # réglage d'un point (ingénieur)
curl -k -b jar -H 'Content-Type: application/json' \
     -d '{"value":21.5,"priority":8}' $B/points/<id>/write   # "value": null relâche la priorité
```

Temps réel : `wss://localhost/api/v1/ws` (cookie de session). Messages client :
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

## Synoptiques

L'application web (`frontend/`, TypeScript + Vite) est compilée dans l'image Nginx et servie sur
`https://localhost/`. Connexion par identifiant et mot de passe, puis :

| Adresse | Page | Rôle minimal |
|---|---|---|
| `#/` | liste des synoptiques | viewer |
| `#/view/<slug>` | viewer, valeurs en direct par WebSocket | viewer (commandes : operator) |
| `#/edit/<slug>` · `#/edit/new` | éditeur | engineer |
| `#/admin` | utilisateurs et journal d'audit | admin |

**Widgets** : `value`, `label`, `gauge`, `indicator`, `switch` (commande binaire avec confirmation),
`setpoint` (consigne bornée, priorité configurable, relâchement), `trend`, `alarm_list`, `image`, `link`
(navigation vers un autre synoptique), `shape`. Chaque widget a des règles d'affichage (`rules`) écrites
dans un mini-langage d'expressions sûr (`value > 25 && status == "ok"`), évalué de la même façon côté
Python (validation à l'enregistrement) et côté navigateur ; les vecteurs de test sont partagés.

**Éditeur** : palette (clic ou glisser-déposer), sélection multiple et par rectangle, déplacement et
redimensionnement à la souris avec magnétisme à la grille (Alt le suspend), alignement et répartition,
copier / couper / coller / dupliquer, premier plan / arrière-plan, annuler / rétablir (100 étapes),
zoom, aperçu en direct, historique des versions avec restauration.
Raccourcis : Suppr, Ctrl+Z, Ctrl+Maj+Z ou Ctrl+Y, Ctrl+C / X / V / D / A, Ctrl+S, flèches (1 px, Maj : 10 px).

**Format** : JSON versionné (`"schema": 1`), une ligne par version dans `synoptic_version`. Chaque
enregistrement crée une version ; `PUT /synoptics/{id}` avec `base_version` répond 409 si quelqu'un a
enregistré entre-temps. Restaurer une version en crée une nouvelle (l'historique n'est jamais réécrit).

```bash
B=https://localhost/api/v1
curl -k -b jar $B/synoptics                           # liste
curl -k -b jar $B/synoptics/cta-1                     # dernière version
curl -k -b jar $B/synoptics/cta-1/versions            # historique
curl -k -b jar -X POST $B/synoptics/<id>/restore/2       # restauration → nouvelle version
```

**Développement**

```bash
cd frontend
npm ci
npm test              # tests unitaires (vitest, jsdom)
npm run dev           # http://localhost:5173 ; l'API est atteinte par proxy (API_TARGET, défaut http://127.0.0.1:8000)
npm run build         # vérification des types puis build (index.html = application, demo.html = démo widgets)
```

`demo.html` affiche les widgets `trend` et `alarm_list` avec des données simulées
(`?api=1&points=<uuid>,<uuid>` pour l'API réelle). Le développement contre la pile Docker se fait avec
`API_TARGET=https://localhost npm run dev` ; contre une API lancée à la main (`uvicorn`), avec le défaut.

**Tests d'acceptation (Playwright)** : ils s'exécutent contre la stack complète avec le simulateur et
vérifient la création de 10 widgets liés, les valeurs en direct, la commande du `switch` (valeur relue sur
l'appareil simulé), la restauration d'une version et l'affichage 1920×1080 / tablettes.

```bash
deploy/gen-cert.sh localhost
docker compose --profile sim up -d --build --wait     # avec COLLECTOR_CONFIG=/config/collector.sim.yaml dans .env
for spec in "e2e-engineer engineer" "e2e-operator operator" "e2e-viewer viewer" "e2e-admin admin"; do set -- $spec
  docker compose exec -T -e NEW_USER_PASSWORD=mot-de-passe-e2e-123 api python -m app.auth.cli create-user "$1" --role "$2"
done
cd e2e && npm ci && npx playwright install chromium && npx playwright test
```

Variables : `E2E_BASE_URL` (défaut `https://127.0.0.1`, certificat auto-signé accepté), `E2E_CHANNEL=chrome`
pour utiliser le Chrome installé, `E2E_PASSWORD`, `E2E_ENGINEER_LOGIN`, `E2E_OPERATOR_LOGIN`,
`E2E_VIEWER_LOGIN`, `E2E_ADMIN_LOGIN`. Les tests `production.spec.ts` vérifient aussi l'absence de violation de la CSP,
le verrouillage de compte et l'administration.

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
| `TLS_CERT_DIR`, `HTTP_PORT`, `HTTPS_PORT`, `HTTPS_PORT_SUFFIX` | Certificat et ports de Nginx |
| `LOGIN_MAX_FAILURES`, `LOGIN_LOCK_S` | Verrouillage de compte (défaut 5 échecs, 900 s) |
| `FRONTEND_DIR` | Dossier d'un frontend compilé que l'API sert elle-même (développement ; en production c'est Nginx) |

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
docker-compose.yml     db, redis, api, collector, alarms, nginx, simulator (profil sim)
deploy/                nginx (image, TLS, en-têtes), backup.sh, restore.sh, systemd, security-check.sh
config/                collector.yaml (réseau réel), collector.sim.yaml (simulateur)
simulator/             simulateur BACnet (bacpypes3), défauts injectables
backend/app/collector/ driver BACnet, découverte, polling, COV, écriture, historisation
backend/app/alarms/    moteur d'alarmes : règles, machine à états, notifications
backend/app/common/    configuration, logs JSON, enums partagés
backend/app/db/        modèles SQLAlchemy, session
backend/app/api/       application FastAPI : routes REST, WebSocket, audit
backend/app/auth/      mots de passe argon2id, JWT, rôles, CLI utilisateurs
backend/alembic/       migrations (0001 = schéma + rôles, 0002 = hypertable et compression)
frontend/              application web : viewer, éditeur, widgets (uPlot), démo des widgets
e2e/                   tests d'acceptation Playwright (Jalon 6)
docs/                  EXPLOITATION.md, SYNOPTIQUES.md, API.md (générée), QUESTIONS.md (décisions par défaut)
```
