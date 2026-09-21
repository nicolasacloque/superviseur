"""Abonnements COV : création, renouvellement avant expiration, repli sur le polling."""

from __future__ import annotations

import asyncio
import logging

from app.collector.config import CovConfig
from app.collector.driver_base import CovDriver, DeviceUnreachable, DriverError, RequestRejected
from app.collector.registry import DeviceState, Registry

log = logging.getLogger(__name__)


class CovManager:
    def __init__(
        self, driver: CovDriver, registry: Registry, config: CovConfig, tick_s: float = 1.0
    ) -> None:
        self._driver = driver
        self._registry = registry
        self._config = config
        self._tick_s = tick_s

    async def run(self) -> None:
        while True:
            devices = [d for d in self._registry.devices.values() if d.supports_cov and d.online]
            await asyncio.gather(*(self.service(device) for device in devices))
            await asyncio.sleep(self._tick_s)

    async def service(self, device: DeviceState) -> None:
        """Abonne les points qui ne le sont pas et renouvelle ceux qui vont expirer."""
        cfg = self._config
        now = asyncio.get_running_loop().time()
        for point in list(device.points.values()):
            if not point.cov_capable:
                continue
            renewing = point.cov_active
            if renewing:
                if now < point.cov_expires - cfg.renew_before_s:
                    continue
            elif now < point.cov_retry_at:
                continue
            try:
                await self._driver.subscribe_cov(point.ref, cfg.lifetime_s)
            except DeviceUnreachable:
                return  # le poller détecte la perte du device et relancera les abonnements
            except DriverError as exc:
                log.warning("COV %s : abonnement refusé (%s), repli sur polling", point.name, exc)
                point.cov_active = False
                point.cov_retry_at = now + cfg.retry_s
                if renewing:
                    point.next_due = now  # relecture immédiate
                if isinstance(exc, RequestRejected) and exc.kind == "reject":
                    device.supports_cov = False  # service non supporté par ce device
                    return
                continue
            point.cov_active = True
            point.cov_expires = now + cfg.lifetime_s if cfg.lifetime_s else float("inf")
            # Polling de sécurité : première relecture dans `safety_poll_s`.
            point.next_due = max(point.next_due, now + cfg.safety_poll_s)
