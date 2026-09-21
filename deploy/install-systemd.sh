#!/usr/bin/env bash
# Installe et active les unités systemd (démarrage au boot + sauvegarde quotidienne).
# À lancer en root depuis le serveur : sudo deploy/install-systemd.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "à lancer en root (sudo)" >&2
  exit 1
fi
root="$(cd "$(dirname "$0")/.." && pwd)"
target="${SYSTEMD_DIR:-/etc/systemd/system}"

for unit in supervisor.service supervisor-backup.service supervisor-backup.timer; do
  # Les unités livrées supposent /opt/supervisor : on les adapte à l'emplacement réel du dépôt.
  sed "s#/opt/supervisor#${root}#g" "$root/deploy/$unit" > "$target/$unit"
  chmod 644 "$target/$unit"
done
mkdir -p /var/backups/supervisor
chmod 700 /var/backups/supervisor

systemctl daemon-reload
systemctl enable supervisor.service supervisor-backup.timer
echo "unités installées dans $target ; démarrage : systemctl start supervisor.service supervisor-backup.timer"
