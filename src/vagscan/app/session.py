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
AIRBAG_ADDRESS = "15"  # SRS / airbag module


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
        channel = self.tp20.discover_and_open(CLUSTER_ADDRESS, timeout=1.0)
        if channel is None:
            self.tp20.restore_obd_mode()
            raise RuntimeError(
                f"El cuadro (módulo {CLUSTER_ADDRESS}) no respondió, o su respuesta de canal no se pudo "
                "interpretar. En el clon ELM327 esto es lo esperado: sin modo monitor la capa VAG queda "
                "deshabilitada. Con un adaptador con modo monitor, si responde pero no abre el canal, "
                "capturá el intercambio en 'VAG avanzado' y confirmá tx/rx a mano."
            )
        return self._with_channel(channel, lambda kwp: kwp.read_odometer())

    def _with_channel(self, channel, action):
        """Run `action(kwp)` on an open channel, then always close the channel
        and return the adapter to standard OBD-II mode."""
        kwp = KWP2000Client(self.tp20, channel)
        try:
            try:
                kwp.start_diagnostic_session()
            except Exception:
                pass  # some modules answer without an explicit session
            return action(kwp)
        finally:
            self.tp20.close_channel(channel)
            self.tp20.restore_obd_mode()

    def read_cluster_odometer_on(self, tx_id: int, rx_id: int) -> OdometerReading:
        """Read the odometer once you've confirmed the cluster's TP2.0 tx/rx
        CAN IDs (via the VAG-advanced discovery). READ-ONLY."""
        channel = self.tp20.open_channel(CLUSTER_ADDRESS, tx_id, rx_id)
        return self._with_channel(channel, lambda kwp: kwp.read_odometer())

    def clear_module_faults(self, address: str) -> None:
        """Clear the stored fault memory of a VAG module (e.g. 15 = airbag).

        This is the legitimate post-repair clear a scan tool does: it erases
        the stored code so the warning light goes out. It does NOT disable
        the module or its telltale - if the fault is still physically
        present, the module sets it again and the light comes back. The
        caller (GUI) is expected to have gotten explicit human confirmation
        first, especially for the airbag module.

        Goes through the safety interlock (listen-before-transmit), so on an
        adapter without monitor mode it refuses rather than transmitting
        blind. Raises with a clear message if the module can't be reached or
        its channel can't be opened.
        """
        channel = self.tp20.discover_and_open(address, timeout=1.0)
        if channel is None:
            self.tp20.restore_obd_mode()
            raise RuntimeError(
                f"El módulo {address} no respondió, o su respuesta de canal no se pudo interpretar. "
                "Con el clon (sin modo monitor) esto es lo esperado. Con un adaptador con modo monitor, "
                "si responde pero no abre el canal, usá 'Descubrir canal' y confirmá tx/rx a mano."
            )
        self._with_channel(channel, lambda kwp: kwp.clear_diagnostic_information())

    def clear_module_faults_on(self, address: str, tx_id: int, rx_id: int) -> None:
        """Same as clear_module_faults, on a channel whose ids you confirmed."""
        channel = self.tp20.open_channel(address, tx_id, rx_id)
        self._with_channel(channel, lambda kwp: kwp.clear_diagnostic_information())

    def close(self) -> None:
        self.driver.close()

    def __enter__(self) -> "VagscanSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
