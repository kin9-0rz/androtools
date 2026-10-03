import pytest
from androtools.android_sdk.emulator import Emulator

pytestmark = pytest.mark.integration


@pytest.fixture
def emu() -> Emulator:
    return Emulator()


def test_list_avds(emu: Emulator):
    result = emu.list_avds()
    assert len(result.output) > 0
    assert result.error == ""
