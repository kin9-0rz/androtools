# - tools/bin
#     - sdkmanager
#     - apkanalyzer
#     - avdmanager
#     - uiautomatorvirewer
#     - monkeyrunner
import shutil

from androtools.cmd import CMD


class SDKManager(CMD):
    def __init__(self, path=shutil.which("sdkmanager")):
        super().__init__(path)
