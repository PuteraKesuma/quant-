"""Entrypoint: start the FastAPI signal server using host/port from config.yaml.

    python -m pipeline.live.run_server
"""
import uvicorn
from loguru import logger

from ..fetch.base_fetcher import load_config


def main():
    cfg = load_config()["live"]
    host, port = cfg["host"], cfg["port"]
    slots = ", ".join(f"{s['name']}({s['type']}->{s['symbol']})" for s in cfg["strategies"])
    logger.info(f"Starting signal server on http://{host}:{port}  slots: {slots}")
    uvicorn.run("pipeline.live.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
