# Validation record

Distribution: 0.2.0. Validation date: 2026-09-13.

## Environment

- Windows x64; FreeCAD 1.1.3 with its bundled Qt/PySide.
- A new external Python 3.11.7 virtual environment with `include-system-site-packages = false`.
- Dependencies installed from `requirements-backend.txt`, without copying packages from the development environment.
- OCP 7.8.1.1; Kratos core, Iga, StructuralMechanics and LinearSolvers applications 10.4.3.
- The complete installed package set is recorded in `requirements-windows-lock.txt`.

## Checks completed

1. Backend import and version check passed in the new virtual environment.
2. The installer copied the workbench into an isolated user directory, including a path containing spaces.
3. The installed package was imported in FreeCAD's native Python/Qt runtime and registered as `KratosIBRAWorkbench`.
4. The installed, relative-path cantilever project was loaded through the GUI. Background STEP conversion, geometry indexing, model validation, natural-corner selection and persisted backend configuration passed.
5. All **24 automated tests** passed: 12 analysis/export tests, 9 synthetic geometry tests and 3 project/configuration portability tests.
6. The distributed command-line example converted its own STEP file, exported the complete model and solved it with the new backend.

The GUI harness explicitly selects the installed package and redirects its configuration to a disposable test user directory. It does not rely on the pre-existing workbench, cache or conversion source.

## Analytical cantilever result

| Quantity | Value |
| --- | --- |
| Length / width / thickness | 10 / 1 / 0.1 mm |
| Young's modulus / Poisson's ratio | 200000 N/mm² / 0 |
| End line load | −0.001 N/mm |
| Analytical tip displacement magnitude | 0.02 mm |
| Computed maximum displacement magnitude | 0.02000000000080121 mm |
| Relative error | 4.00605 × 10⁻¹¹ |
| Control points / elements / conditions | 44 / 192 / 12 |

## Reproduce

From the repository root, after backend installation:

```powershell
& '.\.venv\Scripts\python.exe' freecad/Mod/KratosIBRA/backend/check_environment.py
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
& '.\.venv\Scripts\python.exe' scripts/run_example.py --output output/verification --solve
```

For maintainers with the verified FreeCAD distribution, the installed-GUI test can be reproduced using a disposable user directory:

```powershell
& '.\.venv\Scripts\python.exe' scripts/install_workbench.py --freecad-user-dir 'C:\IBRA-Test\FreeCAD User'
& 'C:\Path\To\FreeCAD\bin\python.exe' tests/gui_installed_smoke.py 'C:\IBRA-Test\FreeCAD User'
```

Use your actual paths. This harness creates and closes its own document and exits the test process. Its `gui_result.json` and screenshots are written to the disposable user directory. Production usage should follow the installation guide instead.

These checks validate this example and software path. They do not establish general engineering convergence, the correctness of arbitrary interface choices, or support for other operating systems. The historical wing conversion and two-patch studies described in the user guide are separate development evidence, not part of the 24 portable tests distributed here.
