# STEP2IBRA V0.5 Preview：安装指南

[English version](V0_5_INSTALLATION_en.md) · [版本说明与使用说明](V0_5_RELEASE_NOTES_zh.md)

## 1. 软件要求与发布包

已验证的配置为 Windows x64、FreeCAD 1.1.3，以及带有 `venv` 和 `pip` 的独立 64 位 Python 3.11 环境。发布包包含工作台与 STEP 转换源码；FreeCAD、Python、OCP 和 Kratos 的二进制依赖需另行安装。其他平台的兼容性尚未验证。

从 [V0.5 Preview 发布页](https://github.com/adkl1117/STEP2IBRA/releases/tag/V0.5preview)下载 **STEP2IBRA-V0.5preview-20260927.zip**，解压到位置稳定且可写的目录。在包含 `requirements-backend.txt` 和 `scripts` 的 `STEP2IBRA-V0.5preview` 解压目录中打开 PowerShell。后端依赖安装在独立 Python 环境中；图形工作台使用 FreeCAD 自带的 Python。

## 2. 配置后端环境

```powershell
py -3.11 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-backend.txt
& '.\.venv\Scripts\python.exe' freecad/Mod/KratosIBRA/backend/check_environment.py
```

检查结果应包含 `"passed": true`。依赖清单包含 Kratos IGA、Structural Mechanics、Linear Solvers 应用，以及 OCP、NumPy 和 h5py。下载依赖需要联网；该配置无需从源码编译 Kratos。若系统没有 `py` 命令，可改用 Python 3.11 可执行文件的绝对路径。上述命令直接调用虚拟环境，无需先激活。

虚拟环境应保留在配置时的位置，安装器会记录其 Python 可执行文件路径。

## 3. 安装到独立的 FreeCAD 用户目录

安装前关闭 FreeCAD。使用独立的 V0.5 用户目录，可以保留已有 V0.2 安装及其设置。在解压目录执行：

```powershell
$previewProfile = Join-Path (Get-Location) 'profile-v05'
New-Item -ItemType Directory -Force -Path $previewProfile | Out-Null
& '.\.venv\Scripts\python.exe' scripts/install_workbench.py --freecad-user-dir $previewProfile
$env:FREECAD_USER_HOME = $previewProfile
# 将示例路径替换为自己机器上的 FreeCAD.exe：
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe' -u "$previewProfile\user.cfg" -s "$previewProfile\system.cfg"
```

再次启动时，在同一解压目录打开 PowerShell，重新设置 `$previewProfile` 和 `FREECAD_USER_HOME`，再执行 FreeCAD 启动命令。该环境变量作用于当前窗口及其子进程；`-u`、`-s` 分别指定该用户目录的用户和系统配置文件。

FreeCAD 还会搜索程序目录中的工作台。若出现非预期版本，应检查其中是否存在另一份 KratosIBRA/KratosIGA。激活工作台后，在 FreeCAD Python 控制台核对 `FreeCAD.getUserAppDataDir()` 和 `kratos_iga.__file__`，确认它们对应预期的预览用户目录。

如需安装到已有用户目录，先通过 `print(FreeCAD.getUserAppDataDir())` 获取准确路径，关闭 FreeCAD 后将该路径传给安装器。安装器发现已有 `Mod/KratosIBRA` 时会拒绝覆盖。升级前，应将旧插件备份到所有有效 `Mod` 目录之外，并保留项目与结果。需要同时使用 V0.2 和 V0.5 时，应分别设置用户目录。

## 4. 配置核对与首次运行

1. 在 FreeCAD 中选择 **IBRA Preprocessor for Kratos** 工作台。
2. 在插件面板顶部选择 **English** 或 **中文**。
3. 在**输出 → 分析与输出**执行**检查后端环境**。检查失败时，核对后端 Python、转换流水线目录和工作目录，再保存修正后的后端配置。
4. 打开解压目录中的 `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json`，或安装目录 `Mod/KratosIBRA/examples` 中的对应文件。
5. 按[使用说明](V0_5_RELEASE_NOTES_zh.md)检查模型、设置条件、导出、初始化与求解，并查看原生 VTKHDF 结果。

安装器复制完整工作台，并写入该用户目录专用的 `backend_config.json`。已有的 `profile-v05/IBRA/backend.json` 用户设置优先于这些默认值，复用用户目录时应核对其内容。

移动解压目录后，应重建虚拟环境并更新后端 Python 路径。移动项目时，保持项目文件及其相对引用的 STEP、转换配置文件一同移动。

## 5. 问题诊断与卸载

| 现象 | 检查方法 |
| --- | --- |
| 工作台缺失或显示旧版界面 | 核对用户目录、`Mod/KratosIBRA/InitGui.py` 和 `kratos_iga.__file__`；先备份重复模块，再将其移出有效搜索目录。 |
| 缺少后端应用 | 使用独立环境的 Python 运行环境检查，并安装清单中的全部依赖。 |
| 提示几何发生变化 | 重新导入几何，审核条件赋值并完成所需的重新绑定，保留失效保护。 |
| 求解失败 | 检查运行报告和后端日志，再核对约束、单位、厚度及接口决策。初始化与求解分别对应不同验证阶段。 |

卸载 V0.5 时，关闭 FreeCAD，只移除 `profile-v05/Mod/KratosIBRA`，保留项目文件和 `IBRA-work` 结果。该本地操作不影响 V0.2 的 Git 标签及原分支。
