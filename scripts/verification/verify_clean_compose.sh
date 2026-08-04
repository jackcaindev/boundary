#!/bin/sh
set -eu

if [ "${1:-}" != "--reset" ]; then
  echo "usage: $0 --reset" >&2
  echo "--reset explicitly deletes only this Compose project's local volumes" >&2
  exit 2
fi

docker compose down --volumes --remove-orphans
docker compose up --build --detach

wait_healthy() {
  service="$1"
  attempts=0
  while [ "$attempts" -lt 90 ]; do
    container_id=$(docker compose ps --quiet "$service")
    if [ -n "$container_id" ]; then
      health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")
      if [ "$health" = "healthy" ]; then
        echo "$service health: healthy"
        return 0
      fi
    fi
    attempts=$((attempts + 1))
    sleep 1
  done
  echo "$service did not become healthy" >&2
  docker compose ps >&2
  return 1
}

wait_healthy postgres
wait_healthy boundary
wait_healthy sample-agent
wait_healthy frontend

migration_id=$(docker compose ps --all --quiet migrate)
test -n "$migration_id"
test "$(docker inspect --format '{{.State.ExitCode}}' "$migration_id")" = "0"
test "$(docker compose exec -T postgres psql -U boundary -d boundary -Atc 'SELECT version_num FROM alembic_version')" = "0007_executor_public_api"
echo "migration: 0007_executor_public_api"

test "$(docker compose port frontend 5173)" = "127.0.0.1:5173"
for unpublished in "boundary 8000" "postgres 5432" "sample-agent 8001"; do
  service=${unpublished% *}
  port=${unpublished#* }
  published=$(docker compose port "$service" "$port" 2>/dev/null || true)
  case "$published" in
    ""|":0") ;;
    *) echo "$service unexpectedly publishes $published" >&2; exit 1 ;;
  esac
done
echo "host publication: frontend only"

test "$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' http://127.0.0.1:5173/healthz)" = "200"
public_url=http://127.0.0.1:5173/api/v1/campaigns/00000000-0000-4000-8000-000000000000
public_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$public_url")
public_body=$(curl --silent --show-error "$public_url")
test "$public_status" = "404"
case "$public_body" in
  *'"status":404'*'"code":"CAMPAIGN_NOT_FOUND"'*) ;;
  *) echo "public /api response did not come from Boundary" >&2; exit 1 ;;
esac
for internal_path in /internal /internal/v1/runs/not-a-run/tools/phase1-lookup; do
  test "$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:5173$internal_path")" = "404"
done
echo "proxy boundary: /api public; /internal denied"

npm --prefix tests/e2e ci
(
  cd tests/e2e
  if [ -z "${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH:-}" ]; then
    npx playwright install chromium
  fi
  npm run typecheck
  npm test
)
