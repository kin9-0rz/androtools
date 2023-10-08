class A:
    def __init__(self) -> None:
        print("A")

    def hello(self):
        print("hello")


class B(A):
    def __init__(self, a) -> None:
        print(a)
        super().__init__("")


b = B("B")
b.hello()
