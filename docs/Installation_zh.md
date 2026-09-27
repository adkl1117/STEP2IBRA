# STEP2IBRA V0.5 Preview — 安装指南

[English](V0_5_INSTALLATION_en.md) · [版本说明与使用说明](V0_5_RELEASE_NOTES_zh.md)

## 环境与下载

已验证环境为 Windows x64、FreeCAD 1.1.3 和独立的 64 位 Python 3.11（包含 `venv`、`pip`）。发布包不包含 FreeCAD、Python、OCP 或 Kratos 二进制程序，其他平台尚未验证。

从 [V0.5 Preview 发布页](https://github.com/adkl1117/STEP2IBRA/releases/tag/V0.5preview)下载 **STEP2IBRA-V0.5preview-20260927.zip**，解压到固定且可写的目录。在解压后的 `STEP2IBRA-V0.5preview` 目录打开 PowerShell；此目录应包含 `requirements-backend.txt` 和 `scripts`。不要向 FreeCAD 内置 Python 安装后端依赖。

## 准备后端

```powershell
py -3.11 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-backend.txt
& '.\.venv\Scripts\python.exe' freecad/Mod/KratosIBRA/backend/check_environment.py
```

环境检查必须成功并输出 `"passed": true`。保留虚拟环境的位置；安装后插件会记录它的 Python 路径。依赖包含 Kratos IGA、Structural Mechanics、Linear Solvers、OCP、NumPy 和 h5py，无需编译 Kratos 源码。下载依赖需要网络。没有 `py` 时，第一条命令改用 Python 3.11 可执行文件的绝对路径；不需要激活虚拟环境。

## 安装到独立的预览用户目录

关闭 FreeCAD。为保留已安装的 V0.2，建议使用独立 V0.5 用户目录。在解压目录执行：

```powershell
$previewProfile = Join-Path (Get-Location) 'profile-v05'
New-Item -ItemType Directory -Force -Path $previewProfile | Out-Null
& '.\.venv\Scripts\python.exe' scripts/install_workbench.py --freecad-user-dir $previewProfile
$env:FREECAD_USER_HOME = $previewProfile
# 将示例路径替换为自己机器上的 FreeCAD.exe：
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe' -u "$previewProfile\user.cfg" -s "$previewProfile\system.cfg"
```

下次在新的 PowerShell 窗口启动时，重新设置 `$previewProfile`、`FREECAD_USER_HOME`，并执行 FreeCAD 启动命令。该环境变量只影响当前窗口及其子进程；`-u`、`-s` 分别指定独立用户和系统设置文件。FreeCAD 还可能发现程序目录中的工作台：不要在那里重复安装 KratosIBRA/KratosIGA。激活工作台后，在 FreeCAD Python 控制台检查 `FreeCAD.getUserAppDataDir()` 和 `kratos_iga.__file__`，确认使用的是预览用户目录。

也可在 FreeCAD Python 控制台运行 `print(FreeCAD.getUserAppDataDir())`，关闭 FreeCAD 后，将返回的真实目录传给安装器。安装器遇到已有 `Mod/KratosIBRA` 会拒绝覆盖；升级前应把旧插件备份到所有有效 `Mod` 目录之外，保留项目和结果。若需要两版并存，不要替换 V0.2 的安装目录。

## 启动与核对

1. 在 FreeCAD 中选择 **IBRA Preprocessor for Kratos** 工作台。
2. 在插件面板顶部选择 **English** 或 **中文**。
3. 打开 **输出 → 分析与输出**，执行 **检查后端环境**。必要时核对后端 Python、转换流水线目录和工作目录，再保存后端配置。
4. 打开解压目录中的 `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json`，也可使用安装后的 `Mod/KratosIBRA/examples` 中的对应文件。
5. 按[版本说明与使用说明](V0_5_RELEASE_NOTES_zh.md)操作几何查看、鼠标点选、导出、初始化、求解和原生 VTKHDF 结果查看。

安装器复制完整工作台，并生成当前用户目录专用的 `backend_config.json`。复用用户目录时，已有 `profile-v05/IBRA/backend.json` 优先于默认配置，需要核对。移动解压目录后应重建虚拟环境并更新后端 Python 路径。移动项目时，保持相对引用的 STEP 和配置文件一起移动。

## 排错与卸载

- 工作台缺失或界面仍旧：检查用户目录、`Mod/KratosIBRA/InitGui.py` 和 `kratos_iga.__file__`；备份后移走有效搜索目录中的重复模块。
- 后端缺少应用：用独立环境的 Python 重跑环境检查，并安装全部依赖。
- 几何变更警告：重新导入、审核并重新绑定目标，不要绕过失效保护。
- 求解失败：检查运行报告和后端日志、约束、单位、厚度及接口决策。初始化成功不代表求解成功。

卸载时关闭 FreeCAD，只移除预览目录的 `profile-v05/Mod/KratosIBRA`。保留项目和 `IBRA-work` 结果。V0.2 的 Git 标签和原发布分支与此安装互相独立。
