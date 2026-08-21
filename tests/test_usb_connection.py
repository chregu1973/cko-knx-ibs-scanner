from __future__ import annotations

from cko_ibs import usb_connection


def test_knx_usb_hid_single_report_roundtrip() -> None:
    cemi = bytes.fromhex("2900bce000001101000081")
    reports = usb_connection._split_hid_reports(cemi)

    assert len(reports) == 1
    assert len(reports[0]) == 64
    assert usb_connection._HIDAssembler().feed(reports[0]) == cemi


def test_knx_usb_hid_multi_report_roundtrip() -> None:
    cemi = bytes(range(150))
    reports = usb_connection._split_hid_reports(cemi)
    assembler = usb_connection._HIDAssembler()

    assert len(reports) == 3
    assert all(len(report) == 64 for report in reports)
    assert [assembler.feed(report) for report in reports] == [None, None, cemi]


def test_discovers_siemens_oci702(monkeypatch) -> None:
    monkeypatch.setattr(
        usb_connection.hid,
        "enumerate",
        lambda: [
            {
                "path": b"oci702",
                "vendor_id": 0x0908,
                "product_id": 0x02DC,
                "manufacturer_string": "Siemens HVAC",
                "product_string": "OCI702 USB",
                "serial_number": "4711",
            },
            {"path": b"keyboard", "vendor_id": 0x1234, "product_id": 0x5678},
        ],
    )

    devices = usb_connection.discover_usb_devices()

    assert len(devices) == 1
    assert devices[0].name == "Siemens OCI702 USB (Siemens HVAC)"
    assert devices[0].to_dict()["serial_number"] == "4711"
