import os

import pytest

from androtools.android_sdk.build_tools import AAPT2

fixtures_path = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def aapt2():
    return AAPT2()


@pytest.fixture
def apk_path():
    return os.path.join(fixtures_path, "test.apk")


def test_dump(aapt2, apk_path):
    output, error = aapt2.run_subcmd(
        AAPT2.Dump.permissions,
        [apk_path],
    )
    assert "android.permission.INTERNET" in output
    assert error == ""
