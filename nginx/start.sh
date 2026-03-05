#!/bin/sh
set -e

: "${DOMAIN:?Задайте DOMAIN в docker-compose}"
: "${CERTBOT_EMAIL:?Задайте CERTBOT_EMAIL в docker-compose}"

CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
WEBROOT="/var/www/certbot"

mkdir -p "${WEBROOT}" /etc/nginx/conf.d

# Генерация nginx-конфига (nginx-переменные экранированы через \$)
cat > /etc/nginx/conf.d/default.conf << NGINXEOF
server {
    listen 80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name ${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    client_max_body_size 10m;

    location / {
        proxy_pass         http://app:8000;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
NGINXEOF

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
