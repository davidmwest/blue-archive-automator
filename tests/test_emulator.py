"""Emulator recovery is opt-in, bounded, and limited to the named instance."""

import pytest

from ba_automator import emulator
from ba_automator.adb import DeviceError
from ba_automator.emulator import configured_instance, ensure_ready, player_pids


class Device:
    def __init__(self, *, connects=(True,), writable=(True,), booted=(True,)):
        self.connects, self.writable, self.booted = list(connects), list(writable), list(booted)
        self.calls = []

    @staticmethod
    def _next(values):
        return values.pop(0) if len(values) > 1 else values[0]

    def connect(self):
        self.calls.append('connect')
        if not self._next(self.connects):
            raise DeviceError('cannot connect to 127.0.0.1:5595')

    def storage_writable(self):
        return self._next(self.writable)

    def boot_completed(self):
        return self._next(self.booted)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.mark.parametrize('value, expected', [
    ('Pie64_4', 'Pie64_4'), (' Pie64_4 ', 'Pie64_4'), ('', None),
    ('Pie64_4; calc', None), ('"Pie64"', None),
])
def test_instance_name_must_be_a_plain_identifier(monkeypatch, value, expected):
    monkeypatch.setenv('BA_BLUESTACKS_INSTANCE', value)
    assert configured_instance() == expected


def test_healthy_device_needs_no_recovery(monkeypatch):
    monkeypatch.setenv('BA_BLUESTACKS_INSTANCE', 'Pie64_4')
    restarts = []
    assert ensure_ready(Device(), restart=restarts.append) is False
    assert restarts == []


@pytest.mark.parametrize('device, reason', [
    (Device(writable=(False,)), 'read-only'),
    (Device(connects=(False,)), 'unreachable'),
])
def test_without_a_named_instance_the_problem_is_reported(monkeypatch, device, reason):
    monkeypatch.delenv('BA_BLUESTACKS_INSTANCE', raising=False)
    restarts = []
    with pytest.raises(DeviceError, match=reason):
        ensure_ready(device, restart=restarts.append)
    assert restarts == []


def test_read_only_storage_restarts_the_named_instance_once(monkeypatch):
    monkeypatch.setenv('BA_BLUESTACKS_INSTANCE', 'Pie64_4')
    clock, restarts, events = Clock(), [], []
    device = Device(connects=(True, False, True), writable=(False, True), booted=(False, True))
    recovered = ensure_ready(device, lambda event, **fields: events.append((event, fields)),
                             restart=restarts.append, monotonic=clock.monotonic, sleep=clock.sleep)
    assert recovered is True and restarts == ['Pie64_4']
    assert [event for event, _ in events] == ['emulator_recovery', 'emulator_recovered']
    assert events[0][1] == {'instance': 'Pie64_4', 'reason': 'Android storage is read-only'}


def test_recovery_that_never_boots_is_bounded(monkeypatch):
    monkeypatch.setenv('BA_BLUESTACKS_INSTANCE', 'Pie64_4')
    clock, restarts = Clock(), []
    with pytest.raises(DeviceError, match='did not recover after a restart'):
        ensure_ready(Device(connects=(False,)), restart=restarts.append,
                     monotonic=clock.monotonic, sleep=clock.sleep)
    assert restarts == ['Pie64_4']
    assert emulator.BOOT_SECONDS <= clock.now <= emulator.BOOT_SECONDS + 12


def test_devices_without_a_storage_probe_are_treated_as_healthy(monkeypatch):
    monkeypatch.delenv('BA_BLUESTACKS_INSTANCE', raising=False)

    class Minimal:
        def connect(self):
            pass

    assert ensure_ready(Minimal()) is False


def test_only_the_named_instances_player_is_matched(tmp_path, monkeypatch):
    conf = tmp_path / 'bluestacks.conf'
    conf.write_text('bst.instance.Pie64_2.display_name="BlueStacks HBR"\n'
                    'bst.instance.Pie64_4.display_name="Bluestacks Blue Archive"\n'
                    'bst.instance.Pie64_4.dpi="640"\n', encoding='utf-8')
    monkeypatch.setenv('BA_BLUESTACKS_CONF', str(conf))
    listing = ('"Image Name","PID","Session Name","Session#","Mem Usage","Status","User Name","CPU Time","Window Title"\r\n'
               '"HD-Player.exe","39024","Console","1","15,570,000 K","Running","PC\\user","17:37:54","BlueStacks HBR"\r\n'
               '"HD-Player.exe","4242","Console","1","951,000 K","Running","PC\\user","6:14:17","Bluestacks Blue Archive"\r\n')

    def run(command, **kwargs):
        assert command[:2] == ['tasklist', '/v']
        return type('Result', (), {'stdout': listing, 'returncode': 0})()

    assert player_pids('Pie64_4', run) == [4242]
    assert player_pids('Pie64_2', run) == [39024]
    assert player_pids('Pie64_9', run) == []          # unknown instance: nothing to stop
