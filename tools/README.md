# 准备 alice-tools

Releases 的 Windows 便携安装器已包含 alice.exe，无需执行下面的步骤。以下说明适用于源码版和网页导出的角色／势力包。

从上游官方 Releases 下载 Windows 命令行版本：

https://github.com/nunuhara/alice-tools/releases

解压后，将 alice.exe 和下载包要求的依赖文件一起放在此目录，使 `tools/alice.exe` 存在。保留下载包内的许可证。可以在项目根目录用 `tools\alice.exe --help` 检查是否能运行。

公开 Git 源码仓库和角色／势力导出包不附带该二进制文件。打包版通过 Releases 分发，附带 GPL v2 许可及对应源码；详见根目录 THIRD_PARTY_NOTICES.md。
