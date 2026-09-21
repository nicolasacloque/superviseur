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
21. **Historique** : agrégation par `date_bin` (PostgreSQL 14+) ; le Jalon 4 utilise `time_bucket`
    quand TimescaleDB est présent (voir 23). Maximum 20 000 lignes par réponse.
22. **Pas encore fait** : `POST /discovery/run` (nécessite un canal de commande vers le collecteur) et
    `docs/API.md` (généré depuis OpenAPI en fin de projet ; la doc interactive est sur `/api/docs`).

## Jalon 4

23. **Hypertable** : `sample` en chunks de 1 jour, compression après 7 jours segmentée par point
    (`compress_segmentby = point_id`, tri `ts DESC`), rétention 730 jours par défaut ; `RETENTION_DAYS`
    l'ajuste au démarrage de l'API (0 = conservation illimitée). Aucun index sur `ts` seul : la clé
    primaire `(point_id, ts)` sert toutes les requêtes. La migration 0002 n'est pas réversible vers une
    table ordinaire : son downgrade retire les politiques et décompresse, la table est supprimée par
    le downgrade de 0001. `time_bucket` et `date_bin` partent de la même origine (2000-01-01) et
    donnent les mêmes tranches ; la première sert avec TimescaleDB, la seconde en repli.
24. **Pas d'agrégat continu** : la requête « 30 jours d'un point » (2880 échantillons) répond en quelques
    dizaines de millisecondes sur les chunks compressés ; un agrégat continu n'apporterait que de la
    complexité. À reconsidérer si les périodes affichées dépassent l'année.
25. **`bucket=auto`** : le serveur choisit la plus petite tranche ronde (5 s, 10 s, 30 s, 1 m, 5 m, 15 m,
    30 m, 1 h, 3 h, 6 h, 12 h, 1 j, puis multiples de jours) qui donne au plus `max_points` tranches
    (500 par défaut) ; en dessous d'une seconde par tranche, il renvoie les échantillons bruts.
26. **Réglage des points** : `PATCH /points/{id}` (ingénieur) règle `deadband`, `max_interval_s`,
    `poll_interval_s`, `write_min`/`write_max`, `path` et `tags` ; `name` et `description` restent ceux
    du contrôleur (une redécouverte les réécrirait). Chaque changement est audité (avant / après) et le
    collecteur recharge deadband et intervalles sans redémarrer (canal Redis `point.config`).
27. **Widget `trend`** : première brique du dossier `frontend/` (TypeScript + Vite + uPlot). Il ne
    dépend que de deux interfaces (`HistoryApi`, `LiveApi`) et d'un moteur de tracé injectable : les
    tests n'ont besoin ni de navigateur ni d'API. Séries limitées à 8, un seul axe, couleurs de la
    palette de référence dans un ordre fixe, légende avec dernière valeur, infobulle, vue tableau,
    rendu limité à 10 par seconde. Les valeurs temps réel sont regroupées (1000 points par fenêtre) pour
    borner la mémoire sur 7 et 30 jours. Les courbes s'ajoutent à un synoptique au Jalon 6.
28. **Mesures de performance** : le job CI `performance` publie chaque mesure en annotation
    (insertion, compression, durées des requêtes 30 jours, débit d'écriture).

## Jalon 5

29. **Sémantique des règles** : `high` / `low` : `threshold` est le seuil (strict : égal ne déclenche pas)
    et `hysteresis` l'écart de retour (la condition retombe à `seuil - hystérésis` pour `high`,
    `seuil + hystérésis` pour `low`). `state` : `threshold` est la valeur attendue (1 pour un défaut
    binaire, l'index pour un multi-état). `stale` : `threshold` est la durée maximale sans nouvelle valeur,
    en secondes (une lecture `comm_lost` ne compte pas comme nouvelle valeur). `comm_lost` : le device du
    point est hors ligne. `bacnet_event` : le contrôleur a émis une Event Notification vers un état autre
    que `normal` pour l'objet. Les deux derniers n'ont pas de seuil. Une mesure sans valeur (point en
    `comm_lost`) ne déclenche ni n'efface une règle de valeur : la condition reste ce qu'elle était.
30. **Une ligne `alarm_event` par occurrence**, mise à jour à chaque transition (états, `raised_at`,
    `acked_at`/`acked_by`, `cleared_at`, `raised_value`). Un index unique partiel garantit une seule alarme
    ouverte par règle. L'état `pending` n'est pas enregistré : tant que la temporisation n'est pas écoulée,
    aucune ligne n'existe (elle repart de zéro après un redémarrage du moteur). Si la condition revient
    avant l'acquittement d'une alarme terminée, c'est la même alarme qui redevient active.
31. **Notifications** : une par changement d'état persistant (déclenchée, terminée, acquittée, retour à la
    normale) et par destinataire. Canaux d'une règle (`notify`) : `email` (destinataires par défaut
    `ALARM_EMAIL_TO`), `email:adresse`, `webhook:https://...`. Au-delà de 20 notifications par minute et par
    destinataire, les suivantes partent en un seul message récapitulatif à la fin de la fenêtre. Un envoi en
    échec est retenté 3 fois (1 s, 2 s) puis abandonné et journalisé : il n'est pas rejoué après un
    redémarrage. Les webhooks sont appelés depuis le serveur : seul un ingénieur peut définir l'URL, mais
    aucune liste blanche d'hôtes n'est appliquée.
32. **Un seul écrivain** : le moteur est le seul à écrire `alarm_event`. Les acquittements passent par le
    stream `cmd.alarm` (comme les écritures de consigne) : l'API attend le résultat (409 si l'état ne s'y
    prête pas, 504 si le moteur ne répond pas). Le moteur revérifie le rôle et ignore une commande de
    plus de 30 s.
33. **Règle désactivée ou supprimée** : désactiver une règle qui a une alarme ouverte la clôt (état
    `normal`). Supprimer une règle est refusé (409) tant qu'une alarme est ouverte ; l'historique clos
    est supprimé avec la règle (les modifications restent dans `audit_log`).
34. **Reprise** : au démarrage et à chaque rechargement (règle modifiée, ou toutes les minutes), le moteur
    reprend les alarmes ouvertes et rattrape l'état courant avec `point_latest` et l'état des devices.
    Après un redémarrage, une alarme dont la condition persiste ne produit aucune nouvelle notification.
35. **Réseau du service `alarms`** : il est sur `front` en plus de `back`, car ses notifications (SMTP,
    webhooks) sortent du serveur alors que `back` est interne. Il n'a aucun port publié.
36. **Événements BACnet** : le collecteur reçoit les Event Notification (confirmées ou non) et les publie
    sur `bacnet.event`. Pour qu'un contrôleur les envoie, le superviseur (instance du fichier
    `collector.yaml`) doit figurer dans les destinataires de sa classe de notification. Sans cela, seules les
    quatre autres formes de règles sont disponibles pour ce contrôleur.
37. **Règles : PATCH et non PUT** ; un ingénieur uniquement (comme dans le CDC). La modification est fusionnée
    avec l'existant puis revalidée en entier. `point_id` ne se modifie pas : créer une autre règle.
