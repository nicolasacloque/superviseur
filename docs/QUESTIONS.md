# Questions ouvertes

Décisions prises par défaut (option la plus simple) faute de précision dans SPEC.md.
À confirmer ou corriger.

## Jalon 1

1. **Hypertable `sample`** : créée comme table ordinaire dans la migration 0001. La conversion en
   hypertable (chunk 1 jour, compression à 7 jours, rétention) est laissée au Jalon 4, comme
   l'indique la section 15. Valeur nullable pour pouvoir enregistrer un état `comm_lost` sans mesure.
2. **Colonnes ajoutées** aux tables de la section 5.1, déduites du texte du CDC :
   `point.missing` (6.3), `point.deadband` et `point.max_interval_s` (5.2),
   `point.write_min` / `write_max` (11), `point.state_text` (6.3),
   `alarm_rule.hysteresis` et `alarm_rule.notify` (9.1).
3. **`GET /health`** : servi à la fois sous `/api/v1/health` (section 8) et à la racine `/health`
   (critère d'acceptation du Jalon 1, healthchecks Docker). Répond 503 si la base ou Redis est injoignable.
4. **Migrations** : lancées au démarrage du conteneur `api` (`alembic upgrade head` avant uvicorn)
   plutôt que dans un service one-shot. À revoir si l'API est un jour répliquée.
5. **`acked_by`** : chaîne (login), comme dans le diagramme ER, et non clé étrangère vers `user`,
   pour conserver l'historique si un utilisateur est supprimé.
6. **Ports** : l'API est publiée sur `127.0.0.1:8000` uniquement, en attendant Nginx + TLS (Jalon 7).
7. **Services absents** : `collector` (Jalon 2), `alarms` (Jalon 5) et `nginx` (Jalon 7) ne sont
   pas encore dans `docker-compose.yml`.

## Jalon 2

8. **Simulateur sans broadcast** : les 10 devices simulés partagent une seule machine, chacun sur son
   propre port UDP (47809-47818). Le Who-Is broadcast ne les atteint pas ; le collecteur envoie donc
   aussi un Who-Is en unicast vers `discovery.targets` (utile aussi derrière un routeur sans BBMD).
9. **Ports db/redis** : `db` et `redis` sont publiés sur `127.0.0.1` (via un réseau `hostpub`) parce que
   le collecteur est en `network_mode: host` et ne voit pas les noms de service Docker. Ils ne sont
   jamais exposés hors du serveur.
10. **Vendor ID** : le collecteur et le simulateur s'annoncent avec l'identifiant 999 (valeur réservée aux
    exemples bacpypes3). Un identifiant fabricant réel n'est pas requis pour un usage interne.
11. **COV** : notifications non confirmées par défaut (`cov.confirmed: false`) pour limiter le trafic ;
    la durée d'abonnement (`lifetime_s`) est renouvelée `renew_before_s` avant l'expiration.
12. **Cycle et hors ligne** : un « cycle » est une tentative de lecture d'un device (au plus toutes les
    `default_interval_s`). Trois cycles consécutifs sans réponse (`offline_after_cycles`) passent le
    device hors ligne et tous ses points en `comm_lost`. Un device sous COV reçoit une lecture-sonde
    par cycle pour détecter sa perte, faute de notifications quand rien ne change.
13. **Priorité d'écriture** : une priorité absente vaut 8 ; hors de 1..16, la commande est refusée.
    Toute tentative (acceptée ou refusée) est tracée dans `audit_log`. Le rôle est revérifié dans le
    collecteur (operator, engineer ou admin) en plus du contrôle que fera l'API.
14. **Historique** : la deadband et `max_interval_s` sont stockées par point (défaut 0 et 900 s). Aucun
    réglage n'existe encore dans l'interface : à modifier en base jusqu'au Jalon 3.
15. **Écritures BACnet sur objets non commandables** : les objets sans priority-array (ex. multi-state
    value du simulateur) reçoivent l'écriture directement, la priorité étant ignorée par le device.

## Jalon 3

16. **Création des utilisateurs** : pas d'API `/users` ni `/roles` à ce stade (prévue avec l'administration).
    Le premier compte se crée en ligne de commande :
    `docker compose exec api python -m app.auth.cli create-user admin --role admin`
    (mot de passe demandé, ou variable `NEW_USER_PASSWORD`). Mot de passe : 10 caractères minimum.
17. **Session** : deux cookies `HttpOnly` + `SameSite=Strict` (+ `Secure` par défaut) : `access_token`
    (15 min, envoyé à `/api`) et `refresh_token` (7 jours, envoyé seulement à `/api/v1/auth`).
    `POST /auth/refresh` renouvelle les deux. Les JWT sont sans état : la déconnexion efface les cookies
    mais ne révoque pas un jeton déjà copié (il expire en 15 min). L'utilisateur est rechargé en base à
    chaque requête : un compte désactivé perd l'accès immédiatement.
18. **Verrouillage après 5 échecs** : prévu au Jalon 7 avec le reste du durcissement ; les échecs de
    connexion sont déjà tracés dans `audit_log`.
19. **Écriture** : `POST /points/{id}/write` envoie la commande dans le stream `cmd.write` et attend le
    résultat du collecteur (10 s, `WRITE_TIMEOUT_S`). Codes : 403 rôle, 404 point, 409 non inscriptible,
    422 hors bornes, 502 refus du contrôleur, 504 collecteur muet. Une commande porte `issued_at` et le
    collecteur refuse celles de plus de 30 s : une écriture ayant expiré côté API ne s'exécute pas
    plus tard. L'API trace ses propres refus, le collecteur trace les tentatives qui le concernent.
20. **WebSocket** : authentifié par le cookie d'accès ; la connexion est fermée avec le code 4401 quand
    le jeton expire (le client renouvelle puis se reconnecte). Ping serveur toutes les 30 s (uvicorn) et
    `{"action":"ping"}` -> `{"type":"pong"}` côté application. Les messages d'alarme (`alarm.event`) sont
    déjà relayés ; le moteur d'alarmes arrive au Jalon 5.
21. **Historique** : agrégation par `date_bin` (PostgreSQL 14+), donc sans TimescaleDB ; le Jalon 4
    passera à `time_bucket` et aux agrégats continus. Maximum 20 000 lignes par réponse.
22. **Pas encore fait** : `POST /discovery/run` (nécessite un canal de commande vers le collecteur) et
    `docs/API.md` (généré depuis OpenAPI en fin de projet ; la doc interactive est sur `/api/docs`).
