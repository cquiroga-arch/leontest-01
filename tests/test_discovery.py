from vagscan.transport.discovery import ProbeResult, autodetect_elm327


def _fake_probe(responders: dict[str, str]):
    """Builds a probe function that 'answers' only for the given devices."""
    seen: list[str] = []

    def probe(device, *, baudrate=38400, timeout=1.5):
        seen.append(device)
        if device in responders:
            return ProbeResult(device=device, responded=True, identity=responders[device])
        return ProbeResult(device=device, responded=False, identity="", error="no reply")

    probe.seen = seen  # type: ignore[attr-defined]
    return probe


def test_autodetect_returns_the_port_that_answers(monkeypatch):
    from vagscan.transport import discovery

    monkeypatch.setattr(
        discovery,
        "list_serial_ports",
        lambda: [
            discovery.PortInfo("COM1", "Some mouse", "", False),
            discovery.PortInfo("COM5", "Standard Serial over Bluetooth link", "", False),
        ],
    )
    probe = _fake_probe({"COM5": "ELM327 v1.5"})
    found = autodetect_elm327(probe=probe)

    assert found is not None
    assert found.device == "COM5"
    assert found.identity == "ELM327 v1.5"


def test_autodetect_returns_none_when_nothing_answers(monkeypatch):
    from vagscan.transport import discovery

    monkeypatch.setattr(
        discovery, "list_serial_ports", lambda: [discovery.PortInfo("COM1", "Some mouse", "", False)]
    )
    assert autodetect_elm327(probe=_fake_probe({})) is None


def test_autodetect_probes_likely_named_ports_first(monkeypatch):
    """On Windows every paired Bluetooth device looks the same, but when a
    description *is* informative we should try it before the others so
    detection is fast."""
    from vagscan.transport import discovery

    monkeypatch.setattr(
        discovery,
        "list_serial_ports",
        lambda: [
            discovery.PortInfo("COM1", "Printer", "", False),
            discovery.PortInfo("COM3", "Other device", "", False),
            discovery.PortInfo("COM9", "OBDII adapter", "", True),
        ],
    )
    probe = _fake_probe({"COM9": "ELM327 v2.1"})
    found = autodetect_elm327(probe=probe)

    assert found.device == "COM9"
    assert probe.seen[0] == "COM9"  # tried the likely one first


def test_candidate_ports_includes_rfcomm_devices_not_enumerated(monkeypatch):
    """On Linux a paired Bluetooth SPP adapter lives at /dev/rfcommN, which
    some distros don't list as a serial port - missing it would make the
    adapter invisible on exactly the setup this tool targets."""
    from vagscan.transport import discovery

    monkeypatch.setattr(discovery, "list_serial_ports", lambda: [discovery.PortInfo("/dev/ttyS0", "builtin", "", False)])
    monkeypatch.setattr(discovery.glob, "glob", lambda pattern: ["/dev/rfcomm0"])

    assert discovery.candidate_ports() == ["/dev/ttyS0", "/dev/rfcomm0"]


def test_candidate_ports_does_not_duplicate_an_already_listed_rfcomm(monkeypatch):
    from vagscan.transport import discovery

    monkeypatch.setattr(
        discovery, "list_serial_ports", lambda: [discovery.PortInfo("/dev/rfcomm0", "Bluetooth link", "", False)]
    )
    monkeypatch.setattr(discovery.glob, "glob", lambda pattern: ["/dev/rfcomm0"])

    assert discovery.candidate_ports() == ["/dev/rfcomm0"]


def test_autodetect_finds_a_clone_that_only_answers_at_9600(monkeypatch):
    """The blue clones often enumerate at 9600, not 38400. Probing 38400 only
    made such an adapter look absent even with its COM port present."""
    from vagscan.transport import discovery

    monkeypatch.setattr(
        discovery, "list_serial_ports", lambda: [discovery.PortInfo("COM5", "Bluetooth link", "", False)]
    )
    monkeypatch.setattr(discovery.glob, "glob", lambda pattern: [])

    def probe(device, *, baudrate=38400, timeout=1.5):
        if baudrate == 9600:
            return ProbeResult(device=device, responded=True, identity="ELM327 v1.5", baudrate=9600)
        # Wrong baud: a real ELM327 spits garbage bytes, not silence - which
        # is the signal to keep trying other rates on this port.
        return ProbeResult(device=device, responded=False, identity="xyz", error="garbage", saw_data=True)

    found = autodetect_elm327(probe=probe)
    assert found is not None
    assert found.device == "COM5"
    assert found.baudrate == 9600


def test_autodetect_skips_remaining_bauds_on_a_silent_port(monkeypatch):
    """A port that returns nothing at the first baud is dead/unpowered -
    don't burn a timeout on it at every other baud."""
    from vagscan.transport import discovery

    monkeypatch.setattr(
        discovery, "list_serial_ports", lambda: [discovery.PortInfo("COM3", "nothing here", "", False)]
    )
    monkeypatch.setattr(discovery.glob, "glob", lambda pattern: [])
    attempts = []

    def probe(device, *, baudrate=38400, timeout=1.5):
        attempts.append(baudrate)
        return ProbeResult(device=device, responded=False, identity="", error="silence", saw_data=False)

    assert autodetect_elm327(probe=probe) is None
    assert attempts == [38400]  # only the first baud was tried on the silent port


def test_autodetect_keeps_going_past_a_port_that_errors(monkeypatch):
    from vagscan.transport import discovery

    monkeypatch.setattr(
        discovery,
        "list_serial_ports",
        lambda: [
            discovery.PortInfo("COM1", "Busy port", "", False),
            discovery.PortInfo("COM5", "Bluetooth link", "", False),
        ],
    )

    def probe(device, *, baudrate=38400, timeout=1.5):
        if device == "COM1":
            return ProbeResult(device=device, responded=False, identity="", error="PermissionError: port busy")
        return ProbeResult(device=device, responded=True, identity="ELM327 v1.5")

    found = autodetect_elm327(probe=probe)
    assert found.device == "COM5"
