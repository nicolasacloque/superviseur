#!/usr/bin/env bash
# Génère un certificat auto-signé pour un premier essai ou un réseau interne.
# En production, remplacer deploy/certs/fullchain.pem et privkey.pem par un certificat signé
# (autorité interne ou Let's Encrypt) : Nginx les relit au redémarrage (`docker compose restart nginx`).
#
# Usage : deploy/gen-cert.sh [nom-dhôte] [dossier]     (défaut : localhost, deploy/certs)
set -euo pipefail

host="${1:-localhost}"
dir="${2:-$(cd "$(dirname "$0")" && pwd)/certs}"
mkdir -p "$dir"

if [[ -e "$dir/privkey.pem" ]]; then
  echo "$dir/privkey.pem existe déjà : rien n'est écrasé." >&2
  exit 1
fi

san="DNS:${host},DNS:localhost,IP:127.0.0.1"
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -subj "/CN=${host}" -addext "subjectAltName=${san}" \
  -keyout "$dir/privkey.pem" -out "$dir/fullchain.pem" 2>/dev/null
chmod 600 "$dir/privkey.pem"
echo "certificat auto-signé pour ${host} créé dans ${dir} (valable 825 jours)"
