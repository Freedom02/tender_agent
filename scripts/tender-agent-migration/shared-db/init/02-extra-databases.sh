#!/bin/bash
# 首次初始化时执行：把 EXTRA_DATABASES（逗号分隔）建出来，并套用同一套表结构。
# 每个伙伴一个库，避免开发期互相踩数据。库名示例：tender_dev_alice,tender_dev_bob
# 用子 shell 包裹：官方入口会以 source 方式执行无执行位的 .sh，避免 set 选项外泄。
(
set -euo pipefail

IFS=',' read -r -a DBS <<< "${EXTRA_DATABASES:-}"
for raw in "${DBS[@]:-}"; do
  db="$(echo "$raw" | xargs)"
  [ -z "$db" ] && continue

  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    CREATE DATABASE "$db" OWNER "$POSTGRES_USER"
      ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;
EOSQL

  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" \
    -f /docker-entrypoint-initdb.d/01-schema.sql

  echo "[init] created database: $db"
done
)
