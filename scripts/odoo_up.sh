#!/usr/bin/env bash
# Démarre l'ERP A (Odoo 17) et crée la base `demo_a` si elle n'existe pas.
#   ./scripts/odoo_up.sh            démarre, crée la base au besoin
#   ./scripts/odoo_up.sh --reset    supprime tout (base et fichiers) puis recrée
set -euo pipefail
cd "$(dirname "$0")/.."
DB="${ODOO_DB:-demo_a}"

if [ "${1:-}" = "--reset" ]; then
  docker compose down -v
fi

docker compose up -d db
if ! docker compose exec -T db psql -U odoo -d postgres -tAc "select 1 from pg_database where datname='${DB}'" | grep -q 1; then
  echo "Création de la base ${DB} (module purchase, sans données de démonstration)..."
  docker compose run --rm odoo odoo -d "${DB}" -i purchase --without-demo=all --stop-after-init
fi
docker compose up -d odoo

echo -n "Attente d'Odoo sur http://localhost:8170 "
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null "http://localhost:8170/web/login"; then echo " prêt."; exit 0; fi
  echo -n "."; sleep 2
done
echo " délai dépassé"; exit 1
