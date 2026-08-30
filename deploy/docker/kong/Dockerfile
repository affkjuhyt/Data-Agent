# Kong community 3.6 + aeroflow-jwks custom plugin
# Plugin: JWKS fetch + RS256 verify + 300s cache + X-User-* header injection
# Replaces: jwt built-in plugin + jwks-refresher service
FROM kong:3.6-ubuntu
COPY deploy/docker/kong/plugins/aeroflow-jwks /usr/local/share/lua/5.1/kong/plugins/aeroflow-jwks
