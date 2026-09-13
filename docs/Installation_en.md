# Installation and first analysis

These instructions start from a fresh download. They do not require the author's FreeCAD installation, virtual environment, conversion cache or Kratos source checkout.

## 1. Prerequisites

- Windows x64; FreeCAD 1.1.3 is the verified GUI version.
- A **64-bit Python 3.11** installation with `venv` and `pip`, separate from FreeCAD's Python.
- Internet access to download backend wheels from PyPI.

Do not use FreeCAD's embedded Python to install the backend packages. The workbench uses FreeCAD's native Part and PySide modules, while conversion and solution run in a separate process.

Other operating systems have not been tested for this distribution. The Python scripts are portable, but the required OCP and Kratos wheels must exist for the chosen platform and Python version.

## 2. Download this branch

On GitHub, select `freecad-ibra-preprocessor`, then **Code → Download ZIP**. Extract it to a stable writable directory. Open PowerShell in the extracted repository root, where `README.md` and `requirements-backend.txt` are located. All commands below run from that directory.

Alternatively use `git clone --branch freecad-ibra-preprocessor https://github.com/adkl1117/STEP2IBRA.git` and change into `STEP2IBRA`.

## 3. Create and verify the backend

```powershell
py -3.11 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-backend.txt
& '.\.venv\Scripts\python.exe' freecad/Mod/KratosIBRA/backend/check_environment.py
```

If the `py` launcher is unavailable, use the absolute path to your Python 3.11 executable for the first command. Activation is unnecessary: every command explicitly selects the environment's Python. Core packages are version-pinned; the complete Windows environment used during validation is also recorded in `requirements-windows-lock.txt` and can replace the requirements file in the installation command for exact reproduction.

The check must finish successfully and print `"passed": true`. It verifies Python architecture and imports OCP, Kratos and its required applications. `requirements-backend.txt` includes the structural mechanics and linear solver applications; installing only Kratos core is insufficient.

Keep the `.venv` directory at this location. Its executable path is stored during installation. You do not need to compile the downloaded Kratos source.

## 4. Find the correct FreeCAD user directory

Open FreeCAD's Python console using **View → Panels → Python console**, then run:

```python
print(FreeCAD.getUserAppDataDir())
```

Copy the returned path. It is installation-specific, so do not assume the directory of `FreeCAD.exe` is the user directory. Close FreeCAD before installing the workbench.

## 5. Install the workbench

In PowerShell, replace the example user-data path with the exact value from the console:

```powershell
& '.\.venv\Scripts\python.exe' scripts/install_workbench.py --freecad-user-dir 'C:\Users\YourName\AppData\Roaming\FreeCAD'
```

The installer checks the backend, then copies the complete workbench into `<user-directory>/Mod/KratosIBRA`. It includes conversion source, documentation and examples, and writes an installation-specific `backend_config.json`. No administrator privileges are needed when the selected directory is user-writable.

An existing `KratosIBRA` directory is preserved rather than overwritten. For an upgrade, save your projects, close FreeCAD, move the previous workbench folder **outside the Mod directory**, and rerun the installer. Also remove or move an older `KratosIGA` workbench from any other active module directory to avoid registering two workbenches with the same Python package and class. Keep your project and result directories.

## 6. Start and verify the GUI

1. Restart FreeCAD and select **IBRA Preprocessor for Kratos**.
2. Open **Output → Analysis and Output**. Review Backend Python Executable, Conversion Pipeline Directory and Working Directory.
3. Select **Check Backend Environment**. The log must report success.
4. If you modify a path, select **Save Backend Configuration**. User overrides persist in `<user-directory>/IBRA/backend.json` and take precedence over installation defaults.
5. Return to Geometry and select **Open Cantilever Example**. Conversion runs asynchronously; a surface patch and its boundaries should appear.
6. Select **Validate Model**, then **Export Analysis Model** into a parent directory of your choice.
7. In Output, select **Initialize Exported Model**, then **Run Exported Analysis**.

The example has a clamped root and an end line load. Its expected displacement magnitude is 0.02 mm. The complete workflow and model assumptions are explained in the [user guide](User_Guide_en.md).

## 7. Reproduce the example without the GUI

From the repository root:

```powershell
& '.\.venv\Scripts\python.exe' scripts/run_example.py --output output/cantilever --solve
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

Use a new or empty output folder; the script preserves existing models. `example_verification.json` records the result and analytical error. These commands use only files in this download and the new backend environment.

## 8. Manual installation and relocation

If you do not use the installer, copy `freecad/Mod/KratosIBRA` to `<user-directory>/Mod/KratosIBRA`. After restarting FreeCAD, configure the backend executable manually. The pipeline directory is `<user-directory>/Mod/KratosIBRA/backend`; choose a writable working directory and save the settings.

Saved projects use relative STEP/profile paths where possible. Moving a project and its source files together preserves those references. Different Windows drives require absolute paths. Recreate the virtual environment if moving the repository, then update the backend executable in the GUI. The installed plugin is copied, not linked to the repository, but the selected backend executable must remain available.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Workbench is absent | Confirm the exact user directory and `<Mod>/KratosIBRA/InitGui.py`; restart FreeCAD and inspect its Report view. |
| Duplicate workbench or old interface | Move the previous `KratosIGA`/`KratosIBRA` module out of every active Mod directory. |
| Missing backend or module | Run the environment check with `.venv/Scripts/python.exe`; install all requirements and save the correct GUI path. |
| No matching wheel | Confirm 64-bit Python 3.11 and supported platform; do not mix wheels from other Python versions. |
| STEP/profile checksum mismatch | Reimport the changed source and review assignments; do not edit checksums to bypass the check. |
| Shell rejected for C⁰ continuity | Degree elevation is insufficient; use valid reparameterization or a physically justified membrane model. |
| Output directory is not empty | Export to a new directory. |
| Initialization succeeds but solve fails | Inspect backend logs, constraints, rigid-body modes, materials and interface penalties. |

Uninstall by closing FreeCAD and removing only `<user-directory>/Mod/KratosIBRA`. Preserve `<user-directory>/IBRA-work`, projects and results if needed.
