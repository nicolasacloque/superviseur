# API REST

Généré depuis l'OpenAPI (`python -m app.api.apidoc`) : ne pas modifier à la main.
Documentation interactive sur `/api/docs`, schéma brut sur `/api/openapi.json`.

Authentification : cookie `access_token` (JWT, HttpOnly, 15 min) obtenu par
`POST /api/v1/auth/login`, renouvelé par `POST /api/v1/auth/refresh`.
Rôle minimal : `public` (aucun), `viewer` (tout compte
connecté), `operator`, `engineer`, `admin` ; un rôle inclut ceux qui le précèdent.
Réponses d'erreur : `{"detail": ...}` ; 401 non authentifié, 403 droits insuffisants,
422 requête invalide, 429 compte verrouillé.

## alarms

### `GET /api/v1/alarm-rules`

Rôle minimal : **engineer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `point` | query | string | null | non |
| `enabled` | query | boolean | null | non |

Réponses : 200 (liste de AlarmRuleOut)

### `POST /api/v1/alarm-rules`

Rôle minimal : **engineer**

Corps JSON : `AlarmRuleIn`
- `point_id` : string
- `name` (facultatif) : string | null
- `kind` : AlarmKind
- `threshold` (facultatif) : number | null
- `hysteresis` (facultatif) : number
- `delay_s` (facultatif) : integer
- `severity` : Severity
- `notify` (facultatif) : liste de string
- `enabled` (facultatif) : boolean

Réponses : 201 (AlarmRuleOut)

### `GET /api/v1/alarm-rules/{rule_id}`

Rôle minimal : **engineer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `rule_id` | path | string | oui |

Réponses : 200 (AlarmRuleOut)

### `PATCH /api/v1/alarm-rules/{rule_id}`

Rôle minimal : **engineer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `rule_id` | path | string | oui |

Corps JSON : `AlarmRuleUpdate`
- `name` (facultatif) : string | null
- `kind` (facultatif) : AlarmKind | null
- `threshold` (facultatif) : number | null
- `hysteresis` (facultatif) : number | null
- `delay_s` (facultatif) : integer | null
- `severity` (facultatif) : Severity | null
- `notify` (facultatif) : liste de string | null
- `enabled` (facultatif) : boolean | null

Réponses : 200 (AlarmRuleOut)

### `DELETE /api/v1/alarm-rules/{rule_id}`

Rôle minimal : **engineer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `rule_id` | path | string | oui |

Réponses : 204

### `GET /api/v1/alarms`

Rôle minimal : **viewer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `state` | query | string | non |
| `severity` | query | string | null | non |
| `path` | query | string | null | non |
| `point` | query | string | null | non |
| `limit` | query | integer | non |
| `offset` | query | integer | non |

Réponses : 200 (AlarmPage)

### `GET /api/v1/alarms/{alarm_id}`

Rôle minimal : **viewer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `alarm_id` | path | string | oui |

Réponses : 200 (AlarmOut)

### `POST /api/v1/alarms/{alarm_id}/ack`

Rôle minimal : **operator**

Acquitte une alarme : le moteur applique la transition de la machine à états.

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `alarm_id` | path | string | oui |

Réponses : 200 (AckResponse)

## audit

### `GET /api/v1/audit`

Rôle minimal : **admin**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `from` | query | string | null | non |
| `to` | query | string | null | non |
| `user` | query | string | null | non |
| `action` | query | string | null | non |
| `limit` | query | integer | non |

Réponses : 200 (liste de AuditOut)

## auth

### `POST /api/v1/auth/login`

Rôle minimal : **public**

Corps JSON : `LoginRequest`
- `login` : string
- `password` : string

Réponses : 200 (UserOut)

### `POST /api/v1/auth/logout`

Rôle minimal : **public**

Réponses : 204

### `GET /api/v1/auth/me`

Rôle minimal : **viewer**

Réponses : 200 (UserOut)

### `POST /api/v1/auth/refresh`

Rôle minimal : **public**

Réponses : 200 (UserOut)

## devices

### `GET /api/v1/devices`

Rôle minimal : **viewer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `network` | query | string | null | non |
| `online` | query | boolean | null | non |

Réponses : 200 (liste de DeviceOut)

### `GET /api/v1/devices/{device_id}`

Rôle minimal : **viewer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `device_id` | path | string | oui |

Réponses : 200 (DeviceOut)

### `GET /api/v1/networks`

Rôle minimal : **viewer**

Réponses : 200 (liste de NetworkOut)

## discovery

### `POST /api/v1/discovery/run`

Rôle minimal : **engineer**

Demande au collecteur de relancer la découverte ; le résultat apparaît dans `/devices`.

Réponses : 202 (DiscoveryRequested)

## health

### `GET /api/v1/health`

Rôle minimal : **public**

200 si tous les services répondent, 503 sinon.

Réponses : 200 (HealthStatus)

## points

### `GET /api/v1/points`

Rôle minimal : **viewer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `path` | query | string | null | non |
| `tag` | query | string | null | non |
| `device` | query | string | null | non |
| `q` | query | string | null | non |
| `writable` | query | boolean | null | non |
| `include_missing` | query | boolean | non |
| `limit` | query | integer | non |
| `offset` | query | integer | non |

Réponses : 200 (PointPage)

### `GET /api/v1/points/tree`

Rôle minimal : **viewer**

Dossiers (segments suivants du chemin) et points directement sous `path`.

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `path` | query | string | non |

Réponses : 200 (TreeOut)

### `GET /api/v1/points/{point_id}`

Rôle minimal : **viewer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `point_id` | path | string | oui |

Réponses : 200 (PointDetail)

### `PATCH /api/v1/points/{point_id}`

Rôle minimal : **engineer**

Règle un point : deadband, intervalles, bornes d'écriture, chemin logique, tags.

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `point_id` | path | string | oui |

Corps JSON : `PointUpdate`
- `path` (facultatif) : string | null
- `deadband` (facultatif) : number | null
- `max_interval_s` (facultatif) : integer | null
- `poll_interval_s` (facultatif) : integer | null
- `write_min` (facultatif) : number | null
- `write_max` (facultatif) : number | null
- `tags` (facultatif) : liste de string | null

Réponses : 200 (PointDetail)

### `GET /api/v1/points/{point_id}/history`

Rôle minimal : **viewer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `point_id` | path | string | oui |
| `from` | query | string | null | non |
| `to` | query | string | null | non |
| `bucket` | query | string | null | non |
| `max_points` | query | integer | non |

Réponses : 200 (HistoryOut)

## synoptics

### `GET /api/v1/synoptics`

Rôle minimal : **viewer**

Réponses : 200 (liste de SynopticSummary)

### `POST /api/v1/synoptics`

Rôle minimal : **engineer**

Corps JSON : `SynopticCreate`
- `slug` (facultatif) : string | null
- `doc` : object

Réponses : 201 (SynopticOut)

### `GET /api/v1/synoptics/{slug}`

Rôle minimal : **viewer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `slug` | path | string | oui |

Réponses : 200 (SynopticOut)

### `GET /api/v1/synoptics/{slug}/versions`

Rôle minimal : **engineer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `slug` | path | string | oui |

Réponses : 200 (liste de VersionOut)

### `GET /api/v1/synoptics/{slug}/versions/{version}`

Rôle minimal : **engineer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `slug` | path | string | oui |
| `version` | path | integer | oui |

Réponses : 200 (SynopticOut)

### `PUT /api/v1/synoptics/{synoptic_id}`

Rôle minimal : **engineer**

Enregistrer = créer une nouvelle version ; les précédentes restent restaurables.

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `synoptic_id` | path | string | oui |

Corps JSON : `SynopticSave`
- `doc` : object
- `base_version` (facultatif) : integer | null

Réponses : 200 (SynopticOut)

### `DELETE /api/v1/synoptics/{synoptic_id}`

Rôle minimal : **engineer**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `synoptic_id` | path | string | oui |

Réponses : 204

### `POST /api/v1/synoptics/{synoptic_id}/restore/{version}`

Rôle minimal : **engineer**

Restaurer recopie une ancienne version dans une nouvelle : l'historique reste intact.

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `synoptic_id` | path | string | oui |
| `version` | path | integer | oui |

Réponses : 200 (SynopticOut)

## users

### `GET /api/v1/roles`

Rôle minimal : **admin**

Réponses : 200 (liste de RoleOut)

### `GET /api/v1/users`

Rôle minimal : **admin**

Réponses : 200 (liste de UserAdminOut)

### `POST /api/v1/users`

Rôle minimal : **admin**

Corps JSON : `UserCreate`
- `login` : string
- `password` : string
- `role` : "viewer" | "operator" | "engineer" | "admin"

Réponses : 201 (UserAdminOut)

### `PATCH /api/v1/users/{user_id}`

Rôle minimal : **admin**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `user_id` | path | string | oui |

Corps JSON : `UserUpdate`
- `role` (facultatif) : "viewer" | "operator" | "engineer" | "admin" | null
- `active` (facultatif) : boolean | null
- `password` (facultatif) : string | null

Réponses : 200 (UserAdminOut)

### `DELETE /api/v1/users/{user_id}`

Rôle minimal : **admin**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `user_id` | path | string | oui |

Réponses : 204

### `POST /api/v1/users/{user_id}/unlock`

Rôle minimal : **admin**

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `user_id` | path | string | oui |

Réponses : 200 (UserAdminOut)

## write

### `POST /api/v1/points/{point_id}/write`

Rôle minimal : **operator**

Écrit `present-value` avec une priorité BACnet (défaut 8) ; `value: null` relâche.

| Paramètre | Où | Type | Requis |
|---|---|---|---|
| `point_id` | path | string | oui |

Corps JSON : `WriteRequest`
- `value` : number | null
- `priority` (facultatif) : integer

Réponses : 200 (WriteResponse)
