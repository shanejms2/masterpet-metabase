#!/bin/bash
set -euo pipefail

COMPOSE_FILE="${HOME}/metabase/docker-compose.yml"
H2_PATH="/metabase-data/metabase.db/metabase.db"

if ! sg docker -c "docker compose --project-name metabase -f ${COMPOSE_FILE} ps --status running --services" | grep -qx postgres; then
  sg docker -c "docker compose --project-name metabase -f ${COMPOSE_FILE} up -d postgres"
fi

for _ in $(seq 1 30); do
  status=$(sg docker -c "docker inspect metabase-postgres --format '{{.State.Health.Status}}'" 2>/dev/null || true)
  [ "$status" = "healthy" ] && break
  sleep 2
done

sg docker -c "docker compose --project-name metabase -f ${COMPOSE_FILE} stop metabase" 2>/dev/null || true

sg docker -c "docker run --rm --user 2000:2000 --network metabase_default \
  -v ${HOME}/metabase/metabase-data:/metabase-data \
  --entrypoint java --env-file ${HOME}/metabase/.env \
  metabase/metabase:v0.58.6 \
  --add-opens java.base/java.nio=ALL-UNNAMED -jar /app/metabase.jar load-from-h2 ${H2_PATH}"

sg docker -c "docker compose --project-name metabase -f ${COMPOSE_FILE} up -d metabase"
