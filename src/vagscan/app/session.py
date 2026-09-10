"""Ties the transport/driver/service layers together into one object the
CLI (or, later, a mobile front-end reusing this same package) drives."""
from __future__ import annotations

from dataclasses import dataclass

from vagscan.dtc_db import DtcDatabase
from vagscan.elm327 import ELM327Driver, OBDProtocol
from vagscan.obd2 import OBD2Service
from vagscan.transport import SerialTransport
from vagscan.transport.serial_port import SerialTransportConfig
from vagscan.vag.bus_logger import BusLogger
from vagscan.vag.tp20 import TP20Client


@dataclass
class VagscanSession:
    driver: ELM327Driver
    obd2: OBD2Service
    tp20: TP20Client
    bus_logger: BusLogger
    dtc_db: DtcDatabase

    @classmethod
    def connect(cls, port: str, *, baudrate: int = 38400, protocol: OBDProtocol = OBDProtocol.AUTO) -> "VagscanSession":
        transport = SerialTransport(SerialTransportConfig(port=port, baudrate=baudrate))
        driver = ELM327Driver(transport)
        driver.initialize(protocol=protocol)
        return cls(
            driver=driver,
            obd2=OBD2Service(driver),
            tp20=TP20Client(driver),
            bus_logger=BusLogger(driver),
            dtc_db=DtcDatabase.load_default(),
        )

    def close(self) -> None:
        self.driver.close()

    def __enter__(self) -> "VagscanSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
