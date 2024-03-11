from time import sleep

from func_timeout import FunctionTimedOut, func_timeout


def hello():
    print("1")
    sleep(2)
    print(2)


if __name__ == "__main__":
    print("0")
    try:
        func_timeout(1, hello)
    except FunctionTimedOut as e:
        print(e)
    print("3")
