import pytest
from fastapi.testclient import TestClient

from cko_ibs import sco_import
from cko_ibs.bus_connection import bus_connection
from cko_ibs.main import app


def _xml() -> bytes:
    """Aufgebaut wie ein ETS-Export («Telegramme speichern»): cEMI in RawData, UTC mit 7 Nachkommastellen."""
    frames = [
        ("2026-09-28T20:23:24.0111375Z", "L_Data.ind", "2900BCE011320C1A010080"),          # 1 Bit
        ("2026-09-28T20:23:25.6470176Z", "L_Data.con", "2E00B4E011FE5014010000"),          # Lesen
        ("2026-09-28T20:24:03.2140000Z", "L_Data.ind", "2900BCE0CCFE500A07008001100202 0000"),
        ("2026-09-28T20:24:03.6900000Z", "L_Data.con", "2E00BCE011FE500A0700800104A1000000"),
        ("2026-09-28T20:24:04.0000000Z", "L_Data.ind", "2900BCE011FE110107034000"),         # Individuell
    ]
    rows = "\n".join(
        f'  <Telegram Timestamp="{time}" ConnectionName="IP" Service="{service}" FrameFormat="CommonEmi" RawData="{raw.replace(" ", "")}" />'
        for time, service, raw in frames)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<CommunicationLog xmlns="http://knx.org/xml/telegrams/01">\n'
            f"{rows}\n</CommunicationLog>\n").encode()


def _csv() -> bytes:
    header = ["Number", "Time", "Service", "Flags", "Priority", "SourceAddress", "Source", "SourceDescription",
              "Destination", "DestinationAddress", "DestinationDescription", "RoutingCounter", "Type",
              "DatapointName", "Info", "Iack", "MediumInfo", "GPASector", "GPAPriority", "GPADecodedInfo", "RawData"]
    rows = [
        ["1", "28.09.2026 22:52:23,456", "vom Bus", "-", "Niedrig", "1.1.254", "-", "-", "10/0/10", "SCO", "-", "6",
         "GroupValue_Write", "-", "01 04 25 00 00 00 | Log Number=1", "-", "-", "1", "Automatikbefehl",
         "Fahrbefehl: Wipp Auf", "29 00 BC E0 11 FE 50 0A 07 00 80 01 04 25 00 00 00"],
        ["2", "28.09.2026 22:52:28,628", "vom Bus", "-", "Niedrig", "1.1.1", "JAX-6", "-", "10/0/21", "Winkel", "-",
         "6", "GroupValue_Write", "DPT_Scaling", "$FF | 100,0%", "-", "-", "-", "-", "-",
         "29 00 BC E0 11 01 50 15 02 00 80 FF"],
        ["3", "28.09.2026 22:53:55,283", "vom Bus", "-", "Niedrig", "1.1.254", "-", "-", "10/0/10", "SCO", "-", "6",
         "GroupValue_Write", "-", "01 14 00 00 00 00 | Log Number=1", "-", "-", "1", "-",
         "Lokalbedienung: Lang Auf", "29 00 BC E0 11 FE 50 0A 07 00 80 01 14 00 00 00 00"],
    ]
    lines = ["\t".join(f'"{value}"' for value in row) for row in [header, *rows]]
    return ("﻿" + "\n".join(lines) + "\n").encode("utf-8")


@pytest.fixture(autouse=True)
def clean_sco_state():
    bus_connection.sco_log.clear()
    bus_connection.set_sco_addresses([])
    yield
    bus_connection.sco_log.clear()
    bus_connection.set_sco_addresses([])


def test_parse_cemi_group_write_with_six_bytes() -> None:
    telegram = sco_import.parse_cemi(bytes.fromhex("2900BCE011FE500A0700800110010100 00".replace(" ", "")))
    assert telegram["source"] == "1.1.254"
    assert telegram["destination"] == "10/0/10"
    assert telegram["service"] == "GroupValueWrite"
    assert telegram["data"].hex(" ").upper() == "01 10 01 01 00 00"


def test_parse_cemi_ignores_reads_and_individual_destinations() -> None:
    assert sco_import.parse_cemi(bytes.fromhex("2E00B4E011FE5014010000")) is None
    assert sco_import.parse_cemi(bytes.fromhex("2900BCE011FE110107034000")) is None


def test_parse_cemi_small_value_lives_in_apci_byte() -> None:
    telegram = sco_import.parse_cemi(bytes.fromhex("2900BCE011320C18010081"))
    assert telegram["destination"] == "1/4/24"
    assert telegram["data"] == b"\x01"


def test_xml_import_keeps_only_six_byte_group_telegrams() -> None:
    frames = sco_import.read_recording("test.xml", _xml())
    result = sco_import.sco_entries("test.xml", frames, {"10/0/10": "SCO Zentrale"})
    assert result["frames"] == 5
    assert result["group_telegrams"] == 3
    assert [entry["hex"] for entry in result["entries"]] == ["01 10 02 02 00 00", "01 04 A1 00 00 00"]
    first, second = result["entries"]
    assert first["source"] == "12.12.254"
    assert first["decoded"]["action"] == "Lokalbedienung sperren"
    assert first["direction"] == "Incoming"
    assert second["direction"] == "Outgoing"
    assert second["decoded"]["priority"] == "Sicherheitsbefehl"
    assert first["group_name"] == "SCO Zentrale"
    assert first["origin"] == "Import: test.xml"
    assert first["time"].startswith("2026-09-28T") and first["time"][19:23] == ".214"


def test_csv_import_from_ets_group_monitor() -> None:
    frames = sco_import.read_recording("GPA.csv", _csv())
    result = sco_import.sco_entries("GPA.csv", frames, {})
    assert result["group_telegrams"] == 3
    assert [entry["decoded"]["action"] for entry in result["entries"]] == [
        "Wipp Auf · Wippdauer vom Aktor", "Lang Auf · mit Automatiksperre löschen"]
    assert result["entries"][0]["time"].startswith("2026-09-28T22:52:23.456")
    assert result["entries"][0]["direction"] == "Incoming"


def test_csv_without_rawdata_falls_back_to_info_column() -> None:
    text = "Time;Service;SourceAddress;DestinationAddress;Info\n28.09.2026 22:52:23,456;vom Bus;1.1.254;10/0/10;01 04 A1 00 00 00 | x\n28.09.2026 22:52:24,000;vom Bus;1.1.1;10/0/21;$FF | 100%\n"
    frames = sco_import.read_recording("export.csv", text.encode("cp1252"))
    entries = sco_import.sco_entries("export.csv", frames, {})["entries"]
    assert len(entries) == 1
    assert entries[0]["destination"] == "10/0/10"
    assert entries[0]["decoded"]["priority"] == "Sicherheitsbefehl"


@pytest.mark.parametrize("content", [b"kein xml und keine spalten\n1;2\n", b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>'])
def test_invalid_files_are_rejected(content: bytes) -> None:
    with pytest.raises(sco_import.ImportError_):
        sco_import.read_recording("datei.csv", content)


def test_import_endpoint_fills_log_and_sector_overview() -> None:
    client = TestClient(app)
    bus_connection.sco_log.append({"time": "2026-09-28T20:00:00", "direction": "Outgoing", "source": "1.1.250",
                                   "destination": "10/0/10", "group_name": None, "service": "GroupValueWrite",
                                   "secure": False, "hex": "01 14 00 00 00 00",
                                   "decoded": sco_import.sco.decode(bytes.fromhex("011400000000")),
                                   "origin": "IBS-Test: Telegramm"})
    response = client.post("/api/sco/import", files={"recording": ("test.xml", _xml(), "text/xml")})
    assert response.status_code == 200
    result = response.json()
    assert result["imported"] == 2
    assert result["addresses"] == ["10/0/10"]
    assert result["size"] == 3
    origins = {row["origin"] for row in client.get("/api/sco/sectors").json()["sectors"]}
    assert origins == {"IBS-Test", "Import"}

    replaced = client.post("/api/sco/import", data={"replace": "true"},
                           files={"recording": ("GPA.csv", _csv(), "text/csv")}).json()
    assert replaced["size"] == 2
    assert all(entry["origin"] == "Import: GPA.csv" for entry in client.get("/api/sco/log").json()["entries"])


def test_import_endpoint_can_limit_to_marked_addresses() -> None:
    client = TestClient(app)
    bus_connection.set_sco_addresses(["10/0/11"])
    result = client.post("/api/sco/import", data={"only_marked": "true"},
                         files={"recording": ("test.xml", _xml(), "text/xml")}).json()
    assert result["imported"] == 0
    assert result["group_telegrams"] == 3


def test_import_endpoint_reports_unreadable_file() -> None:
    response = TestClient(app).post("/api/sco/import", files={"recording": ("x.xml", b"<kaputt", "text/xml")})
    assert response.status_code == 422
    assert "XML" in response.json()["detail"]


def test_sector_overview_groups_changing_physical_addresses() -> None:
    """ETS/Tunnel senden je Verbindung mit anderer Adresse: eine Zeile, Quellen mit Anzahl."""
    frames = [
        {"time": "2026-09-28T22:20:17.354", "service": "L_Data.ind", "raw": "2900BCE0CCFE500A0700800104A1000000"},
        {"time": "2026-09-28T22:43:09.445", "service": "L_Data.ind", "raw": "2900BCE011FE500A0700800104A1000000"},
        {"time": "2026-09-28T23:09:09.614", "service": "L_Data.ind", "raw": "2900BCE01102500A0700800104C1000000"},
    ]
    bus_connection.sco_log.extend(sco_import.sco_entries("a.xml", frames, {})["entries"])
    (row,) = TestClient(app).get("/api/sco/sectors").json()["sectors"]
    assert row["origin"] == "Import" and row["count"] == 3
    assert row["sources"] == {"12.12.254": 1, "1.1.254": 1, "1.1.2": 1}
    assert row["priority_sources"] == {"Sicherheitsbefehl": ["12.12.254", "1.1.254"], "Gefahrenbefehl": ["1.1.2"]}
    assert row["last_action"] == "obere Endlage"
