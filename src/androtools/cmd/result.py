from dataclasses import dataclass


@dataclass
class CmdResult:
    output: str
    error: str
    exit_code: int | None = None
    """
    子进程退出码。**不是**「命令成功了吗」的答案。

    雷电的退出码语义不统一且被截断：删不存在的实例返回 -617，到了命令行是
    4294966295（unsigned），而 `add` 成功时的退出码本身就是**新实例的 index**。
    它的报错还写在 stdout 上。所以这个库一律靠回读实际效果判成败，
    别把 exit_code == 0 当成功。
    """

    def __post_init__(self):
        self.output = self.output.strip()
        self.error = self.error.strip()

    def __str__(self) -> str:
        output = self.output
        if len(self.output) > 1000:
            output = self.output[:1000]
        return f"{'-' * 40 + ' Output ' + '-' * 40}\n{output}\n{'-' * 40 + ' Error ' + '-' * 40}\n{self.error}"

    def __repr__(self) -> str:
        return f"{'-' * 40 + ' Output ' + '-' * 40}\n{self.output}\n{'-' * 40 + ' Error ' + '-' * 40}\n{self.error}"

    def contain(self, txt: str) -> bool:
        return txt in self.output or txt in self.error

    def error_contain(self, txt: str) -> bool:
        return txt in self.error

    def output_contain(self, txt: str) -> bool:
        return txt in self.output

    def output_equal(self, txt: str) -> bool:
        """stdout 是否**恰好**等于 txt。

        不要用 contain 代替：「device offline」包含 device，但它是离线状态，不是已连接。
        """
        return self.output == txt

    def has_error(self) -> bool:
        return self.error != ""
