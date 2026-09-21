"""Driver BACnet/IP (bacpypes3) : découverte, lecture, écriture, abonnements COV.

Toutes les requêtes passent par `_call` : un seul échange à la fois par device
(les contrôleurs sont souvent faibles) et un plafond global de requêtes simultanées.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, TypeVar

from bacpypes3.apdu import (
    AbortPDU,
    ConfirmedCOVNotificationRequest,
    ConfirmedEventNotificationRequest,
    ErrorRejectAbortNack,
    SimpleAckPDU,
    SubscribeCOVRequest,
    UnconfirmedCOVNotificationRequest,
    UnconfirmedEventNotificationRequest,
)
from bacpypes3.app import Application
from bacpypes3.basetypes import ErrorType, HostNPort, IPMode
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.pdu import Address
from bacpypes3.primitivedata import Null, ObjectIdentifier, ObjectType
from bacpypes3.vendor import get_vendor_info

from app.collector.config import (
    CovConfig,
    DiscoveryConfig,
    NetworkConfig,
    PollingConfig,
    expand_targets,
)
from app.collector.driver_base import (
    DeviceInfo,
    DeviceUnreachable,
    DriverError,
    EventCallback,
    EventNotice,
    ObjectInfo,
    PointRef,
    Reading,
    ReadingCallback,
    RequestRejected,
    WriteResult,
)
from app.collector.normalizer import (
    IMPORTED_TYPES,
    build_reading,
    is_multistate,
    is_writable,
    normalize_present_value,
    normalize_status,
    unit_label,
)
from app.common.models import PointStatus

log = logging.getLogger(__name__)

T = TypeVar("T")
ObjectKey = tuple[str, int]

_ANALOG_PROPS = ("object-name", "description", "units", "present-value", "status-flags")
_BINARY_PROPS = ("object-name", "description", "present-value", "status-flags")
_MULTISTATE_PROPS = ("object-name", "description", "state-text", "present-value", "status-flags")
_LIVE_PROPS = ("present-value", "status-flags")


def _discovery_props(object_type: str) -> tuple[str, ...]:
    if object_type.startswith("analog"):
        return _ANALOG_PROPS
    return _MULTISTATE_PROPS if is_multistate(object_type) else _BINARY_PROPS


def _convert_error(exc: ErrorRejectAbortNack) -> DriverError:
    """Traduit une erreur / rejet / abandon BACnet en exception du driver."""
    if isinstance(exc, AbortPDU) and str(exc.apduAbortRejectReason) == "no-response":
        return DeviceUnreachable(str(exc))
    kind = "abort" if isinstance(exc, AbortPDU) else type(exc).__name__.removesuffix("PDU").lower()
    return RequestRejected(kind, str(exc))


def _flags(raw: Any) -> list[int] | None:
    """Les 4 bits de `status-flags` sous forme de liste d'entiers."""
    if raw is None:
        return None
    try:
        return [int(raw[i]) for i in range(4)]
    except (TypeError, IndexError, ValueError):
        return None


def _chunks[X](items: list[X], size: int) -> Iterable[list[X]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class CollectorApplication(Application):  # type: ignore[misc]
    """Application bacpypes3 qui relaie les notifications COV au driver."""

    cov_handler: Callable[[Any], Awaitable[None]] | None = None
    event_handler: Callable[[Any], Awaitable[None]] | None = None

    async def do_ConfirmedEventNotificationRequest(
        self, apdu: ConfirmedEventNotificationRequest
    ) -> None:
        if self.event_handler:
            await self.event_handler(apdu)
        await self.response(SimpleAckPDU(context=apdu))

    async def do_UnconfirmedEventNotificationRequest(
        self, apdu: UnconfirmedEventNotificationRequest
    ) -> None:
        if self.event_handler:
            await self.event_handler(apdu)

    async def do_ConfirmedCOVNotificationRequest(
        self, apdu: ConfirmedCOVNotificationRequest
    ) -> None:
        if self.cov_handler:
            await self.cov_handler(apdu)
        await self.response(SimpleAckPDU(context=apdu))

    async def do_UnconfirmedCOVNotificationRequest(
        self, apdu: UnconfirmedCOVNotificationRequest
    ) -> None:
        if self.cov_handler:
            await self.cov_handler(apdu)


class BacnetDriver:
    def __init__(
        self,
        network: NetworkConfig,
        discovery: DiscoveryConfig,
        polling: PollingConfig,
        cov: CovConfig,
    ) -> None:
        self._network = network
        self._discovery = discovery
        self._polling = polling
        self._cov = cov
        self._app: CollectorApplication | None = None
        self._semaphore = asyncio.Semaphore(polling.max_concurrent_requests)
        self._locks: dict[int, asyncio.Lock] = {}
        self._callbacks: list[ReadingCallback] = []
        self._event_callbacks: list[EventCallback] = []
        self._rpm_unsupported: set[int] = set()
        self._process_ids: dict[Any, int] = {}
        self._cov_points: dict[tuple[str, int], PointRef] = {}
        # Délai maximal d'un échange : timeout de chaque tentative x tentatives, plus une marge.
        self._deadline_s = polling.timeout_s * (polling.retries + 1) + 2.0

    # -- cycle de vie ------------------------------------------------------------

    async def start(self) -> None:
        net = self._network
        device = DeviceObject(
            objectIdentifier=("device", net.device_instance),
            objectName="ZAKLOK-Supervisor",
            vendorIdentifier=999,
            apduTimeout=int(self._polling.timeout_s * 1000),
            numberOfApduRetries=self._polling.retries,
        )
        port = NetworkPortObject(
            f"{net.bind_ip}:{net.port}",
            objectIdentifier=("network-port", 1),
            objectName="NetworkPort-1",
            networkNumber=0,
            networkNumberQuality="unknown",
        )
        if net.bbmd.enabled:
            # Enregistrement comme Foreign Device auprès du BBMD.
            port.bacnetIPMode = IPMode.foreign
            port.fdBBMDAddress = HostNPort(net.bbmd.address)
            port.fdSubscriptionLifetime = net.bbmd.ttl
        app = CollectorApplication.from_object_list([device, port])
        app.cov_handler = self._on_cov
        app.event_handler = self._on_event
        self._app = app

    async def stop(self) -> None:
        if self._app is not None:
            self._app.close()
            self._app = None

    def subscribe(self, callback: ReadingCallback) -> None:
        """Enregistre un rappel appelé pour chaque notification COV reçue."""
        self._callbacks.append(callback)

    def subscribe_events(self, callback: EventCallback) -> None:
        """Enregistre un rappel pour chaque Event Notification reçue (règles `bacnet_event`)."""
        self._event_callbacks.append(callback)

    @property
    def app(self) -> CollectorApplication:
        if self._app is None:
            raise DriverError("driver non démarré")
        return self._app

    # -- échanges ------------------------------------------------------------------

    async def _call(self, instance: int, make: Callable[[], Awaitable[T]]) -> T:
        lock = self._locks.setdefault(instance, asyncio.Lock())
        async with lock, self._semaphore:
            try:
                return await asyncio.wait_for(make(), self._deadline_s)
            except TimeoutError as exc:
                raise DeviceUnreachable(f"device {instance}: délai dépassé") from exc
            except ErrorRejectAbortNack as exc:
                # Les erreurs BACnet héritent de BaseException : à intercepter explicitement.
                raise _convert_error(exc) from None

    async def _read_property(
        self, instance: int, address: str, objid: str, prop: str, index: int | None = None
    ) -> Any:
        return await self._call(
            instance,
            lambda: self.app.read_property(Address(address), objid, prop, index),
        )

    async def _read_many(
        self, instance: int, address: str, wanted: Mapping[ObjectKey, tuple[str, ...]]
    ) -> dict[ObjectKey, dict[str, Any]]:
        """Lit des propriétés de plusieurs objets : RPM si supporté, sinon ReadProperty."""
        if instance not in self._rpm_unsupported:
            try:
                return await self._read_multiple(instance, address, wanted)
            except RequestRejected as exc:
                if exc.kind not in ("reject", "abort"):
                    raise
                log.info("device %s : RPM refusé (%s), repli sur ReadProperty", instance, exc)
                self._rpm_unsupported.add(instance)
        return await self._read_each(instance, address, wanted)

    async def _read_multiple(
        self, instance: int, address: str, wanted: Mapping[ObjectKey, tuple[str, ...]]
    ) -> dict[ObjectKey, dict[str, Any]]:
        parameters: list[Any] = []
        for (object_type, number), props in wanted.items():
            parameters += [f"{object_type},{number}", list(props)]
        results = await self._call(
            instance,
            lambda: self.app.read_property_multiple(Address(address), parameters),
        )
        found: dict[ObjectKey, dict[str, Any]] = {}
        for objid, prop, _index, value in results:
            if isinstance(value, ErrorType):
                continue  # propriété absente sur cet objet
            found.setdefault((str(objid[0]), int(objid[1])), {})[str(prop)] = value
        return found

    async def _read_each(
        self, instance: int, address: str, wanted: Mapping[ObjectKey, tuple[str, ...]]
    ) -> dict[ObjectKey, dict[str, Any]]:
        found: dict[ObjectKey, dict[str, Any]] = {}
        for (object_type, number), props in wanted.items():
            for prop in props:
                try:
                    value = await self._read_property(
                        instance, address, f"{object_type},{number}", prop
                    )
                except RequestRejected:
                    continue  # propriété absente ou refusée : on passe à la suivante
                found.setdefault((object_type, number), {})[prop] = value
        return found

    # -- découverte ------------------------------------------------------------------

    async def discover(self) -> list[DeviceInfo]:
        cfg = self._discovery
        targets: list[Address | None] = []
        if "/" in self._network.bind_ip:
            targets.append(None)  # broadcast : il faut un masque pour en déduire l'adresse
        targets += [Address(t) for t in expand_targets(cfg.targets)]

        async def who_is(address: Address | None) -> list[Any]:
            try:
                answers: list[Any] = await self.app.who_is(
                    cfg.who_is_low, cfg.who_is_high, address=address, timeout=cfg.timeout_s
                )
                return answers
            except (OSError, DriverError) as exc:
                log.warning("Who-Is vers %s en échec : %s", address or "broadcast", exc)
                return []

        answers = await asyncio.gather(*(who_is(t) for t in targets))
        unique: dict[int, Any] = {}
        for iam in (iam for group in answers for iam in group):
            unique.setdefault(int(iam.iAmDeviceIdentifier[1]), iam)

        infos = await asyncio.gather(
            *(self._device_info(instance, iam) for instance, iam in unique.items())
        )
        return sorted(infos, key=lambda info: info.instance)

    async def _device_info(self, instance: int, iam: Any) -> DeviceInfo:
        address = str(iam.pduSource)
        info = DeviceInfo(instance=instance, address=address, vendor=str(iam.vendorID))
        objid = f"device,{instance}"
        try:
            services = await self._read_property(
                instance, address, objid, "protocol-services-supported"
            )
            names = set(str(services).split(";"))
            info.supports_rpm = "read-property-multiple" in names
            info.supports_cov = "subscribe-cov" in names
        except DriverError as exc:
            log.warning("device %s : services supportés illisibles (%s)", instance, exc)
        if not info.supports_rpm:
            self._rpm_unsupported.add(instance)
        try:
            props = await self._read_many(
                instance, address, {("device", instance): ("object-name", "model-name")}
            )
            found = props.get(("device", instance), {})
            info.name = str(found["object-name"]) if "object-name" in found else None
            info.model = str(found["model-name"]) if "model-name" in found else None
        except DriverError as exc:
            log.warning("device %s : nom illisible (%s)", instance, exc)
        return info

    async def _object_list(self, device: DeviceInfo) -> list[ObjectKey]:
        instance, address = device.instance, device.address
        objid = f"device,{instance}"
        try:
            listing = await self._read_property(instance, address, objid, "object-list")
            return [(str(o[0]), int(o[1])) for o in listing]
        except RequestRejected as exc:
            # Segmentation non supportée : lecture élément par élément (array-index).
            log.info(
                "device %s : object-list complète refusée (%s), lecture par index", instance, exc
            )
        count = int(await self._read_property(instance, address, objid, "object-list", 0))
        keys: list[ObjectKey] = []
        for index in range(1, count + 1):
            entry = await self._read_property(instance, address, objid, "object-list", index)
            keys.append((str(entry[0]), int(entry[1])))
        return keys

    async def describe(self, device: DeviceInfo) -> list[ObjectInfo]:
        """Inventaire des objets importables d'un device, avec leur valeur courante."""
        wanted = [key for key in await self._object_list(device) if key[0] in IMPORTED_TYPES]
        log.debug("device %s : %d objets à importer", device.instance, len(wanted))
        objects: list[ObjectInfo] = []
        for chunk in _chunks(wanted, self._polling.batch_size):
            request = {key: _discovery_props(key[0]) for key in chunk}
            props = await self._read_many(device.instance, device.address, request)
            for object_type, number in chunk:
                found = props.get((object_type, number), {})
                state_text = found.get("state-text")
                objects.append(
                    ObjectInfo(
                        object_type=object_type,
                        object_instance=number,
                        name=str(found.get("object-name", f"{object_type},{number}")),
                        description=str(found["description"]) if found.get("description") else None,
                        unit=unit_label(str(found["units"])) if "units" in found else None,
                        writable=is_writable(object_type),
                        state_text=[str(s) for s in state_text] if state_text else None,
                        value=normalize_present_value(found.get("present-value")),
                        status=normalize_status(_flags(found.get("status-flags"))).value,
                    )
                )
        return objects

    # -- lecture -----------------------------------------------------------------------

    async def read(self, points: list[PointRef]) -> list[Reading]:
        """Lit `present-value` et `status-flags` des points (tous du même device)."""
        readings: list[Reading] = []
        by_device: dict[int, list[PointRef]] = {}
        for point in points:
            by_device.setdefault(point.device_instance, []).append(point)
        for instance, refs in by_device.items():
            wanted = {(r.object_type, r.object_instance): _LIVE_PROPS for r in refs}
            props = await self._read_many(instance, refs[0].address, wanted)
            now = datetime.now(UTC)
            for ref in refs:
                found = props.get((ref.object_type, ref.object_instance))
                if not found or "present-value" not in found:
                    readings.append(Reading(ref.point_id, now, None, PointStatus.FAULT.value))
                else:
                    readings.append(
                        build_reading(
                            ref.point_id,
                            found["present-value"],
                            _flags(found.get("status-flags")),
                            now,
                        )
                    )
        return readings

    # -- écriture ------------------------------------------------------------------------

    async def write(self, point: PointRef, value: float | None, priority: int) -> WriteResult:
        """Écrit `present-value` avec une priorité ; `None` relâche la priorité (Null)."""
        if value is None:
            payload: Any = Null(())
        elif point.object_type.startswith("binary"):
            payload = "active" if value else "inactive"
        elif is_multistate(point.object_type):
            payload = int(value)
        else:
            payload = float(value)
        objid = f"{point.object_type},{point.object_instance}"
        try:
            result = await self._call(
                point.device_instance,
                lambda: self.app.write_property(
                    Address(point.address), objid, "present-value", payload, priority=priority
                ),
            )
        except DriverError as exc:
            return WriteResult(False, str(exc))
        if isinstance(result, ErrorRejectAbortNack):
            return WriteResult(False, str(result))
        return WriteResult(True)

    # -- COV -------------------------------------------------------------------------------

    async def subscribe_cov(self, point: PointRef, lifetime_s: int) -> None:
        """Abonne (ou renouvelle l'abonnement) à un point ; lève `DriverError` en cas d'échec."""
        process_id = self._process_ids.setdefault(point.point_id, len(self._process_ids) + 1)
        self._cov_points[(point.address, process_id)] = point
        request = SubscribeCOVRequest(
            subscriberProcessIdentifier=process_id,
            monitoredObjectIdentifier=ObjectIdentifier((point.object_type, point.object_instance)),
            issueConfirmedNotifications=self._cov.confirmed,
            lifetime=lifetime_s,
            destination=Address(point.address),
        )
        response = await self._call(point.device_instance, lambda: self.app.request(request))
        if isinstance(response, ErrorRejectAbortNack):
            raise _convert_error(response)

    async def _on_cov(self, apdu: Any) -> None:
        point = self._cov_points.get((str(apdu.pduSource), int(apdu.subscriberProcessIdentifier)))
        if point is None:
            return
        object_class = get_vendor_info(0).get_object_class(ObjectType(point.object_type))
        values: dict[str, Any] = {}
        for entry in apdu.listOfValues:
            name = str(entry.propertyIdentifier)
            if name in _LIVE_PROPS:
                property_type = object_class.get_property_type(entry.propertyIdentifier)
                values[name] = entry.value.cast_out(property_type)
        if "present-value" not in values:
            return
        reading = build_reading(
            point.point_id, values["present-value"], _flags(values.get("status-flags"))
        )
        for callback in self._callbacks:
            await callback(reading)

    async def _on_event(self, apdu: Any) -> None:
        notice = EventNotice(
            device_instance=int(apdu.initiatingDeviceIdentifier[1]),
            object_type=str(apdu.eventObjectIdentifier[0]),
            object_instance=int(apdu.eventObjectIdentifier[1]),
            to_state=str(apdu.toState),
            from_state=str(apdu.fromState) if apdu.fromState is not None else None,
            message=str(apdu.messageText) if apdu.messageText is not None else None,
        )
        for callback in self._event_callbacks:
            await callback(notice)
