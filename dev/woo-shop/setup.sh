#!/usr/bin/env bash
# Installs WordPress, WooCommerce and the test products into the running containers. Run once,
# after the first `docker compose up -d`. The data stays until `docker compose down -v`.
set -euo pipefail
cd "$(dirname "$0")"
wp() { docker compose exec -T wp wp --allow-root "$@"; }

if wp core is-installed 2>/dev/null; then
  echo "Already set up: http://localhost:8088 (keys in keys.txt)"
  exit 0
fi
echo "Downloading WordPress..."
wp core download --version=6.6 --quiet
wp config create --dbname=wp --dbuser=wp --dbpass=wp --dbhost=db --skip-check --quiet --extra-php <<'PHP'
// Woo takes HeyDesk's key only over HTTPS. This test shop is plain HTTP, so its API calls
// count as HTTPS. Only in this test shop: never on a real store
if (str_starts_with($_SERVER['REQUEST_URI'] ?? '', '/wp-json/')) { $_SERVER['HTTPS'] = 'on'; }
PHP
until wp db check --quiet >/dev/null 2>&1; do echo "Waiting for the database..."; sleep 2; done
wp core install --url=http://localhost:8088 --title="Test Shop" --admin_user=admin \
  --admin_password=admin --admin_email=admin@example.test --skip-email --quiet
wp rewrite structure '/%postname%/' --quiet  # the API lives at /wp-json/
echo "Installing WooCommerce..."
wp plugin install woocommerce --version=9.4.3 --activate --quiet  # the newest that fits WordPress 6.6
docker compose cp seed.php wp:/var/www/html/seed.php >/dev/null
wp eval-file seed.php > keys.txt
echo
echo "Done. Shop: http://localhost:8088   Admin: http://localhost:8088/wp-admin (admin / admin)"
echo "API keys for HeyDesk are in keys.txt"
