#!/usr/bin/env bash
# Pass 5 — creates 3 disposable test users in the LOCAL Keycloak realm
# (nextjs-kc, imported from docker/realms/realm-export.json) via the admin
# REST API:
#   - publisher-a  (realm role: publisher)
#   - publisher-b  (realm role: publisher)
#   - user-c       (no realm role — used for KC-INT-06/07 negative tests)
#
# This is a LOCAL/DISPOSABLE Keycloak instance started by docker-compose.yml
# in this repo. It is NOT the production realm at keycloak.pangkalandata.id.
# No secrets from this script are production credentials — they are throwaway
# values for a container that will be torn down after this QA pass.
set -euo pipefail

KC_URL="http://127.0.0.1:8080"
REALM="nextjs-kc"
ADMIN_USER="${KEYCLOAK_USER:-admin}"
ADMIN_PASS="${KEYCLOAK_PASS:-admin}"

echo "== Waiting for Keycloak to be ready at $KC_URL =="
for i in $(seq 1 60); do
  if curl -sf "$KC_URL/realms/master/.well-known/openid-configuration" >/dev/null 2>&1; then
    echo "Keycloak is up."
    break
  fi
  sleep 3
done

get_admin_token() {
  curl -sf -X POST "$KC_URL/realms/master/protocol/openid-connect/token" \
    -d "client_id=admin-cli" \
    -d "username=$ADMIN_USER" \
    -d "password=$ADMIN_PASS" \
    -d "grant_type=password" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"
}

TOKEN=$(get_admin_token)
echo "== Got admin token =="

create_user() {
  local username="$1"
  local password="$2"
  local role="$3"  # "publisher" or "" for none

  echo "-- Creating user: $username (role=${role:-none}) --"
  USER_ID=$(curl -s -X POST "$KC_URL/admin/realms/$REALM/users" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$username\",\"enabled\":true,\"emailVerified\":true,\"email\":\"$username@pass5.local\",\"firstName\":\"$username\",\"lastName\":\"pass5\"}" \
    -w '\n%{http_code}' -D - -o /dev/null | grep -i '^location' | sed 's#.*/##' | tr -d '\r' || true)

  if [ -z "$USER_ID" ]; then
    echo "   user may already exist, looking it up..."
    USER_ID=$(curl -s -G "$KC_URL/admin/realms/$REALM/users" \
      -H "Authorization: Bearer $TOKEN" \
      --data-urlencode "username=$username" --data-urlencode "exact=true" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if d else '')")
  fi
  echo "   user id: $USER_ID"

  curl -s -X PUT "$KC_URL/admin/realms/$REALM/users/$USER_ID/reset-password" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"password\",\"value\":\"$password\",\"temporary\":false}" >/dev/null
  echo "   password set"

  if [ -n "$role" ]; then
    ROLE_JSON=$(curl -s "$KC_URL/admin/realms/$REALM/roles/$role" -H "Authorization: Bearer $TOKEN")
    curl -s -X POST "$KC_URL/admin/realms/$REALM/users/$USER_ID/role-mappings/realm" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "[$ROLE_JSON]" >/dev/null
    echo "   role '$role' assigned"
  fi

  echo "$username:$USER_ID"
}

create_user "publisher-a" "PassA-2026!" "publisher"
create_user "publisher-b" "PassB-2026!" "publisher"
create_user "user-c" "PassC-2026!" ""

echo "== Done. Users: publisher-a / publisher-b (role=publisher), user-c (no role) =="
