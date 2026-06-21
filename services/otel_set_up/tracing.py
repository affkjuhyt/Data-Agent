import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)


def setup_tracing(service_name: str, app=None) -> None:
    """Khởi tạo OTel tracing, export traces tới Jaeger qua OTLP gRPC.
    Gọi 1 lần lúc service start. Pass `app` (FastAPI instance) để auto-trace HTTP requests."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    if app is not None:
        FastAPIInstrumentor.instrument_app(app)

    SQLAlchemyInstrumentor().instrument()

    # Các instrumentation sau optional — chỉ service nào install package mới bật
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.pymongo import PymongoInstrumentor
        PymongoInstrumentor().instrument()
    except ImportError:
        pass

    # Gắn trace_id/span_id vào mọi log record — để nhảy từ Jaeger sang Kibana
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        LoggingInstrumentor().instrument()
    except ImportError:
        pass

    logger.info("otel | tracing initialized service=%s endpoint=%s", service_name, endpoint)
