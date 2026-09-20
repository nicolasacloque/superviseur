"""Lancement autonome : `python -m bacnet_sim` (configuré par variables d'environnement)."""

from __future__ import annotations

import asyncio
import logging
import os

from bacnet_sim.simulator import Simulator


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    sim = Simulator(
        devices=int(os.environ.get("SIM_DEVICES", "10")),
        points_per_device=int(os.environ.get("SIM_POINTS_PER_DEVICE", "200")),
        host=os.environ.get("SIM_HOST", "127.0.0.1"),
        base_port=int(os.environ.get("SIM_BASE_PORT", "47809")),
        first_instance=int(os.environ.get("SIM_FIRST_INSTANCE", "1001")),
    )
    sim.start(tick_s=float(os.environ.get("SIM_TICK_S", "2")))
    logging.getLogger("bacnet_sim").info("simulateur demarre: %s", ", ".join(sim.addresses))
    try:
        await asyncio.Event().wait()
    finally:
        sim.stop()


if __name__ == "__main__":
    asyncio.run(main())
