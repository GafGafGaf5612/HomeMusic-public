FROM alpine:3.22
RUN apk add --no-cache openssh-client-default \
 && adduser -D -H -u 10001 tunnel
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod 755 /entrypoint.sh
USER tunnel
ENTRYPOINT ["/entrypoint.sh"]
