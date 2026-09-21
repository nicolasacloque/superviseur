#!/usr/bin/env bash
# Audit de sécurité de base d'une installation en service (section 15, Jalon 7).
#
# Usage : deploy/security-check.sh https://supervision.example.org [--docker]
# Environnement : SEC_LOGIN et SEC_PASSWORD (compte existant, n'importe quel rôle) pour contrôler les
# cookies de session. Sans eux, ce contrôle est ignoré. `--docker` vérifie aussi les ports publiés.
# Ajouter INSECURE=1 pour un certificat auto-signé.
set -uo pipefail

base="${1:?usage : $0 https://hote[:port] [--docker]}"
check_docker=false
[[ "${2:-}" == "--docker" ]] && check_docker=true
base="${base%/}"
curl_opts=(-sS --max-time 15)
[[ "${INSECURE:-}" == "1" ]] && curl_opts+=(-k)

failures=0
pass() { echo "  ok    $1"; }
fail() { echo "  ECHEC $1"; failures=$((failures + 1)); }
expect() { # expect "libellé" commande...   (la commande doit réussir)
  local label="$1"; shift
  if "$@" > /dev/null 2>&1; then pass "$label"; else fail "$label"; fi
}
status() { curl "${curl_opts[@]}" -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || echo 000; }
has_header() { grep -iq "^$1:" <<< "$2"; }

echo "== Redirection HTTP -> HTTPS"
hostport="${base#https://}"
host="${hostport%%:*}"
# Le port HTTP est celui de HTTP_PORT (défaut 80) ; HTTP_URL pour une adresse différente.
http_base="http://${host}${HTTP_PORT:+:$HTTP_PORT}"
[[ -n "${HTTP_URL:-}" ]] && http_base="$HTTP_URL"
redirect="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code} %{redirect_url}' "$http_base/" 2>/dev/null || echo "000 ")"
if [[ "$redirect" == 301\ https://* ]]; then pass "HTTP redirige vers HTTPS ($redirect)"; else fail "pas de redirection HTTPS ($redirect)"; fi

echo "== En-têtes de sécurité"
for path in / /api/v1/health /api/v1/points; do
  headers="$(curl "${curl_opts[@]}" -D - -o /dev/null "$base$path" 2>/dev/null | tr -d '\r')"
  for name in Strict-Transport-Security Content-Security-Policy X-Content-Type-Options X-Frame-Options Referrer-Policy Permissions-Policy; do
    if has_header "$name" "$headers"; then pass "$name sur $path"; else fail "$name absent sur $path"; fi
  done
  if grep -iq "^content-security-policy:.*script-src 'self'" <<< "$headers" \
     && ! grep -iq "^content-security-policy:.*script-src[^;]*unsafe" <<< "$headers"; then
    pass "CSP : scripts limités à 'self' sur $path"
  else
    fail "CSP : script-src trop permissif sur $path"
  fi
  if grep -iq '^server: nginx/' <<< "$headers"; then fail "la version de Nginx est divulguée sur $path"; else pass "version du serveur masquée sur $path"; fi
done

echo "== TLS"
old_tls="$(status --tlsv1.1 --tls-max 1.1 "$base/health")"
if [[ "$old_tls" == "000" ]]; then pass "TLS 1.0/1.1 refusé"; else fail "TLS 1.1 accepté ($old_tls)"; fi

echo "== Accès sans session refusé (401)"
uuid="00000000-0000-4000-8000-000000000000"
for route in \
  "GET /api/v1/auth/me" "GET /api/v1/points" "GET /api/v1/points/tree" "GET /api/v1/devices" \
  "GET /api/v1/networks" "GET /api/v1/alarms" "GET /api/v1/alarm-rules" "GET /api/v1/synoptics" \
  "GET /api/v1/users" "GET /api/v1/roles" "GET /api/v1/audit" "POST /api/v1/discovery/run" \
  "POST /api/v1/points/$uuid/write" "POST /api/v1/alarms/$uuid/ack" "PUT /api/v1/synoptics/$uuid" \
  "DELETE /api/v1/synoptics/$uuid" "DELETE /api/v1/users/$uuid"; do
  method="${route%% *}"; path="${route#* }"
  code="$(status -X "$method" -H 'Content-Type: application/json' -d '{}' "$base$path")"
  if [[ "$code" == "401" ]]; then pass "$route -> 401"; else fail "$route -> $code (attendu 401)"; fi
done
code="$(status -H 'Cookie: access_token=jeton-forge' "$base/api/v1/points")"
[[ "$code" == "401" ]] && pass "jeton forgé refusé" || fail "jeton forgé accepté ($code)"

echo "== Verrouillage après 5 échecs"
victim="sec-check-$RANDOM$RANDOM"
codes=""
for _ in 1 2 3 4 5 6; do
  codes+="$(status -X POST -H 'Content-Type: application/json' \
    -d "{\"login\":\"$victim\",\"password\":\"mauvais\"}" "$base/api/v1/auth/login") "
done
if [[ "$codes" == "401 401 401 401 401 429 " ]]; then pass "6e tentative refusée en 429 ($codes)"; else fail "verrouillage absent : $codes"; fi

echo "== Cookies de session"
if [[ -n "${SEC_LOGIN:-}" && -n "${SEC_PASSWORD:-}" ]]; then
  headers="$(curl "${curl_opts[@]}" -D - -o /dev/null -X POST -H 'Content-Type: application/json' \
    -d "{\"login\":\"$SEC_LOGIN\",\"password\":\"$SEC_PASSWORD\"}" "$base/api/v1/auth/login" | tr -d '\r')"
  cookies="$(grep -i '^set-cookie:' <<< "$headers")"
  if [[ "$(wc -l <<< "$cookies" | tr -d ' ')" -ge 2 ]]; then
    while IFS= read -r line; do
      name="$(sed -E 's/^[Ss]et-[Cc]ookie: ([^=]+)=.*/\1/' <<< "$line")"
      for flag in HttpOnly Secure SameSite=Strict; do
        if grep -iq "; *$flag" <<< "$line"; then pass "cookie $name : $flag"; else fail "cookie $name : $flag absent"; fi
      done
    done <<< "$cookies"
  else
    fail "la connexion n'a pas posé de cookies (identifiants SEC_LOGIN/SEC_PASSWORD valides ?)"
  fi
else
  echo "  (ignoré : SEC_LOGIN et SEC_PASSWORD non définis)"
fi

if [[ "$check_docker" == true ]]; then
  echo "== Ports publiés (docker)"
  ports="$(docker ps --format '{{.Names}} {{.Ports}}')"
  if grep -Eq '(0\.0\.0\.0|\[::\]):(5432|6379|8000)->' <<< "$ports"; then
    fail "base, Redis ou api publiés sur toutes les interfaces"
  else
    pass "base, Redis et api non exposés hors du serveur"
  fi
  if grep -Eq ':47808->' <<< "$ports"; then fail "le port BACnet 47808 est publié"; else pass "port BACnet non publié"; fi
fi

echo
if [[ $failures -eq 0 ]]; then echo "audit : tout est conforme"; else echo "audit : $failures contrôle(s) en échec"; fi
exit $((failures > 0))
