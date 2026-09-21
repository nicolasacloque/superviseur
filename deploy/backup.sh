#!/usr/bin/env bash
# Sauvegarde quotidienne de la base (pg_dump au format « custom », compressé).
#
# Usage : deploy/backup.sh                (depuis n'importe où ; agit sur le dépôt qui contient ce script)
# Variables : BACKUP_DIR (défaut /var/backups/supervisor), KEEP_DAYS (défaut 14), COMPOSE (défaut « docker compose »)
#
# La sauvegarde est vérifiée (l'archive doit se relire) avant d'être conservée, et les
# sauvegardes de plus de KEEP_DAYS jours sont supprimées seulement après un succès.
set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-/var/backups/supervisor}"
KEEP_DAYS="${KEEP_DAYS:-14}"
COMPOSE="${COMPOSE:-docker compose}"

umask 077
mkdir -p "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
partial="$BACKUP_DIR/.supervisor-$stamp.dump.partial"
final="$BACKUP_DIR/supervisor-$stamp.dump"
trap 'rm -f "$partial"' EXIT

# pg_dump s'exécute dans le conteneur db : il en connaît l'utilisateur et la base.
# shellcheck disable=SC2016  # les variables sont celles du conteneur, pas de cet hôte
$COMPOSE exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner' > "$partial"

if [[ ! -s "$partial" ]]; then
  echo "sauvegarde vide : abandon" >&2
  exit 1
fi
# Relecture de l'archive : détecte une sauvegarde tronquée ou corrompue.
if ! $COMPOSE exec -T db pg_restore --list < "$partial" > /dev/null; then
  echo "l'archive produite est illisible : abandon" >&2
  exit 1
fi

mv "$partial" "$final"
# Rotation : seulement après une sauvegarde réussie, pour ne jamais rester sans copie.
find "$BACKUP_DIR" -maxdepth 1 -name 'supervisor-*.dump' -mtime "+$KEEP_DAYS" -delete
echo "$final ($(wc -c < "$final" | tr -d ' ') octets)"
