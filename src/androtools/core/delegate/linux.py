from androtools.core.delegate import ABCDelegate


class LinuxCommand(ABCDelegate):
    def rm(self, path: str, isDir: bool = False, force: bool = False):
        """删除文件

        Args:
            path (str): 文件路径
            force (bool, optional): 是否强制删除，默认否. Defaults to False.
        """
        cmd = ["rm"]
        if isDir:
            cmd.append("-r")
        if force:
            cmd.append("-f")
        cmd.append(path)
        self.adb.adb_shell(cmd)

    def ls(self, path: str):
        cmd = ["ls", path]
        result = self.adb.adb_shell(cmd)
        if result.has_error():
            return result
        return result.output

    def mkdir(self, path):
        self.adb.adb_shell(["mkdir", path])

    def ps(self):
        return self.adb.adb_shell(["ps", "-A"]).output

    def pidof(self, process_name):
        result = self.adb.adb_shell(["pidof", process_name])
        if result.contain("not found"):
            lines = self.adb.adb_shell(["ps"]).output.splitlines()
            for line in lines:
                parts = line.split()
                if parts[-1] == process_name:
                    return int(parts[1])
            return -1

        if result.output == "":
            return -1
        return int(result.output)

    def killall(self, process_name):
        return self.adb.adb_shell(["killall", process_name]).output

    def kill(self, pid):
        cmd = ["kill", str(pid)]
        self.adb.adb_shell(cmd)
