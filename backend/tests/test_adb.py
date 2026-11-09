from app import adb


def test_discover_multiple_devices(monkeypatch):
    monkeypatch.setattr(adb, "run_adb", lambda *a, **kw: "List of devices attached\nRF8TEST001 device usb:1-1 product:dm3q model:SM_S908E device:dm3q\n192.168.1.20:5555 unauthorized\n")
    devices = adb.connected_devices()
    assert len(devices) == 2
    assert devices[0]["serial"] == "RF8TEST001"
    assert devices[1]["status"] == "unauthorized"
