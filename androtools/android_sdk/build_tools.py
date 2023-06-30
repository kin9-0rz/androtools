import logging
import shutil
import subprocess
import sys
from enum import Enum
from time import sleep


class AAPT:
    def __init__(self):
        self.aapt_path = shutil.which("aapt")


class AAPT2:
    def __init__(self):
        self.aapt_path = shutil.which("aapt2")


class ApkSigner:
    def __init__(self):
        self.bin_path = shutil.which("apksigner")


class DexDump:
    def __init__(self):
        self.bin_path = shutil.which("dexdump")
