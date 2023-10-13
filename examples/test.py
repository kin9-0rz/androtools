import abc


# class Animal(metaclass=abc.ABCMeta):  # 同一类事物:动物
class Animal(abc.ABC):  # 同一类事物:动物
    @abc.abstractmethod
    def talk(self):
        pass


class Cat(Animal):  # 动物的形态之一:猫
    def talk(self):
        print("say miaomiao")

    def hello(self):
        print("hello")


class Dog(Animal):  # 动物的形态之二:狗
    def talk(self):
        print("say wangwang")


class Pig(Animal):  # 动物的形态之三:猪
    def talk(self):
        print("say aoao")


c = Cat()
d = Dog()
p = Pig()


def func(obj: Animal):
    obj.talk()
    obj.hello()


func(c)
func(d)
func(p)
