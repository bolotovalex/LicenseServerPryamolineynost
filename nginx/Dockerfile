FROM nginx:1.27-alpine
RUN apk add --no-cache certbot openssl
COPY start.sh /start.sh
RUN chmod +x /start.sh
ENTRYPOINT ["/start.sh"]
