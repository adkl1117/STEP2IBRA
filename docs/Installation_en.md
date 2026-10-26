# STEP2IBRA V0.5 Preview: installation guide

[中文版](V0_5_INSTALLATION_zh.md) · [Release notes and usage](V0_5_RELEASE_NOTES_en.md)

## 1. Software requirements and distribution

The validated configuration is Windows x64, FreeCAD 1.1.3 and a separate 64-bit Python 3.11 environment with `venv` and `pip`. The distribution contains the workbench and STEP conversion source. FreeCAD, Python, OCP and Kratos binaries require separate installation. Compatibility with other platforms has not been established.

Download **STEP2IBRA-V0.5preview-20260927.zip** from the [V0.5 Preview release](https://github.com/adkl1117/STEP2IBRA/releases/tag/V0.5preview) and extract it to a stable, writable location. Open PowerShell in the extracted `STEP2IBRA-V0.5preview` directory, which contains `requirements-backend.txt` and `scripts`. Install backend dependencies in the separate Python environment; FreeCAD uses its own embedded Python for the graphical workbench.

## 2. Backend environment

```powershell
py -3.11 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-backend.txt
& '.\.venv\Scripts\python.exe' freecad/Mod/KratosIBRA/backend/check_environment.py
```

The check must report `"passed": true`. The requirements specify the Kratos IGA, Structural Mechanics and Linear Solvers applications, OCP, NumPy and h5py. Package installation requires internet access; a Kratos source build is unnecessary for this configuration. If `py` is unavailable, substitute the absolute path to a Python 3.11 executable. The commands invoke the environment directly, so activation is unnecessary.

Keep the virtual environment at its configured location. The installer records its executable path.

## 3. Installation in a separate FreeCAD profile

Close FreeCAD before installation. A dedicated V0.5 user directory allows an existing V0.2 installation and its settings to be retained. Run these commands from the extracted package directory:

```powershell
$previewProfile = Join-Path (Get-Location) 'profile-v05'
New-Item -ItemType Directory -Force -Path $previewProfile | Out-Null
& '.\.venv\Scripts\python.exe' scripts/install_workbench.py --freecad-user-dir $previewProfile
$env:FREECAD_USER_HOME = $previewProfile
# Replace this example with the path to your FreeCAD.exe:
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe' -u "$previewProfile\user.cfg" -s "$previewProfile\system.cfg"
```

For subsequent launches, open PowerShell in the same extracted directory, set `$previewProfile` and `FREECAD_USER_HOME` again, and execute the FreeCAD launch command. The environment variable applies to that shell and its child processes. The `-u` and `-s` arguments select the profile's user and system configuration files.

FreeCAD can also discover workbenches in its program directory. Check for another KratosIBRA/KratosIGA installation there if an unexpected version appears. After activating the workbench, inspect `FreeCAD.getUserAppDataDir()` and `kratos_iga.__file__` in the FreeCAD Python console. Both paths should correspond to the intended preview profile.

To install in an existing user profile, first obtain its exact path with `print(FreeCAD.getUserAppDataDir())`, then close FreeCAD and pass that path to the installer. The installer preserves an existing `Mod/KratosIBRA` by refusing to overwrite it. Before an upgrade, back up the previous plugin outside all active `Mod` directories and retain its projects and results. Use separate profiles when both V0.2 and V0.5 are required.

## 4. Configuration and initial verification

1. Select **IBRA Preprocessor for Kratos** in FreeCAD.
2. Select **English** or **中文** at the top of the plugin panel.
3. In **Output → Analysis and Output**, run **Check Backend Environment**. Review Backend Python Executable, Conversion Pipeline Directory and Working Directory if the check fails, then save the corrected backend configuration.
4. Open `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json` from the extracted package, or the corresponding file in the installed `Mod/KratosIBRA/examples` directory.
5. Follow the [usage guide](V0_5_RELEASE_NOTES_en.md) to inspect the model, assign conditions, export, initialize and solve it, and display native VTKHDF results.

The installer copies the complete workbench and writes a profile-specific `backend_config.json`. Existing user settings in `profile-v05/IBRA/backend.json` take precedence over those defaults; review them when reusing a profile.

If the extracted distribution is moved, recreate the virtual environment and update the backend executable path. Keep project files together with their relatively referenced STEP and conversion-profile files.

## 5. Diagnosis and removal

| Observation | Check |
| --- | --- |
| Workbench missing or an earlier interface appears | Verify the selected profile, `Mod/KratosIBRA/InitGui.py` and `kratos_iga.__file__`. Back up duplicate modules before moving them out of active search directories. |
| Backend application missing | Run the environment check with the separate environment's Python and install all listed requirements. |
| Geometry-change warning | Reimport the geometry, review the assignments and complete the required rebinding. Retain the invalidation protection. |
| Analysis fails | Inspect the run report and backend log, then check constraints, units, thickness and interface decisions. Initialization and solution are separate verification stages. |

To remove V0.5, close FreeCAD and remove only `profile-v05/Mod/KratosIBRA`. Retain project files and `IBRA-work` results. This local removal does not affect the V0.2 Git tag or its original branch.
