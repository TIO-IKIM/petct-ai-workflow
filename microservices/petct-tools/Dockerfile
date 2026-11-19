FROM curlimages/curl:8.16.0
USER root
RUN apk add --no-cache python3
COPY bin/ /usr/local/bin/
RUN chmod 755 /usr/local/bin/*
