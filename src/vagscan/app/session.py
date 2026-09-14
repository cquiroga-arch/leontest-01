"""Ties the transport/driver/service layers together into one object the
CLI (or, later, a mobile front-end reusing this same package) drives."""
from __future__ import annotations

from dataclasses import dataclass

from vagscan.dtc_db import DtcDatabase
from vagscan.elm327 import AdapterCapabilities, ELM327Driver, OBDProtocol
from vagscan.obd2 import OBD2Service
from vagscan.transport import SerialTransport
from vagscan.transport.serial_port import SerialTransportConfig
from vagscan.vag.bus_logger import BusLogger
from vagscan.vag.kwp2000 import KWP2000Client, OdometerReading
from vagscan.vag.tp20 import TP20Client

CLUSTER_ADDRESS = "17"  # instrument cluster, where the odometer lives


@dataclass
class VagscanSession:
    driver: ELM327Driver
    obd2: OBD2Service
    tp20: TP20Client
    bus_logger: BusLogger
    dtc_db: DtcDatabase
    capabilities: AdapterCapabilities | None = None

    def ensure_capabilities(self) -> AdapterCapabilities:
        """Probe the adapter once per session and remember the answer -
        probing runs ATMA, which isn't something to repeat on every scan."""
        if self.capabilities is None:
            self.capabilities = self.driver.probe_capabilities()
        return self.capabilities

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

    def read_cluster_odometer(self) -> OdometerReading:
        """Read the real mileage stored in the instrument cluster. READ-ONLY.

        Goes through the same experimental TP2.0 path (and the same
        listen-before-transmit safety interlock) as everything VAG-specific,
        so on an adapter without working monitor mode it will refuse rather
        than transmit blind. Raises if the cluster can't be reached or the
        channel isn't confirmed - there is intentionally no fallback that
        invents a number.
        """
        frames = self.tp20.discover_channel(CLUSTER_ADDRESS, timeout=1.0)
        if not frames:
            raise RuntimeError(
                f"El cuadro (módulo {CLUSTER_ADDRESS}) no respondió. En el clon ELM327 esto es lo "
                "esperado: sin modo monitor la capa VAG queda deshabilitada."
            )
        # discover_channel returns candidate frames; the real tx/rx IDs have
        # to be confirmed against this car before a read can be trusted. We
        # don't guess them - see the TP2.0 module docstring.
        raise NotImplementedError(
            "El cuadro respondió a la sonda, pero los IDs de canal TP2.0 de este auto todavía no están "
            "confirmados, así que no se lee a ciegas. Capturá el intercambio con la pestaña 'VAG avanzado' "
            "y confirmá tx/rx antes de leer el kilometraje."
        )

    def read_cluster_odometer_on(self, tx_id: int, rx_id: int) -> OdometerReading:
        """Read the odometer once you've confirmed the cluster's TP2.0 tx/rx
        CAN IDs (via the VAG-advanced discovery). READ-ONLY."""
        channel = self.tp20.open_channel(CLUSTER_ADDRESS, tx_id, rx_id)
        kwp = KWP2000Client(self.tp20, channel)
        try:
            kwp.start_diagnostic_session()
        except Exception:
            pass  # some clusters read measuring blocks without an explicit session
        return kwp.read_odometer()

    def close(self) -> None:
        self.driver.close()

    def __enter__(self) -> "VagscanSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
