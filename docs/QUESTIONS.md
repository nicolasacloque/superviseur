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
38. **Widget `alarm_list`** (`frontend/`) : tableau des alarmes filtré par préfixe de chemin et par groupe
    d'états (ouvertes, actives, à acquitter, closes, toutes), 200 lignes au plus. Les alarmes à acquitter
    passent en premier, puis la gravité, puis la récence. La sévérité n'est jamais portée par la couleur
    seule : forme (losange, triangle, cercle) et libellé l'accompagnent ; les deux couleurs d'état
    (critique, avertissement) sont fixes et ne suivent pas le thème. Le bouton d'acquittement peut être
    retiré de la configuration ; un refus de droits est expliqué et la liste est resynchronisée. La liste
    se met à jour par les événements `alarm` du WebSocket et se recharge à chaque reconnexion pour
    rattraper ce qui a été manqué pendant une coupure.
39. **Dépendances d'exécution** : `httpx` (webhooks) est une dépendance du service, pas seulement des tests.
    Un job CI installe le paquet seul dans un environnement vierge et importe l'API et les services : l'image
    Docker n'a pas les outils de test, et une dépendance manquante n'apparaît sinon qu'au démarrage.

## Jalon 6

40. **Format du synoptique** : JSON `schema: 1` (`name`, `canvas`, `widgets`, `links`). Les propriétés
    propres à un type de widget sont acceptées telles quelles (l'éditeur les génère depuis le schéma du
    widget) ; le serveur vérifie les types, les identifiants uniques, les coordonnées finies, au plus
    500 widgets et 6 Mo par document. Les valeurs de style sont limitées à une liste de propriétés CSS
    et refusent `url(`, `expression`, `@import`, `javascript:` et les caractères `; { } < > \` : un synoptique
    ne peut ni déclencher de requête ni injecter de CSS.
41. **Image de fond et widget `image`** : intégrées au JSON en `data:` base64 (PNG, JPEG, WebP, SVG),
    5 Mo au plus par image. Le CDC ne prévoit pas de stockage de fichiers ; c'est l'option la plus simple
    et elle rend chaque version autonome. Contrepartie : un document avec images pèse vite plusieurs Mo.
42. **Versions** : chaque enregistrement crée une version ; `base_version` obsolète → 409 (l'éditeur
    affiche le conflit sans écraser). Restaurer la version N **crée** une nouvelle version identique à N,
    l'historique n'est jamais réécrit. Créer, modifier, restaurer et supprimer sont réservés aux
    ingénieurs et inscrits dans `audit_log` ; la liste des synoptiques et leur dernière version sont lisibles
    par tout compte connecté, l'historique des versions par les ingénieurs seulement.
43. **Slug** : dérivé du nom (minuscules, chiffres, tirets) et suffixé `-2`, `-3`… s'il est pris ; il ne
    change pas quand on renomme le synoptique, pour que les liens et les favoris restent valables.
44. **Navigation** : un widget `link` ou un lien posé sur un autre widget (`links[]`) désigne le slug du
    synoptique cible. Un lien vers un synoptique supprimé mène à la page « introuvable » ; le serveur ne
    vérifie pas l'existence de la cible à l'enregistrement (elle peut être créée ensuite).
45. **Expressions de règles** : grammaire fermée (identifiants `value` et `status`, littéraux, opérateurs
    `&& || ! == != < <= > >= + - * / %`, ou `and or not`). Un analyseur descendant récursif existe en
    Python et en TypeScript, pas d'`eval` ; les deux lisent le même fichier de cas
    (`frontend/src/synoptic/expression_cases.json`) pour rester équivalents. Une règle invalide est refusée
    à l'enregistrement ; à l'affichage, une règle qui échoue est ignorée.
46. **Viewer** : le canevas est mis à l'échelle pour tenir entièrement dans la fenêtre, sans déformation
    (bandes sombres sur les rapports d'aspect différents, par exemple une tablette en portrait). Les
    mises à jour temps réel sont regroupées (100 ms au plus entre deux rafraîchissements). Tant que le
    WebSocket est coupé, le synoptique est grisé ; les valeurs reviennent à la reconnexion. Les commandes (`switch`, `setpoint`) demandent une confirmation et exigent le rôle
    operator ; l'API revérifie le rôle et les bornes.
47. **Application web** : le routage se fait par fragment (`#/view/<slug>`), sans réécriture d'URL côté
    serveur. En production c'est Nginx qui sert les fichiers (voir 54) ; l'API sait aussi monter un frontend
    compilé (`FRONTEND_DIR`, vide par défaut) pour le développement et les tests sans Nginx.
48. **Arborescence des points** : `GET /points/tree?path=` renvoie les dossiers du niveau (avec leur
    nombre de points) et les points de ce niveau ; les points sans chemin sont regroupés sous
    « (sans chemin) ». Le sélecteur de l'éditeur s'en sert, ou bascule sur la recherche si l'on tape.
49. **Tests Playwright** : exécutés dans le job CI `stack`, sur la stack Docker réelle et le simulateur,
    en série (un seul simulateur partagé). Chaque scénario crée ses propres synoptiques et les supprime.
    Le rapport `github` annote les échecs ; captures et traces sont déposées en artefact.

## Jalon 7

50. **Verrouillage de compte** : compteurs dans Redis (pas de migration, expiration native). 5 échecs en
    15 minutes verrouillent le login 15 minutes ; pendant ce temps le bon mot de passe est refusé aussi
    (429 avec `Retry-After`). La clé est le login saisi en minuscules, qu'il existe ou non : un compte
    inexistant se verrouille comme un vrai, on ne révèle rien. Une connexion réussie remet le compteur à zéro.
    Contrepartie assumée : quiconque connaît un login peut le bloquer 15 minutes ; un administrateur peut lever
    le verrou (Administration, ou `redis-cli del auth.lock.<login>`), et Nginx limite chaque adresse à
    60 tentatives par minute. Si Redis est injoignable, la protection est suspendue et l'erreur journalisée
    plutôt que d'interdire toute connexion (sans Redis, le temps réel est de toute façon à l'arrêt).
51. **Sessions et mot de passe** : les jetons portent une empreinte du mot de passe courant ; changer le mot
    de passe (par un admin ou `create-user --update`) invalide toutes les sessions ouvertes du compte, y
    compris le renouvellement. Désactiver un compte coupe déjà l'accès à la requête suivante (rôle relu en base).
52. **Utilisateurs (`/users`, `/roles`)** : réservés aux admin. Login de 1 à 64 caractères (lettres,
    chiffres, `.` `_` `@` `-`), mot de passe de 10 à 256 caractères. Un admin ne peut ni se rétrograder, ni se
    désactiver, ni se supprimer ; il reste toujours un administrateur actif. Suppression autorisée (l'audit
    conserve la trace, l'auteur devient anonyme). Pas de courriel ni de politique de complexité : la longueur
    est le seul critère. Pas d'authentification à deux facteurs (hors périmètre v1).
53. **`POST /discovery/run`** : demande asynchrone (202) publiée sur Redis, exécutée aussitôt par le collecteur
    (l'attente d'intervalle est interrompue). 503 si aucun collecteur n'écoute. Le résultat se lit dans
    `/devices`. Une relance pendant une découverte en cours en déclenche une seconde à la suite.
54. **Nginx** : seul point d'entrée web (80 → 301 vers 443). Il sert le frontend compilé et relaie `/api` et le
    WebSocket ; l'API n'a plus de port publié (elle n'est plus que sur le réseau `back`). Pour développer
    contre la pile Docker : `API_TARGET=https://localhost npm run dev`. L'adresse de l'API est résolue à chaque
    requête (résolveur Docker) : Nginx démarre même si l'API est arrêtée et la retrouve après un redémarrage.
    En-têtes : HSTS un an (sans `includeSubDomains` ni preload, plus prudent sur un nom partagé), CSP stricte,
    `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP/CORP.
    La CSP garde `style-src 'unsafe-inline'` : les widgets posent des styles calculés (un test Playwright
    le confirme : sans, l'interface casse) ; `script-src` reste `'self'` sans exception. TLS 1.2 minimum.
    L'API fait confiance à `X-Forwarded-For` (`--forwarded-allow-ips '*'`) parce que seul Nginx l'atteint, ce
    qui donne la vraie adresse cliente à l'audit.
55. **Certificats** : `deploy/gen-cert.sh` produit un certificat auto-signé pour les essais. En production,
    l'exploitant fournit `fullchain.pem` et `privkey.pem` (autorité interne ou Let's Encrypt) et redémarre
    Nginx après chaque renouvellement ; l'automatisation d'ACME n'est pas incluse.
56. **Sauvegarde** : `pg_dump -Fc` quotidien à 02:30 (minuteur systemd, rattrapé si le serveur était éteint) dans
    `/var/backups/supervisor`, rétention 14 jours, archive relue avant d'être conservée, rotation seulement après
    succès. La copie hors du serveur reste à la charge de l'exploitant, comme `.env`, `config/` et les
    certificats (hors base). Redis n'est pas sauvegardé (bus temps réel, rien de durable). La restauration
    (`deploy/restore.sh`) est destructive, demande confirmation, suit la procédure TimescaleDB et contrôle la
    cohérence ; elle tolère les avertissements bénins de `pg_restore` (extension déjà présente) et échoue si le
    contrôle final n'est pas bon. Le cycle sauvegarde → sinistre → restauration est rejoué en CI.
57. **Démarrage** : `supervisor.service` est un service `oneshot` qui lance `docker compose up -d --wait` ;
    la reprise après coupure repose surtout sur `restart: unless-stopped` (Docker relance les conteneurs sans
    attendre systemd). Un démarrage désordonné se corrige seul : api, collecteur et moteur d'alarmes échouent
    tant que la base n'est pas prête et sont relancés. La CI redémarre le moteur Docker et vérifie la reprise
    (API joignable en moins de 150 s, données et comptes intacts, acquisition reprise).
58. **Documentation de l'API** : `docs/API.md` est générée (`python -m app.api.apidoc`) et un test échoue si
    elle est périmée. Le rôle affiché est lu dans les dépendances des routes ; la route d'écriture, qui
    contrôle son rôle dans le handler pour journaliser les refus, le déclare avec `checked_in_handler`. Un test
    liste toutes les routes et exige une authentification partout, sauf connexion, déconnexion, renouvellement
    et `/health`.
59. **Journal d'audit** : le filtre `action` de `GET /audit` est un préfixe (`user.` pour tous les événements de
    comptes) ; une valeur exacte reste valable.
60. **Non couvert** (hors périmètre v1 ou à décider) : redis sans mot de passe (il n'écoute que sur 127.0.0.1 et
    le réseau interne Docker ; ajouter `requirepass` si d'autres services tournent sur le serveur), alerte
    automatique sur échec de sauvegarde (le journal systemd la conserve), haute disponibilité et réplication,
    envoi des logs vers un collecteur central, liste blanche d'adresses IP.
