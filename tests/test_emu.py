import sys

from androtools.device.ld import LDConsole


def test_ld():
    print(sys.platform)
    if sys.platform not in {"win32", "win64"}:
        return
    LDConsole()
