import os
import pytest

from androtools.android_sdk.avdmanager import AVDManager, CreateAVD

fixtures_path = os.path.join(os.path.dirname(__file__), "fixtures")

is_err = False


@pytest.fixture
def is_ok():
    ca = CreateAVD()
    ca.name("test")
    ca.package("system-images;android-22;google_apis;x86")
    ca.device("pixel_xl")
    ca.force()

    error = ca.run().error
    if "Error: Package path is not valid." in error:
        return False
    return True


def test_list_avd(is_ok):
    if not is_ok:
        return
    am = AVDManager()
    result = am.list_avd().output
    assert "test" in result


def test_delete_avd(is_ok):
    if not is_ok:
        return

    am = AVDManager()
    result = am.delete_avd("test").output
    assert "deleted" in result

    result = am.list_avd().output
    assert "test" not in result
