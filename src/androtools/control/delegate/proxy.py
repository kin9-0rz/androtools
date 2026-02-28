from androtools.control.delegate import ABCDelegate


class ProxyDelegate(ABCDelegate):
    """设置代理"""

    def set_http_proxy(self, host: str):
        cmd = ["settings", "put", "global", "http_proxy", host]
        self.adb.adb_shell(cmd)

    def on(self):
        host = f"{self.adb.device.info.gateway}:{self.adb.device.info.proxy_port}"
        self.set_http_proxy(host)

    def off(self):
        self.set_http_proxy(":0")
