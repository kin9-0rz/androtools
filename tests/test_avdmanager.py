import os

from androtools.android_sdk.avdmanager import AVDManager, CreateAVD

fixtures_path = os.path.join(os.path.dirname(__file__), "fixtures")


def test_create_avd():
    ca = CreateAVD()
    print(ca.bin_path)
    ca.name("test")
    ca.package("system-images;android-22;google_apis;x86")
    ca.device("pixel_xl")
    ca.force()

    assert "100%" in ca.run()[0]
    assert "Usage:" in ca.run()[0]


def test_list_avd():
    am = AVDManager()
    result = am.list_avd()
    assert "test" in result[0]


def test_delete_avd():
    am = AVDManager()
    result = am.delete_avd("test")
    assert "deleted" in result[0]

    result = am.list_avd()
    assert "test" not in result[0]
