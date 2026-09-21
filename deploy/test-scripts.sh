#!/usr/bin/env bash
# Tests rapides (sans Docker) de la logique des scripts d'exploitation : rotation des sauvegardes,
# refus des sauvegardes vides ou illisibles, arguments de restore.sh, génération du certificat.
# Le vrai cycle sauvegarde -> restauration sur la base est testé dans la CI (job stack).
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
failures=0
ok() { echo "  ok    $1"; }
ko() { echo "  ECHEC $1"; failures=$((failures + 1)); }
check() { local label="$1"; shift; if "$@"; then ok "$label"; else ko "$label"; fi; }

# Faux « docker compose » : imite pg_dump et pg_restore --list du conteneur db.
cat > "$work/fake-compose" <<'FAKE'
#!/usr/bin/env bash
args="$*"
if [[ "$args" == *pg_dump* ]]; then
  [[ "${FAKE_EMPTY:-}" == 1 ]] || echo "PGDMP fake content"
  exit 0
fi
if [[ "$args" == *"pg_restore --list"* ]]; then
  cat > /dev/null
  exit "${FAKE_LIST_EXIT:-0}"
fi
exit 0
FAKE
chmod +x "$work/fake-compose"
export COMPOSE="$work/fake-compose"

echo "== backup.sh"
export BACKUP_DIR="$work/backups"
"$root/deploy/backup.sh" > "$work/out" 2>&1
check "crée une sauvegarde non vide" bash -c "test -s $BACKUP_DIR/supervisor-*.dump"
check "ne laisse aucun fichier partiel" bash -c "! ls -A $BACKUP_DIR | grep -q partial"
check "permissions restreintes (0600)" bash -c "[[ \$(stat -c %a $BACKUP_DIR/supervisor-*.dump 2>/dev/null || stat -f %Lp $BACKUP_DIR/supervisor-*.dump) == 600 ]]"

touch -t 202001010000 "$BACKUP_DIR/supervisor-20200101-000000.dump"   # très ancienne
touch -t 209901010000 "$BACKUP_DIR/supervisor-20990101-000000.dump"   # récente
"$root/deploy/backup.sh" > /dev/null 2>&1
check "supprime les sauvegardes de plus de 14 jours" test ! -e "$BACKUP_DIR/supervisor-20200101-000000.dump"
check "conserve les sauvegardes récentes" test -e "$BACKUP_DIR/supervisor-20990101-000000.dump"

before="$(ls "$BACKUP_DIR" | wc -l)"
FAKE_EMPTY=1 "$root/deploy/backup.sh" > /dev/null 2>&1
check "refuse une sauvegarde vide" test $? -ne 0
check "…sans rien ajouter ni effacer" test "$(ls -A "$BACKUP_DIR" | wc -l)" -eq "$before"
FAKE_LIST_EXIT=1 "$root/deploy/backup.sh" > /dev/null 2>&1
check "refuse une archive illisible" test $? -ne 0
check "…et ne fait pas la rotation dans ce cas" test -e "$BACKUP_DIR/supervisor-20990101-000000.dump"

echo "== restore.sh"
"$root/deploy/restore.sh" > /dev/null 2>&1
check "exige un fichier de sauvegarde" test $? -eq 2
"$root/deploy/restore.sh" --yes "$work/inexistant.dump" > /dev/null 2>&1
check "refuse un fichier absent" test $? -eq 2
echo -n "non" | "$root/deploy/restore.sh" "$(ls "$BACKUP_DIR"/supervisor-2*.dump | head -1)" > /dev/null 2>&1
check "abandonne sans confirmation explicite" test $? -ne 0
FAKE_LIST_EXIT=1 "$root/deploy/restore.sh" --yes "$(ls "$BACKUP_DIR"/supervisor-2*.dump | head -1)" > /dev/null 2>&1
check "refuse une archive illisible avant de rien arrêter" test $? -ne 0

echo "== gen-cert.sh"
"$root/deploy/gen-cert.sh" essai.local "$work/certs" > /dev/null 2>&1
check "génère clé et certificat" bash -c "test -s $work/certs/privkey.pem && test -s $work/certs/fullchain.pem"
"$root/deploy/gen-cert.sh" essai.local "$work/certs" > /dev/null 2>&1
check "n'écrase jamais une clé existante" test $? -ne 0

echo
if [[ $failures -eq 0 ]]; then echo "scripts : tout est conforme"; else echo "scripts : $failures échec(s)"; fi
exit $((failures > 0))
