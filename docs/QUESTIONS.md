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
