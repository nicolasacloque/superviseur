#!/usr/bin/env bash
# Restaure une sauvegarde faite par backup.sh. DESTRUCTIF : la base actuelle est supprimée.
#
# Usage : deploy/restore.sh [--yes] /var/backups/supervisor/supervisor-AAAAMMJJ-HHMMSS.dump
#
# Étapes : arrêt des services qui écrivent, recréation de la base, restauration selon la procédure
# TimescaleDB (timescaledb_pre_restore / post_restore), redémarrage, contrôle de cohérence.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="${COMPOSE:-docker compose}"
assume_yes=false
if [[ "${1:-}" == "--yes" ]]; then
  assume_yes=true
  shift
fi
dump="${1:-}"
if [[ -z "$dump" || ! -s "$dump" ]]; then
  echo "usage : $0 [--yes] fichier.dump" >&2
  exit 2
fi

if [[ "$assume_yes" != true ]]; then
  read -r -p "Remplacer la base actuelle par $dump ? (tapez oui) " answer
  [[ "$answer" == "oui" ]] || { echo "abandon"; exit 1; }
fi

# Refuse une archive illisible avant de toucher à quoi que ce soit.
$COMPOSE exec -T db pg_restore --list < "$dump" > /dev/null || { echo "archive illisible" >&2; exit 1; }

echo "arrêt des services…"
$COMPOSE stop nginx api alarms collector

# shellcheck disable=SC2016  # variables du conteneur
{
  echo "recréation de la base…"
  $COMPOSE exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" \
    -c "CREATE DATABASE \"$POSTGRES_DB\""'
  $COMPOSE exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
    -c "CREATE EXTENSION IF NOT EXISTS timescaledb" -c "SELECT timescaledb_pre_restore()"'

  echo "restauration…"
  # Sans --exit-on-error : pg_restore signale un avertissement bénin (extension déjà créée) ;
  # la cohérence est vérifiée explicitement ci-dessous.
  $COMPOSE exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' < "$dump" \
    || echo "pg_restore a signalé des avertissements (voir ci-dessus) : contrôle de cohérence…"

  $COMPOSE exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
    -c "SELECT timescaledb_post_restore()"'

  echo "contrôle de cohérence…"
  $COMPOSE exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -tA -c "
    SELECT CASE WHEN
      (SELECT count(*) FROM alembic_version) = 1 AND
      (SELECT count(*) FROM role) >= 4 AND
      EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = '"'"'sample'"'"')
    THEN '"'"'ok'"'"' ELSE '"'"'incoherent'"'"' END"' | grep -qx ok
}

echo "redémarrage des services…"
$COMPOSE up -d --wait --wait-timeout 180
echo "restauration terminée : $dump"
