# Apache Kafka 3.7 KRaft + SASL/PLAIN (dev)
# Mounts JAAS config with static credentials - no SCRAM pre-seeding needed.
FROM apache/kafka:3.7.0
COPY deploy/docker/kafka/kafka-broker-jaas.config /opt/kafka/config/kafka-broker-jaas.config
USER root
RUN chown appuser:appuser /opt/kafka/config/kafka-broker-jaas.config
USER appuser
