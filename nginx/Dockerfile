FROM nginx:1.27-alpine
RUN apk add --no-cache certbot openssl
COPY start.sh /start.sh
COPY templates/ /etc/nginx/templates/
RUN chmod +x /start.sh
ENTRYPOINT ["/start.sh"]
