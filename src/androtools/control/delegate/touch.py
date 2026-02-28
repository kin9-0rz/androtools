from dataclasses import dataclass

from func_timeout import FunctionTimedOut, func_timeout

from androtools.control.delegate import ABCDelegate
from androtools.device.constants import KeyEvent


@dataclass
class Point:
    x: int
    y: int

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"


class TouchController(ABCDelegate):
    """触摸控制器"""

    def tap(self, x: int, y: int):
        cmd = ["input", "tap", str(x), str(y)]
        try:
            self.adb.adb_shell(cmd)
            return True
        except Exception:
            return False

    def tap_point(self, point: Point):
        return self.tap(point.x, point.y)

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: int = 300,
    ):
        cmd = [
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(end_x),
            str(end_y),
            str(duration),
        ]
        self.adb.adb_shell(cmd)

    def swipe_point(self, start: Point, end: Point):
        self.swipe(start.x, start.y, end.x, end.y)

    def long_press(self, x: int, y: int, duration: int = 1000):
        self.swipe(x, y, x, y, duration)

    def input_keyevent(self, keyevent: KeyEvent):
        cmd = ["input", "keyevent", str(keyevent.value)]
        try:
            # 输入事件5s必定超时
            self.adb.adb_shell(cmd, 5)
            return True
        except Exception:
            return False

    def home(self) -> bool:
        return self.input_keyevent(KeyEvent.KEYCODE_HOME)

    def back(self):
        self.input_keyevent(KeyEvent.KEYCODE_BACK)

    def delete(self):
        self.input_keyevent(KeyEvent.KEYCODE_DEL)

    # TODO: 可能不如 ADBKeyBoard 效果
    def input_text(self, txt: str):
        cmd = ["input", "text", txt]
        self.adb.adb_shell(cmd)

    def is_crashed(self):
        """5秒没响应，则定义为设备崩溃"""
        try:
            return func_timeout(
                5, self.input_keyevent, args=(KeyEvent.KEYCODE_ALT_LEFT,)
            )
        except FunctionTimedOut:
            return True
