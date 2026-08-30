package com.aeroflow.keycloak;

import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.serialization.StringSerializer;
import org.jboss.logging.Logger;
import org.keycloak.Config;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.EventListenerProviderFactory;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;

import java.util.Properties;

public class KafkaEventListenerProviderFactory implements EventListenerProviderFactory {

    private static final Logger log = Logger.getLogger(KafkaEventListenerProviderFactory.class);

    private KafkaProducer<String, String> producer;
    private String topic;

    @Override
    public EventListenerProvider create(KeycloakSession session) {
        return new KafkaEventListenerProvider(producer, topic);
    }

    @Override
    public void init(Config.Scope config) {
        String bootstrapServers = env("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092");
        topic                   = env("KAFKA_TOPIC",             "audit.auth.events");
        String saslUser         = env("KAFKA_SASL_USERNAME",     "");
        String saslPass         = env("KAFKA_SASL_PASSWORD",     "");

        Properties props = new Properties();
        props.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG,      bootstrapServers);
        props.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG,   StringSerializer.class.getName());
        props.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        props.put(ProducerConfig.ACKS_CONFIG,                   "all");
        props.put(ProducerConfig.RETRIES_CONFIG,                5);
        props.put(ProducerConfig.RETRY_BACKOFF_MS_CONFIG,       500);
        // Disable idempotence — requires CLUSTER:IDEMPOTENT_WRITE ACL which audit-bridge doesn't hold
        props.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG,     false);

        if (!saslUser.isEmpty()) {
            props.put("security.protocol", "SASL_PLAINTEXT");
            props.put("sasl.mechanism",    "PLAIN");
            props.put("sasl.jaas.config",  String.format(
                "org.apache.kafka.common.security.plain.PlainLoginModule required " +
                "username=\"%s\" password=\"%s\";", saslUser, saslPass));
            log.infof("Kafka SASL/PLAIN enabled for user: %s", saslUser);
        }

        producer = new KafkaProducer<>(props);
        log.infof("Kafka event listener initialized — bootstrap=%s topic=%s", bootstrapServers, topic);
    }

    @Override
    public void postInit(KeycloakSessionFactory factory) {}

    @Override
    public void close() {
        if (producer != null) {
            producer.close();
            log.info("Kafka producer closed");
        }
    }

    @Override
    public String getId() {
        return "aeroflow-kafka";
    }

    private static String env(String name, String fallback) {
        String val = System.getenv(name);
        return (val != null && !val.isEmpty()) ? val : fallback;
    }
}
