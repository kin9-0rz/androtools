from enum import Enum
from time import sleep

from func_timeout import FunctionTimedOut, func_set_timeout


@func_set_timeout(1)
def hello():
    print("1")
    sleep(2)
    print(2)


if __name__ == "__main__":
    try:
        hello()
    except FunctionTimedOut:
        print("timeout")
