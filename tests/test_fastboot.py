import pytest
from androtools.android_sdk.platform_tools import FastBoot

pytestmark = pytest.mark.integration


def test_help():
    FastBoot().help()