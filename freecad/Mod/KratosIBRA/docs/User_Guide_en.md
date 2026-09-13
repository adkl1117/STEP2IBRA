# IBRA Preprocessor for Kratos — User Guide

Version 0.2.0.

This native FreeCAD workbench supports preprocessing for **isogeometric B-rep analysis (IBRA)**: import exact CAD geometry, assign constitutive properties and discretization parameters, prescribe boundary and initial conditions, and export structural analysis models for Kratos. The interface uses terminology from computational structural mechanics and the finite element method.

## 1. Installation and startup

Start with the [installation guide](Installation_en.md). It covers a separate Python 3.11 environment, dependency verification and installation into the FreeCAD user module directory. This distribution includes the STEP conversion source and does not require a previous project.

After installation, restart FreeCAD and select **IBRA Preprocessor for Kratos**. In Output, use **Check Backend Environment**. The Geometry page includes **Open Cantilever Example**, which loads a configured model from the installed workbench.

The dock can be resized, detached and scrolled. Numbered navigation covers Geometry, Materials, Refinement, Support, Loads, Initial, Interfaces and Output. Validation, export and cancellation remain at the bottom. Backend configuration persists under the FreeCAD user directory in `IBRA/backend.json`; generated data use `IBRA-work` by default. Save path changes using **Save Backend Configuration**.

## 2. Geometry import and entity assignment

1. Select **Import STEP** and choose a `.stp` or `.step` file.
2. If required, use **Select Conversion Profile** before importing. An empty default profile is provided in `examples/profiles/default.json` inside the installed workbench. Model-specific exclusions must be reviewed for each input.
3. Wait for conversion. The table lists surface patches, one-sided boundaries and candidate interfaces with their B-rep IDs.
4. Select patches or boundaries in the table or viewport. Hold Ctrl to select multiple independent entities.
5. Navigate to a condition page, select **Assign Selected Entities**, enter the parameters and select **Add / Update**.

Patches are displayed with their original B-rep geometry. Independent boundaries use sampled polylines for selection; exported analysis geometry retains the exact NURBS surfaces and trimming p-curves. Orange curves indicate candidate interfaces. Geometric coincidence alone does not establish the intended structural connection.

Changing a display object's shape or placement invalidates export. Save modified CAD geometry as STEP, reimport it and review the condition assignments.

## 3. Constitutive properties and units

The Materials page provides unique ID, name, Young's modulus, Poisson's ratio, density and thickness. The current constitutive law is isotropic linear elasticity under plane stress. Assign material IDs to patches on the Refinement page.

Young's modulus, density and thickness must be finite and positive. Poisson's ratio must lie strictly between −1 and 0.5. Material IDs must be unique.

OCCT imports STEP coordinates in millimetres.

| Unit system | Geometry export | Young's modulus | Density | Thickness | Line load | Surface load |
| --- | --- | --- | --- | --- | --- | --- |
| `mm-N-s` | Preserve millimetres | N/mm² | tonne/mm³ | mm | N/mm | N/mm² |
| `m-N-s` | Multiply physical coordinates by 0.001 | Pa | kg/m³ | m | N/m | N/m² |

Default steel density is `7.85e-9` tonne/mm³. Changing units scales geometry during complete-model export; it does **not** convert material properties, thicknesses, prescribed displacements or loads. Enter dimensionally consistent data. CAD-only export preserves millimetres.

## 4. Element formulation and discretization

Each imported analysis patch requires exactly one material and element-formulation assignment.

| Setting | Interpretation |
| --- | --- |
| Kirchhoff–Love Shell | Minimum degree 2 and internal basis continuity of at least C¹ |
| Membrane | Minimum degree 1; membrane action without shell bending stiffness |
| `p`, `q` | Target polynomial degrees, at least the source degrees and no greater than 8 |
| `h-u`, `h-v` | Knot insertions per existing span, from 0 to 100 |
| `GP` | Quadrature points per span; 0 preserves defaults, explicit values range from 1 to 20 |

For batch assignment, select rows or use **Select All**, specify material, formulation, minimum target degree and knot insertions, then select **Apply to Selected Patches**. Higher source degrees are preserved.

Refinement means knot insertion and degree elevation in the spline approximation space, not a triangular or tetrahedral element size. Degree elevation preserves continuity at existing knots: an internal C⁰ knot remains C⁰. The shell formulation may require valid reparameterization or removal of redundant knots. Select a membrane only when neglecting bending is physically appropriate.

## 5. Essential boundary conditions

The Support page provides strong and penalty enforcement of prescribed displacements.

**Strong enforcement:** select displacement components and enter prescribed values. Unchecked components remain unconstrained by that condition. For a patch, Parametric Location selects a natural boundary, a natural corner or all control points. For a one-sided edge, the preprocessor verifies that the entire curve represents a complete natural parametric boundary.

**Clamp Adjacent Control-Point Row:** additionally constrains the neighbouring control-point row with the same components and values. This is the supported shell-clamping construction; it does not introduce independent rotational degrees of freedom. It requires a complete natural parametric boundary and is unavailable for isolated corners or all-control-point selections.

**Penalty enforcement:** requires a one-sided boundary curve and supports general trimming curves. The current penalty support constrains all three displacement components throughout the analysis interval. The interface selects XYZ, locks the interval and disables adjacent-row clamping. Use a positive penalty factor and assess constraint accuracy and numerical conditioning.

Natural boundaries and corners identify control-net locations, not arbitrary parametric-coordinate interpolation. A CAD vertex is accepted for strong enforcement only if it uniquely matches a natural NURBS corner. General trimming-vertex point supports are not implemented.

## 6. Loads and initial fields

The Loads page supports constant vectors and active time intervals:

- `LINE_LOAD`: select a one-sided boundary and specify force per unit length.
- `SURFACE_LOAD`: select a patch and specify force per unit area.
- `DEAD_LOAD`: integrated over the selected geometric measure; interpret units consistently with the target dimension.

The active interval must lie within the analysis interval. `End` denotes analysis end time. Loads are explicitly zero outside their active interval. Arbitrary spatial expressions and time histories are outside the current scope.

Initial displacement, velocity and acceleration are assigned to all control points of a patch once during initialization, without fixing degrees of freedom. Velocity and acceleration require dynamic analysis. Nonzero initial displacement requires nonlinear analysis.

Double-click a recorded condition to edit it. **New** exits the edit mode; **Remove** deletes the selected record. Submit form edits with **Add / Update** before saving the project.

## 7. Patch interfaces

The Interfaces page identifies adjacent patches and provides activation, penalty factor and rotational-continuity settings. Displacement-only coupling does not enforce complete shell bending continuity. Enable rotational coupling where required by the intended connection. Both patches must use the shell formulation for rotational coupling.

Review detected interfaces against the physical structure. The penalty factor is not a universal material parameter; verify its influence on both solution accuracy and conditioning.

## 8. Validation, export and solution

1. In Analysis and Output, select **Static** or **Implicit Dynamic**, and **Linear** or **Nonlinear**.
2. Specify start time, end time and a positive time increment no greater than the analysis duration.
3. Enable displacement output if required. Visualization subdivision ranges from 1 to 100 and is independent of analysis refinement.
4. Select **Validate Model** and inspect errors and notices. Correct errors before export.
5. Select **Export Analysis Model** and choose a parent directory. A unique `ibra_case_*` subdirectory preserves existing models.
6. Use **Initialize Exported Model**, then **Run Exported Analysis**. Logs are written to the working directory's `jobs` folder.

| File | Purpose |
| --- | --- |
| `geometry.cad.json` | Exact surfaces, trimming boundaries and coupling topology |
| `refinements.iga.json` | Knot insertion and degree-elevation parameters |
| `physics.iga.json` | Geometry-to-element, condition and submodel-part assignments |
| `Materials.json` | Constitutive properties, thicknesses and penalty factors |
| `ProjectParameters.json` | Modelers, solver, processes and output configuration |
| `MainKratos.py` | Structural-analysis entry point |
| `ibra_case_process.py` | Initial-field and interval-dependent load processes |
| `project.ibra-project.json` | Editable project snapshot |
| `export_report.json` | Units, notices, checksums and runtime version |

The `.iga.json` extensions and official Kratos application, element and modeler identifiers remain unchanged for backend compatibility. CAD geometry alone is not a complete analysis model.

Exported models run independently of FreeCAD but require the backend dependencies. Run from the downloaded repository root and replace the example directory with the actual export directory:

```powershell
& '.\.venv\Scripts\python.exe' '.\ibra_case_example\MainKratos.py' --check
& '.\.venv\Scripts\python.exe' '.\ibra_case_example\MainKratos.py'
```

Initialization does not prove adequate constraints or solver convergence. Successful solution does not replace discretization, penalty-parameter or engineering convergence studies.

## 9. Project persistence

**Save Project** writes `.ibra-project.json`. Reopening reconverts the original STEP and checks source and converted-geometry checksums. Legacy `.iga-project.json` files remain accessible through the generic **Project JSON** file filter.

An analysis project is separate from a FreeCAD document: `.FCStd` alone does not preserve the complete analysis configuration. Referenced STEP files, profiles and backend paths must remain available. Project paths are stored relative to the project file where possible. Move the STEP/profile files with the project or update the references after relocation. Geometry changes still require reassignment and verification.

## 10. Example and scope

The cantilever shell has length 10 mm, width 1 mm, thickness 0.1 mm, Young's modulus 200000 N/mm² and Poisson's ratio 0. Its root is clamped and its end line load is −0.001 N/mm. Analytical tip displacement is −0.02 mm; the verified displacement magnitude is approximately 0.0200000000008 mm.

A two-patch cantilever with displacement and rotational coupling yields approximately 0.0200000031 mm, with relative analytical error approximately 1.56×10⁻⁷. The original two-patch input was solved as a membrane. Wing verification covers conversion and indexing of 22 patches and 81 interfaces only; no wing structural solution has been validated.

This version does not provide independent beams or cables, volumetric parameterization, solid elements, contact, multiphysics coupling, general trimming-point supports, arbitrary space/time expressions or result-field display inside FreeCAD. Kratos 10.4.3 visualization samples the background surface; verify trimming holes and boundaries independently rather than inferring the integration domain from the display mesh. Repeated solution may append visualization time data; export to a fresh directory for independent runs.

Portable tests are in the repository `tests` directory. The installed `examples/cantilever` directory contains the STEP and relative-path project. See [validation evidence](Validation.md); the historical wing model is not distributed here.
