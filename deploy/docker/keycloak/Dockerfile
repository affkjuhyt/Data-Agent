# ── Stage 1: Build Kafka SPI JAR ───────────────────────────────────────
FROM maven:3.9-eclipse-temurin-17 AS spi-builder
WORKDIR /build
# Download dependencies first (layer cache)
COPY deploy/docker/keycloak/spi/pom.xml .
RUN mvn dependency:go-offline -q
# Build JAR
COPY deploy/docker/keycloak/spi/src/ frontend/src/
RUN mvn package -q -DskipTests
# Result: /build/target/keycloak-kafka-event-listener-1.0.0.jar

# ── Stage 2: Keycloak with SPI ─────────────────────────────────────────
FROM quay.io/keycloak/keycloak:24.0
COPY --from=spi-builder /build/target/keycloak-kafka-event-listener-1.0.0.jar \
     /opt/keycloak/providers/
# Pre-build Keycloak with the new provider so startup is faster
RUN /opt/keycloak/bin/kc.sh build
