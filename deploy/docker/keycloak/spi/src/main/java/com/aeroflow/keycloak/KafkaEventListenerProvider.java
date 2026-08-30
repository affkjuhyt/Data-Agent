package com.aeroflow.keycloak;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.jboss.logging.Logger;
import org.keycloak.events.Event;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.admin.AdminEvent;

import java.util.HashMap;
import java.util.Map;

public class KafkaEventListenerProvider implements EventListenerProvider {

    private static final Logger log = Logger.getLogger(KafkaEventListenerProvider.class);

    private final KafkaProducer<String, String> producer;
    private final String topic;
    private final ObjectMapper mapper = new ObjectMapper();

    public KafkaEventListenerProvider(KafkaProducer<String, String> producer, String topic) {
        this.producer = producer;
        this.topic    = topic;
    }

    @Override
    public void onEvent(Event event) {
        try {
            // PII note: ipAddress and raw details are intentionally excluded.
            // If audit requirements need them, ensure data is masked or stored
            // in a dedicated PII-compliant store with appropriate access controls.
            Map<String, Object> payload = new HashMap<>();
            payload.put("type",      event.getType() != null ? event.getType().toString() : "UNKNOWN");
            payload.put("realmId",   event.getRealmId());
            payload.put("clientId",  event.getClientId());
            payload.put("userId",    event.getUserId());
            payload.put("sessionId", event.getSessionId());
            payload.put("time",      event.getTime());

            if (event.getError() != null) {
                payload.put("error", event.getError());
            }

            String json = mapper.writeValueAsString(payload);

            // Async send with callback — does NOT block the Keycloak login thread.
            // If Kafka is slow or unavailable, the login still completes; the event
            // is dropped and logged. For hard audit requirements, consider a local
            // write-ahead log with a retry mechanism.
            producer.send(
                new ProducerRecord<>(topic, event.getUserId(), json),
                (metadata, exception) -> {
                    if (exception != null) {
                        log.errorf(exception,
                            "Failed to publish Keycloak event type=%s to topic %s",
                            event.getType(), topic);
                    } else {
                        log.debugf("Published event type=%s user=%s to %s offset=%d",
                            event.getType(), event.getUserId(), topic, metadata.offset());
                    }
                }
            );

        } catch (Exception e) {
            log.errorf(e, "Failed to serialize Keycloak event for topic %s: %s", topic, e.getMessage());
        }
    }

    @Override
    public void onEvent(AdminEvent adminEvent, boolean includeRepresentation) {
        // Admin events are not forwarded to the audit topic
    }

    @Override
    public void close() {
        // Producer lifecycle is managed by the factory
    }
}
