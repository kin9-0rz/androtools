import sys

import pytest

from androtools.core.ld import LDConsole

pytestmark = pytest.mark.integration


def test_ld():
    if sys.platform not in {"win32", "win64"}:
        pytest.skip("ldconsole.exe 只在 Windows 上存在")
    LDConsole()
