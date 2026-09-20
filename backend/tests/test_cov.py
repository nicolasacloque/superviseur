import asyncio

from app.collector.config import CovConfig
from app.collector.cov import CovManager
from app.collector.driver_base import DeviceUnreachable, RequestRejected
from tests.fakes import FakeDriver, make_registry


def build(**cfg: float) -> tuple[FakeDriver, CovManager, object]:
    registry = make_registry(points=3, cov_capable=True)
    driver = FakeDriver()
    manager = CovManager(driver, registry, CovConfig(lifetime_s=100, renew_before_s=20, **cfg))
    return driver, manager, next(iter(registry.devices.values()))


async def test_subscribes_every_point_once() -> None:
    driver, manager, device = build()
    await manager.service(device)  # type: ignore[arg-type]
    assert len(driver.subscriptions) == 3
    assert all(p.cov_active for p in device.points.values())  # type: ignore[attr-defined]

    await manager.service(device)  # type: ignore[arg-type]
    assert len(driver.subscriptions) == 3  # pas de doublon tant que rien n'expire


async def test_renews_before_expiry() -> None:
    driver, manager, device = build()
    await manager.service(device)  # type: ignore[arg-type]
    loop_now = asyncio.get_running_loop().time()
    for point in device.points.values():  # type: ignore[attr-defined]
        point.cov_expires = loop_now + 10  # moins de renew_before_s (20 s) avant expiration
    await manager.service(device)  # type: ignore[arg-type]
    assert len(driver.subscriptions) == 6


async def test_failed_renewal_falls_back_to_polling() -> None:
    driver, manager, device = build()
    await manager.service(device)  # type: ignore[arg-type]
    loop_now = asyncio.get_running_loop().time()
    for point in device.points.values():  # type: ignore[attr-defined]
        point.cov_expires = loop_now
        point.next_due = loop_now + 3600
    driver.cov_error = RequestRejected("error", "device: no-space-for-object")

    await manager.service(device)  # type: ignore[arg-type]

    for point in device.points.values():  # type: ignore[attr-defined]
        assert not point.cov_active  # repli sur le polling
        assert point.next_due <= asyncio.get_running_loop().time()  # relecture immédiate
        assert point.cov_retry_at > loop_now  # nouvel essai plus tard


async def test_reject_disables_cov_for_the_device() -> None:
    driver, manager, device = build()
    driver.cov_error = RequestRejected("reject", "unrecognized-service")
    await manager.service(device)  # type: ignore[arg-type]
    assert device.supports_cov is False  # type: ignore[attr-defined]
    assert driver.subscriptions == []


async def test_unreachable_device_is_left_to_the_poller() -> None:
    driver, manager, device = build()
    driver.cov_error = DeviceUnreachable("muet")
    await manager.service(device)  # type: ignore[arg-type]
    assert not any(p.cov_active for p in device.points.values())  # type: ignore[attr-defined]
