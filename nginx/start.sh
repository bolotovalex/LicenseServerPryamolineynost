#!/bin/sh
set -e

: "${DOMAIN:?Задайте DOMAIN в docker-compose}"
: "${CERTBOT_EMAIL:?Задайте CERTBOT_EMAIL в docker-compose}"

CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
WEBROOT="/var/www/certbot"

mkdir -p "${WEBROOT}"

# Генерация nginx-конфига из шаблона (${DOMAIN} → реальный домен)
envsubst '${DOMAIN}' < /etc/nginx/templates/default.conf.template \
    > /etc/nginx/conf.d/default.conf

if [ ! -f "${CERT_DIR}/fullchain.pem" ]; then
    echo "[nginx] Первый запуск: создаём временный сертификат..."
    mkdir -p "${CERT_DIR}"
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -keyout "${CERT_DIR}/privkey.pem" \
        -out    "${CERT_DIR}/fullchain.pem" \
        -subj "/CN=localhost" 2>/dev/null

    echo "[nginx] Запускаем nginx с временным сертификатом..."
    nginx
    sleep 2

    echo "[nginx] Запрашиваем сертификат Let's Encrypt для ${DOMAIN}..."
    certbot certonly --webroot -w "${WEBROOT}" \
        --email "${CERTBOT_EMAIL}" \
        -d "${DOMAIN}" \
        --rsa-key-size 4096 \
        --agree-tos \
        --non-interactive \
        --force-renewal

    echo "[nginx] Перезагружаем nginx с реальным сертификатом..."
    nginx -s reload
fi

# Автообновление сертификата каждые 12 часов
(while :; do
    sleep 12h
    certbot renew --webroot -w "${WEBROOT}" --quiet && nginx -s reload
done) &

exec nginx -g "daemon off;"
