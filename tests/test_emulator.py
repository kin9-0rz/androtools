import pytest
from androtools.android_sdk.emulator import Emulator


@pytest.fixture
def emu() -> Emulator:
    return Emulator()


def test_list_avds(emu: Emulator):
    out, err = emu.list_avds()
    assert len(out) > 0
    assert err == ""
