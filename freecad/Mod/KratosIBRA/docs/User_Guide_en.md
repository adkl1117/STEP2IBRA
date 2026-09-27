# IBRA Preprocessor for Kratos — User Guide

Version 0.5.0.

This native FreeCAD workbench supports preprocessing for **isogeometric B-rep analysis (IBRA)**: import exact CAD geometry, assign constitutive properties and discretization parameters, prescribe boundary and initial conditions, and export structural analysis models for Kratos. The interface uses terminology from computational structural mechanics and the finite element method.

## 1. Installation and startup

Start with the [installation guide](Installation_en.md). It covers a separate Python 3.11 environment, dependency verification and installation into the FreeCAD user module directory. This distribution includes the STEP conversion source and does not require a previous project.

After installation, restart FreeCAD and select **IBRA Preprocessor for Kratos**. In Output, use **Check Backend Environment**. The Geometry page includes **Open Cantilever Example**, which loads a configured model from the installed workbench.

The dock can be resized, detached and scrolled. Numbered navigation covers Geometry, Materials, Boundary, Initial, Interfaces and Output. Boundary contains Support and Loads tabs. The checkbox after Refinement in Geometry enables refinement and expands its settings; clearing it disables refinement and hides the settings. Validation, export and cancellation remain at the bottom. Backend configuration persists under the FreeCAD user directory in `IBRA/backend.json`; generated data use `IBRA-work` by default. Save path changes using **Save Backend Configuration**.

## 2. Geometry import and entity assignment

1. Select **Import STEP** and choose a `.stp` or `.step` file.
2. If required, use **Select Conversion Profile** before importing. An empty default profile is provided in `examples/profiles/default.json` inside the installed workbench. Model-specific exclusions must be reviewed for each input.
3. Wait for conversion. The table lists surface patches, one-sided boundaries and candidate interfaces with their B-rep IDs.
4. Select patches or boundaries in the table or viewport. Hold Ctrl to select multiple independent entities.
5. Navigate to a condition page, select **Assign Selected Entities**, enter the parameters and select **Add / Update**.
6. Mouse picking is also available: on Boundary or Initial select **启动物理点选（左键选中键确认）**, left-click a registered patch or edge in the 3D view, and middle-click to confirm and return to the form; then submit with Add / Update. Initial fields and reference pressure accept faces only. Escape (or **取消点选（Esc）**) cancels and restores the view; a page switch, reimport, document close or workbench exit also removes the temporary callbacks and restores visibility. While picking, the middle button is only used to confirm, so normal view navigation keeps working.

Patches are displayed with their original B-rep geometry. Independent boundaries use sampled polylines for selection; exported analysis geometry retains the exact NURBS surfaces and trimming p-curves. Orange curves indicate candidate interfaces. Geometric coincidence alone does not establish the intended structural connection.

The Geometry page **Hover audit** shows the registered Edge / Patch / Coupling ID under the cursor (mode: all, edge, patch or coupling). It only reads state and is removed at every cleanup point listed above. Mouse picking accepts only registered entities of the current project document whose geometry fingerprint is unchanged: objects from another document, preview/result objects, unregistered objects, stale geometry and wrong kinds are refused without mutating any state, and nothing is written to the analysis domain or to input hashes.

Changing a display object's shape or placement invalidates export. Save modified CAD geometry as STEP, reimport it and review the condition assignments.

## 3. Constitutive properties and units

The Materials page provides unique ID, name, Young's modulus, Poisson's ratio, density and thickness. The current constitutive law is isotropic linear elasticity under plane stress. Use its Patch Assignments table to assign materials, element formulations and quadrature settings even when refinement is disabled.

Young's modulus, density and thickness must be finite and positive. Poisson's ratio must lie strictly between −1 and 0.5. Material IDs must be unique.

OCCT imports STEP coordinates in millimetres.

| Unit system | Geometry export | Young's modulus | Density | Thickness | Line load | Surface load |
| --- | --- | --- | --- | --- | --- | --- |
| `mm-N-s` | Preserve millimetres | N/mm² | tonne/mm³ | mm | N/mm | N/mm² |
| `m-N-s` | Multiply physical coordinates by 0.001 | Pa | kg/m³ | m | N/m | N/m² |

Default steel density is `7.85e-9` tonne/mm³. Changing units scales geometry during complete-model export; it does **not** convert material properties, thicknesses, prescribed displacements or loads. Enter dimensionally consistent data. CAD-only export preserves millimetres.

## 4. Element formulation and discretization

Each imported analysis patch requires exactly one material and element-formulation assignment.

New projects have refinement disabled. Check Refinement in Geometry to enable degree elevation and knot insertion. When disabled, export preserves the original degrees and control points and omits RefinementModeler. Stored refinement settings are retained for re-enabling, and the switch is saved with the project. Legacy projects without the switch retain their original refinement behavior. If the original degrees are insufficient for the chosen formulation, validation blocks export and requests refinement instead of silently elevating the geometry.

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

Boundary → Support provides strong and penalty enforcement of prescribed displacements.

**Strong enforcement:** select displacement components and enter prescribed values. Unchecked components remain unconstrained by that condition. For a patch, Parametric Location selects a natural boundary, a natural corner or all control points. For a one-sided edge, the preprocessor verifies that the entire curve represents a complete natural parametric boundary.

**Clamp Adjacent Control-Point Row:** additionally constrains the neighbouring control-point row with the same components and values. This is the supported shell-clamping construction; it does not introduce independent rotational degrees of freedom. It requires a complete natural parametric boundary and is unavailable for isolated corners or all-control-point selections.

**Penalty enforcement:** requires a one-sided boundary curve and supports general trimming curves. The current penalty support constrains all three displacement components throughout the analysis interval. The interface selects XYZ, locks the interval and disables adjacent-row clamping. Use a positive penalty factor and assess constraint accuracy and numerical conditioning.

Natural boundaries and corners identify control-net locations, not arbitrary parametric-coordinate interpolation. A CAD vertex is accepted for strong enforcement only if it uniquely matches a natural NURBS corner. General trimming-vertex point supports are not implemented.

## 6. Loads and initial fields

Boundary → Loads supports constant vectors, reference-normal pressure, and an optional piecewise-linear amplitude table:

- `LINE_LOAD`: select a one-sided boundary and specify force per unit length.
- `SURFACE_LOAD`: select a patch and specify force per unit area.
- `DEAD_LOAD`: integrated over the selected geometric measure; interpret units consistently with the target dimension.
- **Reference (dead) pressure**: select a patch or face group, enter a signed scalar `p`, and use the explicit `normal_flip` to reverse it. The positive direction is the normalised parametric `∂P/∂u × ∂P/∂v` direction (`du × dv`), **not** the OCCT/CAD outward normal. This stage supports faces/face groups only and does not support follower/current-normal pressure.
- **Amplitude table** (optional, for vectors and pressure): `[[t, f], ...]` with at least two points, strictly increasing finite times and no auto-sorting; the table domain must lie inside both the analysis interval and the load interval (narrower is allowed); stored endpoints are used and values outside the table are zero. Factors and times are not unit-converted, and **table times do not add solver time steps** — align the key times with your time increment yourself.

The active interval must lie within the analysis interval. `End` denotes analysis end time. Loads are explicitly zero outside their active interval. Arbitrary spatial expressions and time histories are outside the current scope. Pressure takes its reference normal from the parametric `du × dv` on the **X0 reference configuration**. Unit conversion follows the export direction: **mm→m** scales pressure by ×1e6 and line load by ×1000; **m→mm** uses the reciprocals; amplitude-table numbers are unchanged. Follower/current-normal pressure is unsupported in **both linear and nonlinear** analysis.

In static linear analysis each pseudo-time is an independent reference load case, and that behaviour applies **only through the exported `MainKratos.py`** (`CheckedAnalysis.InitializeSolutionStep`, which resets X0 and zeroes displacement before BCs/loads/Predict); dynamic and nonlinear analysis preserve their real history and are not reset. The linear `Shell3pElement` performs a single residual iteration per step, so its `REACTION` may carry substantial transverse error — **accurate reactions require converged nonlinear analysis**.

Initial displacement, velocity and acceleration are assigned to all control points of a patch once during initialization, without fixing degrees of freedom. Velocity and acceleration require dynamic analysis. Nonzero initial displacement requires nonlinear analysis.

Double-click a recorded condition to edit it. **New** exits the edit mode; **Remove** deletes the selected record. Submit form edits with **Add / Update** before saving the project.

## 7. Patch interfaces

The Interfaces page identifies adjacent patches and provides activation, penalty factor and rotational-continuity settings. Displacement-only coupling does not enforce complete shell bending continuity. Enable rotational coupling where required by the intended connection. Both patches must use the shell formulation for rotational coupling.

Review detected interfaces against the physical structure. The penalty factor is not a universal material parameter; verify its influence on both solution accuracy and conditioning.

Mouse interface flow: select **启动接口点选流程**, then left-click the first candidate patch, left-click the second candidate patch, and left-click the **existing** candidate interface edge between them; middle-click on that same edge confirms the target. The table highlights the interface row and the right-hand side shows the interface's existing connection type and penalty factor. The connection intent must still be saved explicitly through the existing **Confirm Selected Interface** or **Exclude Selected Interface** — the mouse flow never changes the decision itself. If no existing candidate edge joins the two faces, the flow refuses instead of inventing an interface from adjacency.

**只显示耦合边（coupling-only）** temporarily hides every patch and one-sided boundary and keeps only the interface edges; selecting it again (or cancelling, switching page, reimporting, closing the document or leaving the workbench) restores the previous visibility. This display layer does not change the analysis domain, the coupling decisions or the exported inputs.

## 8. Validation, export and solution

1. In Analysis and Output, select **Static** or **Implicit Dynamic**, and **Linear** or **Nonlinear**.
2. Specify start time, end time and a positive time increment no greater than the analysis duration.
3. Enable displacement output if required. Visualization subdivision ranges from 1 to 100 and is independent of analysis refinement.
4. Select **Validate Model** and inspect errors and notices. Correct errors before export.
5. Select **Export Analysis Model** and choose a parent directory. A unique `ibra_case_*` subdirectory preserves existing models.
6. Use **Initialize Exported Model**, then **Run Exported Analysis**. Each run creates a separate `ibra_run_*` directory under the export; logs are written to the working directory's `jobs` folder.

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

## 9. Viewing results

After a solve, select its `ibra_run_*` directory in the Output page's result section and click **Load Result Metadata**. Choose a recorded step, displacement component X/Y/Z or magnitude, and deformation scale, then click **Draw Result Frame**. Scale 0 displays the reference position. **Clear Result Display** removes the temporary result layer and restores the original CAD display. Changing the model or refusing a result from a different model clears any old result. The viewer reads only the native `results.vtkhdf` written by Kratos IGA in that run; initialization alone produces no drawable result.

The panel reports extrema and their positions, strong-support reactions, actual applied-load resultant, raw penalty-condition contributions, and four distinct evidence levels. A signed physical reaction for penalty conditions is currently `unavailable`; balance for such cases is `unverified`. Native VTKHDF has no face/UV correspondence, so that mapping is explicitly unavailable. The result layer does not change CAD Shapes, project bindings or analysis inputs.

## 10. Project persistence

**Save Project** writes `.ibra-project.json`. Reopening reconverts the original STEP and checks source and converted-geometry checksums. Legacy `.iga-project.json` files remain accessible through the generic **Project JSON** file filter.

Current P0-3 behaviour: pressing **Ctrl+S** to save the `.FCStd` document associates the saved analysis project with that document, and reopening the document restores that association — a separate `.ibra-project.json` save is no longer required to continue. When the geometry changes, existing bindings/conditions must be rebound first; the geometry-change protection blocks reusing stale conditions and the pending rebind guards report what is required. Referenced STEP files, profiles and backend paths must still remain available, and project paths are stored relative to the project file where possible. Move the STEP/profile files with the project or update the references after relocation.

## 11. Example and scope

The cantilever shell has length 10 mm, width 1 mm, thickness 0.1 mm, Young's modulus 200000 N/mm² and Poisson's ratio 0. Its root is clamped and its end line load is −0.001 N/mm. Analytical tip displacement is −0.02 mm; the verified displacement magnitude is approximately 0.0200000000008 mm.

A two-patch cantilever with displacement and rotational coupling yields approximately 0.0200000031 mm, with relative analytical error approximately 1.56×10⁻⁷. The original two-patch input was solved as a membrane. Wing verification covers conversion and indexing of 22 patches and 81 interfaces only; no wing structural solution has been validated.

This version does not provide independent beams or cables, volumetric parameterization, solid elements, contact, multiphysics coupling, general trimming-point supports or arbitrary space/time expressions. Native Kratos 10.4.3 visualization proves only the sampled picture; verify trimming geometry, integration area and loads independently. Each panel solve uses a new isolated run directory so old visualization steps are not mixed into the new result.

Portable tests are in the repository `tests` directory. The installed `examples/cantilever` directory contains the STEP and relative-path project. See [validation evidence](Validation.md); the historical wing model is not distributed here.
