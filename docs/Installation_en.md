# STEP2IBRA V0.5 Preview — Installation

[中文版](V0_5_INSTALLATION_zh.md) · [Release notes and usage](V0_5_RELEASE_NOTES_en.md)

## Requirements and download

The verified platform is Windows x64, FreeCAD 1.1.3 and a separate 64-bit Python 3.11 with `venv` and `pip`. FreeCAD, Python, OCP and Kratos binaries are not included. Other platforms have not been validated.

Download **STEP2IBRA-V0.5preview-20260927.zip** from the [V0.5 Preview release](https://github.com/adkl1117/STEP2IBRA/releases/tag/V0.5preview). Extract it to a stable writable directory. Open PowerShell in the extracted `STEP2IBRA-V0.5preview` directory, containing `requirements-backend.txt` and `scripts`. Do not install backend packages into FreeCAD's embedded Python.

## Prepare the backend

```powershell
py -3.11 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-backend.txt
& '.\.venv\Scripts\python.exe' freecad/Mod/KratosIBRA/backend/check_environment.py
```

The environment check must succeed with `"passed": true`. Keep this environment at its configured location. The requirements include Kratos IGA, Structural Mechanics and Linear Solvers applications, OCP, NumPy and h5py. No Kratos source build is needed. Internet access is required for the package downloads. Use the absolute Python 3.11 executable path if `py` is unavailable; environment activation is unnecessary.

## Install into an independent preview profile

Close FreeCAD. To keep V0.2 installed, use a dedicated V0.5 profile rather than the existing user directory. Run the following from the extracted directory:

```powershell
$previewProfile = Join-Path (Get-Location) 'profile-v05'
New-Item -ItemType Directory -Force -Path $previewProfile | Out-Null
& '.\.venv\Scripts\python.exe' scripts/install_workbench.py --freecad-user-dir $previewProfile
$env:FREECAD_USER_HOME = $previewProfile
# Replace this example with the path to your FreeCAD.exe:
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe' -u "$previewProfile\user.cfg" -s "$previewProfile\system.cfg"
```

Repeat the last three lines (profile variable, environment variable and FreeCAD launch) in a new PowerShell session to enter this profile again. `FREECAD_USER_HOME` applies to that shell and its child processes. The explicit `-u` and `-s` arguments keep preview settings separate. FreeCAD may also discover workbenches installed in its program directory: do not install another KratosIBRA/KratosIGA copy there. In the FreeCAD Python console, check `FreeCAD.getUserAppDataDir()` and `kratos_iga.__file__` after activating the workbench; they should identify the preview profile.

Alternatively, obtain your normal user directory with `print(FreeCAD.getUserAppDataDir())` in FreeCAD's Python console, close FreeCAD, then pass that exact directory to the installer. The installer refuses to overwrite an existing `Mod/KratosIBRA`. Back up an older plugin outside every active `Mod` directory before upgrading; retain projects and results. Do not replace the V0.2 installation if you want both versions available.

## Start and verify

1. Select **IBRA Preprocessor for Kratos** in FreeCAD.
2. Choose **English** or **中文** at the top of the plugin panel.
3. Open **Output → Analysis and Output** and select **Check Backend Environment**. If needed, review Backend Python Executable, Conversion Pipeline Directory and Working Directory, then save the backend configuration.
4. Open `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json` from the extracted package, or the corresponding file under the installed `Mod/KratosIBRA/examples` directory.
5. Follow the [release notes and usage instructions](V0_5_RELEASE_NOTES_en.md) for geometry inspection, mouse picking, export, initialization, solution and native VTKHDF result display.

The installer copies the entire workbench and writes profile-specific `backend_config.json`. Existing `profile-v05/IBRA/backend.json` overrides take precedence: review them if reusing a profile. Moving the download requires recreating the virtual environment and updating the backend executable path. Keep each project together with its relative STEP/profile files.

## Troubleshooting and removal

- Missing workbench or old UI: check the selected user directory, `Mod/KratosIBRA/InitGui.py` and `kratos_iga.__file__`; remove duplicate modules from active search directories after backing them up.
- Missing backend application: rerun the environment check with the separate environment's Python and install every requirement.
- Geometry-change warning: reimport and review/rebind assignments; do not bypass invalidation protection.
- Failed solve: inspect the run report and backend logs, constraints, units, section thickness and interface decisions. Successful initialization alone is not a successful analysis.

To remove the preview, close FreeCAD and remove only its `profile-v05/Mod/KratosIBRA` directory. Preserve projects and `IBRA-work` results. V0.2's Git tag and original branch are independent of this installation.
