import contextvars
import logging
import os

correlation_id_var = contextvars.ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record):
        record.correlation_id = correlation_id_var.get()
        return True


def configure_monitoring(logger):
    """Wire the worker's logger to Application Insights (if configured) and
    make every log record carry the current correlation_id automatically."""
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if connection_string:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=connection_string, logger_name=logger.name)

    # Attach to the root logger (not just `logger`) so every record passing
    # through any handler has a correlation_id attribute -- otherwise the
    # %(correlation_id)s format string breaks on records from other loggers
    # (e.g. azure-sdk's own loggers).
    logging.getLogger().addFilter(CorrelationIdFilter())
