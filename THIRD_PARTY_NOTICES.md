# 第三方与原游戏内容

## alice-tools

本项目通过独立命令行程序调用 nunuhara 的 alice-tools 进行图像格式转换及资源处理。

- 项目与源码：https://github.com/nunuhara/alice-tools
- 官方下载：https://github.com/nunuhara/alice-tools/releases
- 许可：GNU GPL version 2，见 https://github.com/nunuhara/alice-tools/blob/master/LICENSE

源码仓库与网页导出的角色／势力包不附带 alice.exe，使用者自行从上游获取。Releases 的 Windows 便携安装器附带上游 0.13.0 的独立 alice.exe，保留其 COPYING.txt 与上游附带许可证，并在 `third-party/alice-tools-0.13.0-source.zip` 中提供对应版本完整项目源码、构建说明和 libsys4 子模块源码；本项目不修改该工具。

其静态依赖 libiconv 的 LGPL v2.1 许可见 `tools/licenses/libiconv/COPYING.LIB`，源码随包放在 `third-party/libiconv-1.17.tar.gz`，来自 GNU 官方发布：https://ftp.gnu.org/pub/gnu/libiconv/ 。其他依赖的许可保留在 `tools/licenses/`。

## Pillow

Python 图像处理依赖 Pillow。源码版通过 pip 安装，Windows 便携版内置该依赖，其许可（含 wheel 内第三方许可）位于 `third-party/Pillow-LICENSE`。

- 项目：https://github.com/python-pillow/Pillow
- 许可：https://github.com/python-pillow/Pillow/blob/main/LICENSE

## 打包运行环境

Windows 便携版内置 Python 与 Tcl/Tk，由 PyInstaller 打包。Python 许可见 `third-party/Python-LICENSE.txt`；Tcl 许可见 `third-party/Tcl-LICENSE.txt`，Tk 许可见 `_internal/_tk_data/license.terms`。PyInstaller 许可与 bootloader 分发例外见 `third-party/PyInstaller-COPYING.txt`。具体构建版本与资源数量记录在 `BUILD_INFO.json`。

- Python：https://www.python.org/
- Tcl/Tk：https://www.tcl.tk/
- PyInstaller：https://pyinstaller.org/

## 原游戏与演示图

《战国兰斯》及原游戏中的角色、图片、文字等内容，其权利归相应权利人。本项目为非官方同人资源替换项目，与原游戏权利人无隶属关系。

宣传图保留原图对比用于效果说明；不提供可独立提取的原版游戏资源库。使用者的游戏原包、提取工作区、文本和安装备份不属于本项目发布内容，不应提交到此仓库。
