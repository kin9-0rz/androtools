import androtools


def main():
    androtools.enable_console_logging()
    androtools.logger.debug("Hello androtools!")
    print("Hello World")


if __name__ == "__main__":
    exit(main())
