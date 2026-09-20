"""Un device BACnet simulé : application bacpypes3 + points + défauts."""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass

from bacpypes3.apdu import (
    AbortPDU,
    ConfirmedRequestPDU,
    Error,
    ReadPropertyMultipleRequest,
    ReadPropertyRequest,
    RejectPDU,
    SubscribeCOVRequest,
)
from bacpypes3.app import Application
from bacpypes3.basetypes import PropertyIdentifier
from bacpypes3.local.analog import (
    AnalogInputObject,
    AnalogOutputObject,
    AnalogValueObjectCmd,
)
from bacpypes3.local.binary import BinaryInputObject, BinaryOutputObject, BinaryValueObject
from bacpypes3.local.cmd import Commandable
from bacpypes3.local.cov import GenericCriteria
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.multistate import MultiStateValueObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.object import Object
from bacpypes3.primitivedata import ObjectIdentifier

from bacnet_sim.faults import Faults


class StateValueObject(MultiStateValueObject):  # type: ignore[misc]
    """Multi-state value avec COV sur tout changement d'etat (pas d'increment)."""

    _cov_criteria = GenericCriteria


class CommandableBinaryValue(Commandable, BinaryValueObject):  # type: ignore[misc]
    """Binary value commandable, construit sur l'objet local pour disposer du COV."""


# Répartition pour 200 points : (type d'objet, nombre).
POINT_MIX: tuple[tuple[str, int], ...] = (
    ("analog-input", 50),
    ("analog-output", 20),
    ("analog-value", 40),
    ("binary-input", 30),
    ("binary-output", 10),
    ("binary-value", 20),
    ("multi-state-value", 30),
)

UNITS = ("degrees-celsius", "percent", "kilowatts", "pascals", "percent-relative-humidity")
STATE_TEXT = ["Arret", "Reduit", "Confort", "Boost"]


def mix_for(count: int) -> list[tuple[str, int]]:
    """Adapte la répartition à `count` points (le reste va aux entrées analogiques)."""
    total = sum(n for _, n in POINT_MIX)
    mix = [(kind, count * n // total) for kind, n in POINT_MIX]
    mix[0] = (mix[0][0], mix[0][1] + count - sum(n for _, n in mix))
    return mix


class SimApplication(Application):  # type: ignore[misc]
    """Application bacpypes3 qui applique les défauts injectés."""

    faults: Faults

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.faults = Faults()

    async def indication(self, apdu: object) -> None:
        faults = self.faults
        if faults.muted:
            return
        if faults.latency_s > 0:
            await asyncio.sleep(faults.latency_s)
        if isinstance(apdu, ConfirmedRequestPDU):
            reply = self._faulty_reply(apdu)
            if reply is not None:
                await self.response(reply)
                return
        await super().indication(apdu)

    def _faulty_reply(self, apdu: ConfirmedRequestPDU) -> object | None:
        faults = self.faults
        if faults.error_response:
            return Error(
                service_choice=apdu.apduService,
                errorClass="device",
                errorCode="operationalProblem",
                context=apdu,
            )
        if (faults.no_rpm and isinstance(apdu, ReadPropertyMultipleRequest)) or (
            faults.no_cov and isinstance(apdu, SubscribeCOVRequest)
        ):
            return RejectPDU(reason="unrecognizedService", context=apdu)
        if (
            faults.no_segmentation
            and isinstance(apdu, ReadPropertyRequest)
            and apdu.propertyIdentifier == PropertyIdentifier.objectList
            and apdu.propertyArrayIndex is None
        ):
            return AbortPDU(srv=True, reason="segmentationNotSupported", context=apdu)
        return None

    def request(self, apdu: object) -> object:
        # Un device muet n'émet plus rien (notamment plus de notifications COV).
        if self.faults.muted:
            future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
            future.set_result(None)
            return future
        return super().request(apdu)

    def get_services_supported(self) -> object:
        services = super().get_services_supported()
        if self.faults.no_rpm:
            services[services.readPropertyMultiple] = 0
        if self.faults.no_cov:
            services[services.subscribeCOV] = 0
        return services


@dataclass
class SimPoint:
    """Point simulé animé par `SimDevice.animate()`."""

    obj: Object
    kind: str
    base: float = 0.0
    amplitude: float = 0.0
    phase: float = 0.0


class SimDevice:
    """Device simulé écoutant sur `host:port`."""

    def __init__(
        self,
        instance: int,
        host: str,
        port: int,
        points_per_device: int = 200,
        seed: int | None = None,
    ) -> None:
        self.instance = instance
        self.host = host
        self.port = port
        self.address = f"{host}:{port}"
        self._random = random.Random(seed if seed is not None else instance)
        self._points: list[SimPoint] = []
        self.app = self._build(points_per_device)
        self._task: asyncio.Task[None] | None = None

    @property
    def faults(self) -> Faults:
        return self.app.faults

    def get_object(self, object_type: str, instance: int) -> Object:
        obj = self.app.get_object_id(ObjectIdentifier((object_type, instance)))
        if obj is None:
            raise KeyError(f"{object_type},{instance}")
        return obj

    def _build(self, points_per_device: int) -> SimApplication:
        device = DeviceObject(
            objectIdentifier=("device", self.instance),
            objectName=f"SIM-{self.instance}",
            vendorIdentifier=999,
            modelName="BACnet-Sim",
        )
        port = NetworkPortObject(
            self.address,
            objectIdentifier=("network-port", 1),
            objectName="NetworkPort-1",
            networkNumber=0,
            networkNumberQuality="unknown",
        )
        objects: list[Object] = [device, port]
        counters: dict[str, int] = {}
        for kind, count in mix_for(points_per_device):
            for _ in range(count):
                counters[kind] = counters.get(kind, 0) + 1
                obj = self._make_object(kind, counters[kind])
                objects.append(obj)
        app = SimApplication.from_object_list(objects)
        assert isinstance(app, SimApplication)
        return app

    def _make_object(self, kind: str, number: int) -> Object:
        rnd = self._random
        short = "".join(part[0] for part in kind.split("-")).upper()
        name = f"D{self.instance}-{short}-{number:03d}"
        common = {
            "objectIdentifier": (kind, number),
            "objectName": name,
            "description": f"Point simule {short} {number}",
            "statusFlags": [0, 0, 0, 0],
        }
        if kind.startswith("analog"):
            unit = UNITS[(number - 1) % len(UNITS)]
            base = rnd.uniform(10.0, 30.0)
            common.update(units=unit, presentValue=round(base, 2))
            if kind == "analog-input":
                obj: Object = AnalogInputObject(covIncrement=0.5, **common)
                self._points.append(
                    SimPoint(obj, kind, base, rnd.uniform(0.5, 5.0), rnd.uniform(0, 6.28))
                )
            elif kind == "analog-output":
                obj = AnalogOutputObject(covIncrement=0.5, **common)
            else:
                obj = AnalogValueObjectCmd(covIncrement=0.5, **common)
            return obj
        if kind.startswith("binary"):
            common.update(presentValue="inactive")
            if kind == "binary-input":
                obj = BinaryInputObject(**common)
                self._points.append(SimPoint(obj, kind))
            elif kind == "binary-output":
                obj = BinaryOutputObject(**common)
            else:
                obj = CommandableBinaryValue(**common)
            return obj
        obj = StateValueObject(
            numberOfStates=len(STATE_TEXT),
            stateText=STATE_TEXT,
            **{**common, "presentValue": 1},
        )
        self._points.append(SimPoint(obj, kind))
        return obj

    async def animate(self, tick_s: float = 2.0) -> None:
        """Fait évoluer les entrées : sinusoïdes bruitées, bascules aléatoires."""
        rnd = self._random
        start = asyncio.get_event_loop().time()
        while True:
            await asyncio.sleep(tick_s)
            elapsed = asyncio.get_event_loop().time() - start
            for point in self._points:
                if point.kind == "analog-input":
                    value = point.base + point.amplitude * math.sin(elapsed / 30 + point.phase)
                    point.obj.presentValue = round(value + rnd.gauss(0, 0.05), 2)
                elif point.kind == "binary-input" and rnd.random() < 0.02:
                    flipped = "inactive" if point.obj.presentValue == "active" else "active"
                    point.obj.presentValue = flipped
                elif point.kind == "multi-state-value" and rnd.random() < 0.02:
                    point.obj.presentValue = rnd.randint(1, len(STATE_TEXT))

    def start(self, animate: bool = True, tick_s: float = 2.0) -> None:
        if animate:
            self._task = asyncio.create_task(self.animate(tick_s))

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self.app.close()
