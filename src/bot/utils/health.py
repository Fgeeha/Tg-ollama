"""Health check server for monitoring."""
import structlog
from aiohttp import web

from bot.utils.ollama import OllamaClient

logger = structlog.get_logger()


async def liveness_handler(request: web.Request) -> web.Response:
    """Liveness check: process is up, no dependency checks. Cheap, always 200."""
    return web.json_response({"status": "alive"})


async def health_handler(request: web.Request) -> web.Response:
    """Readiness check: bot and its Ollama dependency."""
    ollama_client: OllamaClient = request.app["ollama_client"]

    health_status = {
        "status": "healthy",
        "bot": "online",
        "ollama": "unknown"
    }

    # Check Ollama connection
    try:
        if await ollama_client.health_check():
            health_status["ollama"] = "online"
        else:
            health_status["ollama"] = "offline"
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["ollama"] = "error"
        health_status["status"] = "unhealthy"
        health_status["error"] = str(e)

    status_code = 200 if health_status["status"] == "healthy" else 503

    return web.json_response(health_status, status=status_code)


async def metrics_handler(request: web.Request) -> web.Response:
    """Metrics endpoint (placeholder for Prometheus integration)."""
    # This could be expanded to include actual Prometheus metrics
    metrics = """
# HELP bot_health Bot health status
# TYPE bot_health gauge
bot_health 1

# HELP ollama_connection Ollama connection status
# TYPE ollama_connection gauge
ollama_connection 1
"""

    return web.Response(text=metrics, content_type="text/plain")


async def start_health_server(ollama_client: OllamaClient, port: int = 8080) -> web.AppRunner:
    """Start the health check server."""
    app = web.Application()
    app["ollama_client"] = ollama_client

    app.router.add_get("/health", health_handler)
    app.router.add_get("/healthz", liveness_handler)
    app.router.add_get("/readyz", health_handler)
    app.router.add_get("/metrics", metrics_handler)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"Health check server started on port {port}")

    return runner


async def stop_health_server(runner: web.AppRunner) -> None:
    """Stop the health check server."""
    await runner.cleanup()
    logger.info("Health check server stopped")
