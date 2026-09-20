"""Ensemble de devices simulés (10 devices x 200 points par défaut)."""

from __future__ import annotations

from bacnet_sim.device import SimDevice
from bacnet_sim.faults import Faults

__all__ = ["Faults", "SimDevice", "Simulator"]


class Simulator:
    """Un device par port UDP consécutif, à partir de `base_port`."""

    def __init__(
        self,
        devices: int = 10,
        points_per_device: int = 200,
        host: str = "127.0.0.1",
        base_port: int = 47809,
        first_instance: int = 1001,
    ) -> None:
        self.devices = [
            SimDevice(first_instance + i, host, base_port + i, points_per_device)
            for i in range(devices)
        ]

    @property
    def addresses(self) -> list[str]:
        return [device.address for device in self.devices]

    def start(self, animate: bool = True, tick_s: float = 2.0) -> None:
        for device in self.devices:
            device.start(animate=animate, tick_s=tick_s)

    def stop(self) -> None:
        for device in self.devices:
            device.stop()
