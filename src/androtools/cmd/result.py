from dataclasses import dataclass


@dataclass
class CmdResult:
    output: str
    error: str

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
        return txt in self.output

    def has_error(self) -> bool:
        return self.error != ""
