# --optimized (service/keycloak.yaml) skips re-reading KC_DB/KC_DB_URL from
# env at boot and trusts a build-time `kc.sh build` instead — the stock
# quay.io/keycloak/keycloak image was never built with --db=postgres, so it
# falls back to its baked-in dev H2 store. Bake it here instead: this is a
# build-time step (needs to write into /opt/keycloak), so it can't happen
# under the pod's readOnlyRootFilesystem at runtime, and there's nothing to
# COPY — the realm and mappers are mounted from ConfigMaps at runtime
# (keycloak.yaml), same as the stock image.
FROM quay.io/keycloak/keycloak:24.0
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true --cache=ispn --cache-stack=kubernetes
