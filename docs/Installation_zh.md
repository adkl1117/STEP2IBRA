# 安装与首次分析

本说明从全新下载开始，不需要作者电脑上的 FreeCAD 安装、虚拟环境、转换缓存或 Kratos 源码目录。

## 1. 准备环境

- 已验证平台为 Windows 64 位，界面使用 FreeCAD 1.1.3。
- 单独安装 **64 位 Python 3.11**，包含虚拟环境和包管理功能。
- 能连接软件包下载源。

不要在 FreeCAD 内置的 Python 中安装后端依赖。插件使用 FreeCAD 自带的几何与界面模块，转换和求解由外部独立进程完成。其他操作系统尚未验证；即使脚本可运行，仍需有对应的 OCP 与 Kratos 二进制软件包。

## 2. 下载分支

在 GitHub 选择 `freecad-ibra-preprocessor` 分支，通过代码菜单下载压缩包，解压到稳定、可写的目录。在包含 `README.md` 和 `requirements-backend.txt` 的仓库根目录打开 PowerShell。以下命令均从该目录执行。

也可使用：

```text
git clone --branch freecad-ibra-preprocessor https://github.com/adkl1117/STEP2IBRA.git
```

随后进入下载得到的 `STEP2IBRA` 目录。

## 3. 创建并检查后端

```powershell
py -3.11 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-backend.txt
& '.\.venv\Scripts\python.exe' freecad/Mod/KratosIBRA/backend/check_environment.py
```

如果没有 `py` 启动器，将首条命令中的 `py -3.11` 替换为本机 Python 3.11 可执行文件的绝对路径。不必激活虚拟环境，每条命令都明确指定解释器。核心依赖已经锁定版本；完整验证环境记录在 `requirements-windows-lock.txt`，需要精确复现时，可在安装命令中用它替换依赖文件。

检查程序应成功结束，并输出 `"passed": true`。它检查解释器架构，并实际导入 OCP、Kratos 及所需应用。只安装 Kratos 核心不够，依赖文件已包含结构力学和线性求解器应用。

安装后保持 `.venv` 目录位置不变，其解释器路径会写入插件配置。此流程不需要编译 Kratos 源码。

## 4. 确认 FreeCAD 用户目录

打开 FreeCAD 的“视图→面板→Python 控制台”，输入：

```python
print(FreeCAD.getUserAppDataDir())
```

复制输出的目录路径，不要把程序安装目录误当作用户目录。关闭 FreeCAD，再进行安装。

## 5. 安装插件

在 PowerShell 中执行以下命令，将示例用户目录替换成上一步的实际输出：

```powershell
& '.\.venv\Scripts\python.exe' scripts/install_workbench.py --freecad-user-dir 'C:\Users\YourName\AppData\Roaming\FreeCAD'
```

安装器先检查后端，再复制完整插件到“用户目录下的 `Mod/KratosIBRA`”。转换器源码、说明和示例一起复制，并生成当前安装专用的 `backend_config.json`。目标目录可由当前用户写入时，不需要管理员权限。

安装器不会覆盖已有插件。升级时先保存项目、关闭 FreeCAD，将旧的 `KratosIBRA` 文件夹移到 **Mod 目录之外**，再重新安装。如果其他模块目录中仍有旧版 `KratosIGA`，也应移出，避免同名类和模块重复注册。保留分析项目和结果文件夹。

## 6. 检查界面并运行示例

1. 重启 FreeCAD，选择 **IBRA Preprocessor for Kratos** 工作台。
2. 进入左侧最后一页，核对后端解释器、转换器目录和工作目录。
3. 点击后端环境检查按钮，确认日志报告成功。
4. 修改路径后，点击后端配置保存按钮。配置写入用户目录下的 `IBRA/backend.json`，其优先级高于安装默认值。
5. 返回几何页，点击悬臂示例打开按钮；等待异步转换后，三维视口应显示曲面片和边界。
6. 校验模型，再选择父目录导出完整算例。
7. 在输出页先初始化最近导出的模型，再运行分析。

示例根部固支、端部承受线载荷，预期位移绝对值为 0.02 mm。物理假设与操作细节见[中文使用说明](User_Guide_zh.md)。

## 7. 不启动界面的复现方法

在仓库根目录执行：

```powershell
& '.\.venv\Scripts\python.exe' scripts/run_example.py --output output/cantilever --solve
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

输出目录必须是新目录或空目录，以保护已有算例。`example_verification.json` 记录计算结果与解析误差。命令只依赖本次下载的源码、示例和新建后端环境。

## 8. 手动安装与迁移

不使用安装脚本时，手动将 `freecad/Mod/KratosIBRA` 复制到用户目录的 `Mod/KratosIBRA`。重启后在输出页填写后端解释器路径；转换器目录选择已安装插件内的 `backend`，工作目录选择可写位置，然后保存配置。

项目尽可能以相对路径引用 STEP 和转换配置；一起移动项目与源文件，可保留相对关系。Windows 不同盘符之间必须使用绝对路径。迁移仓库后应重新创建虚拟环境，并在界面更新解释器路径。安装的插件是独立副本，但配置的后端解释器仍须保持可用。

## 常见问题

| 问题 | 处理方法 |
| --- | --- |
| 工作台未出现 | 核对用户目录及 `Mod/KratosIBRA/InitGui.py`，重启后查看 FreeCAD 报告窗口。 |
| 重复工作台或仍显示旧界面 | 将其他活动模块目录中的旧版插件移出 Mod。 |
| 找不到后端或模块 | 用虚拟环境解释器运行环境检查，完整安装依赖并保存正确路径。 |
| 没有匹配的软件包 | 确认 64 位 Python 3.11 和支持的平台，不混用其他版本的二进制包。 |
| 文件校验和不一致 | 重新导入并检查条件分配，不修改校验和绕过验证。 |
| 壳因 C⁰ 连续性被拒绝 | 升阶不足以修复，需要有效的重新参数化，或物理上合理的膜模型。 |
| 输出目录非空 | 选择新的输出目录。 |
| 初始化成功但求解失败 | 检查日志、约束、刚体模态、材料和接口罚因子。 |

卸载时关闭 FreeCAD，只删除用户目录下的 `Mod/KratosIBRA`。需要保留的工作目录、分析项目和结果不要一并删除。
