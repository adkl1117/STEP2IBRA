# STEP2IBRA V0.5 Preview: release notes and user guide

Version: 0.5.0 · Release date: 2026-09-27 · Workbench: **IBRA Preprocessor for Kratos**

[中文版](V0_5_RELEASE_NOTES_zh.md) · [Installation guide](V0_5_INSTALLATION_en.md)

## 1. Purpose and scope

STEP2IBRA provides a FreeCAD workbench for isogeometric B-rep analysis (IBRA) of surface shells and membranes with Kratos. The workflow retains STEP surface geometry and trimming information while specifying materials, sections, the analysis domain, boundary and initial conditions, and patch-interface decisions.

V0.5 introduces explicit unit conversion, interface decision states, named groups and document association, preprocessing inspection, reference-normal pressure with time amplitudes, and displacement visualization from native Kratos output. Guided mouse selection, coupling-edge isolation and selectable English/Chinese text support model preparation. Geometric interface detection identifies candidates; the user determines their mechanical role.

## 2. Startup, projects and interface language

Install the distribution using the [installation guide](V0_5_INSTALLATION_en.md), then select **IBRA Preprocessor for Kratos** in FreeCAD. Use **Open Project** at the top of the panel. Backend configuration and status are in **Output → Analysis and Output**.

For an initial session, open `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json`. Paths in this guide are relative to the extracted package. The installer also copies the examples into `Mod/KratosIBRA/examples`.

Select **English** or **中文** using **Interface language**. Buttons, navigation, fields, tooltips, hover information and selection instructions update immediately, and the preference persists for subsequent launches. Switching preserves the model, condition drafts, targets and active picking stage. It leaves FreeCAD's global language, technical variables, paths, IDs and user-defined names unchanged. Backend logs and numerical diagnostics retain their source text.

## 3. Geometry and entity identification

Use **Import STEP** for a new `.stp` or `.step` input. Model-specific conversion profiles and exclusions require review for the input geometry. The displayed patches retain their B-rep shape; sampled boundary polylines support selection while exported analysis geometry retains NURBS surfaces and trimming p-curves.

The **Hover audit** selector in **Geometry** distinguishes these entities:

| Mode | Information |
| --- | --- |
| Edge / Patch / Coupling (all) | Registered entity type and ID |
| Edge (one-sided boundary) | Ordinary boundary Edge ID |
| Patch (surface) | Surface Patch ID |
| Coupling (interface edge) | Candidate-interface Coupling ID |

These IDs refer to the current project's geometry mapping. FreeCAD `FaceN` / `EdgeN` identifiers cannot be substituted for them.

In **Interfaces**, **Coupling edges only** temporarily isolates coupling edges. Select it again to restore the previous visibility. Selecting an interface-table row highlights its edge and can temporarily reveal a hidden interface. Visibility changes do not alter the analysis domain or interface decisions.

Changes to CAD geometry invalidate existing bindings. Review the indicated geometry checks and rebinding before further export.

## 4. Assigning supports, loads and initial fields

1. In **Boundary / Initial**, open the relevant condition editor.
2. Select **Start physics picking (left select / middle confirm)**.
3. Left-click valid geometry in the 3D view and follow the instructions for additional targets.
4. Middle-click in the view to confirm the geometry and populate the editor.
5. Enter the condition parameters and select **Add / Update** to save the record.

Initial fields and reference-normal pressure target surfaces. A Coupling edge cannot serve as an ordinary boundary-condition edge. The workflow rejects entities from another document, result and preprocessing-display objects, and stale or ambiguous mappings.

**New** clears the current draft; **Remove** deletes the selected record. **Esc** or the cancel button ends picking. Switching pages or condition subpages, closing the panel or document, reimporting geometry, or leaving the workbench clears temporary interaction and restores temporary visibility. Outside active picking, the middle button retains its normal navigation function.

## 5. Interface decisions and coupling

1. In **Interfaces**, select **Start interface picking**.
2. Left-click the first candidate coupling surface.
3. Follow **Left-click the other candidate coupling face** and choose a surface with an existing candidate interface to the first.
4. Left-click the corresponding coupling edge. If several interfaces connect the surfaces, select the intended edge.
5. Follow **Middle-click the coupling edge again to confirm the target** on that same edge.
6. Set the connection type, penalty parameters and relevant options, then select **Confirm Selected Interface**. Use **Exclude Selected Interface** to exclude the candidate or **Reset to Candidate** to return it to a pending decision.

Picking configures an existing candidate; it does not generate a new interface between arbitrary surfaces. Shared topology and spatial proximity require interpretation in the structural model.

Displacement coupling constrains relative translation across an interface. Shell bending continuity can also require rotational coupling, for which both participating patches must use shell formulations. Assess penalty parameters through constraint-accuracy, conditioning and convergence checks appropriate to the model.

## 6. Materials, units and analysis domain

In **Materials**, enter Young's modulus, Poisson's ratio and density, define section thickness, and assign materials and sections in **Patch Assignments**. Every participating surface requires valid assignments. Young's modulus, density and thickness must be finite and positive; Poisson's ratio must be strictly between −1 and 0.5. Material IDs must be unique.

| Unit system | Young's modulus | Density | Thickness | Line load | Surface load or pressure |
| --- | --- | --- | --- | --- | --- |
| mm-N-s | N/mm² | tonne/mm³ | mm | N/mm | N/mm² |
| m-N-s | Pa | kg/m³ | m | N/m | N/m² |

FreeCAD stores CAD lengths in millimetres. The unit selector specifies the target analysis system; **Convert Values to Selected Unit System** converts existing values. Submit a draft using **Add / Update**, or discard it using **New**, before conversion. Automatic conversion can refuse a shared penalty parameter involving rotational coupling; follow the diagnostic for that case. Amplitude and time-table values remain unchanged.

The patch-assignment table controls participation in the analysis domain independently of display visibility. At least one valid surface must remain active. Validation rejects conditions whose targets lie outside that domain.

In **Groups**, create face or edge sets for supported condition types. A condition cannot mix a group target with direct geometry targets, and missing group members block export. Initial fields currently use direct face targets. Named groups and document association depend on explicit mappings that require review after CAD changes.

## 7. Discretization and conditions

Optional refinement specifies p/q degrees and knot insertion. Kirchhoff-Love shell analysis requires degree at least 2 and internal continuity of at least C¹; a membrane requires degree at least 1 and carries no shell bending stiffness. Degree elevation does not repair an existing C0 discontinuity. Display sampling, spline refinement and integration-point settings control different parts of the workflow.

New projects disable refinement by default. Disabling it preserves source degrees and control points and omits RefinementModeler; stored settings remain available when refinement is enabled again. Legacy projects without this switch retain their original behavior. Validation blocks a formulation with insufficient source degree. Target degrees must be at least the source degrees and no greater than 8; knot insertions per existing span range from 0 to 100. The quadrature control uses 0 for defaults or 1 to 20 points per span. Output subdivision ranges from 1 to 100.

Strong supports prescribe selected displacement components on supported control-net locations. Shell clamping constrains an adjacent control-point row on a complete natural parametric boundary without introducing independent rotational degrees of freedom. Arbitrary trimmed edges and CAD vertices are not general clamping targets. Penalty supports constrain all three displacement components on a supported boundary curve over the analysis interval; assess the penalty factor for the intended constraint.

Loads include vector surface or edge loads and scalar reference-normal pressure. Pressure sign and explicit reversal determine its direction. The reference normal follows `du × dv` on the reference geometry and need not coincide with a CAD solid's outward normal. Pressure remains tied to the reference configuration in nonlinear analysis; follower pressure is unsupported.

An amplitude table requires at least two finite points with strictly increasing times. Interpolation is piecewise linear, and the load is zero outside its active interval. Table breakpoints do not automatically become solver time steps; choose the time increment accordingly.

Initial velocity and acceleration require dynamic analysis. Nonzero initial displacement requires nonlinear analysis. Initial fields act once during initialization without continuously fixing degrees of freedom.

## 8. Preprocessing inspection and persistence

In **Preview**, select support, load, parameter-direction, reference-normal and material/section layers. **Rebuild Preview** updates them; **Clear Preview** removes them. Use these graphics and counts to inspect assignments. Integration accuracy and mechanical validity require numerical checks.

**Save Project** writes `.ibra-project.json`. In **Output**, **Save to Document** stores document-association data, after which the `.FCStd` document must also be saved. After reopening that document, use **Restore Document**. Preserve referenced STEP and profile files when moving a project. Geometry changes invalidate previous bindings and require the prescribed checks and rebinding.

## 9. Export, initialization and solution

1. Select **Validate Model** and resolve input errors.
2. Use **Export Analysis Model →** to write the case.
3. Select **Initialize Exported Model** to check Kratos model initialization.
4. Select **Run Exported Analysis** to solve the model.

Exports and runs use separate `ibra_case*` and `ibra_run*` directories. Inspect backend status, logs and the run report in **Output**. Initialization establishes that stage's result; a successful solver exit or displayed frame does not establish physical accuracy.

The case separates geometry, refinement, physics assignments, materials and analysis settings:

| File | Role |
| --- | --- |
| `geometry.cad.json` | NURBS geometry, trimming boundaries and coupling topology |
| `refinements.iga.json` | Degree elevation and knot insertion |
| `physics.iga.json` | Element, condition and submodel-part assignments |
| `Materials.json` | Constitutive, section and penalty parameters |
| `ProjectParameters.json` | Modelers, solver, processes and output settings |
| `MainKratos.py` | Analysis entry point |
| `ibra_case_process.py` | Initial-field and interval-dependent load processes |
| `project.ibra-project.json` | Editable analysis-project snapshot |
| `export_report.json` | Export checks and diagnostics |

Exported cases run independently of FreeCAD with the required backend dependencies. Official Kratos identifiers and `.iga.json` extensions remain unchanged for compatibility. CAD geometry alone does not define an analysis.

For command-line execution, run from the extracted repository root and replace the example directory with the actual export directory:

```powershell
& '.\.venv\Scripts\python.exe' '.\ibra_case_example\MainKratos.py' --check
& '.\.venv\Scripts\python.exe' '.\ibra_case_example\MainKratos.py'
```


## 10. Result display

1. Choose **Use Last Run**, or select a directory with **Browse Run…**.
2. Select **Load Result Metadata**.
3. Choose a frame, displacement X/Y/Z or magnitude, and deformation scale.
4. Select **Draw Result Frame**. A scale of 0 displays the reference configuration. **Clear Result Display** removes the result layer.

The reader uses native `results.vtkhdf`. Display subdivision does not modify analysis discretization. Native output lacks complete CAD face/UV mapping, so the displayed mesh does not independently verify the trimmed integration domain. Strong-support `REACTION` and raw penalty RHS are identified separately. The signed physical interpretation of penalty-support reactions remains unverified; these fields do not establish global force balance.

## 11. Included examples and recorded validation

Open the following projects with **Open Project** and inspect their settings before running them:

| Example | Path relative to the extracted package | Main check |
| --- | --- | --- |
| Single-patch cantilever | `freecad/Mod/KratosIBRA/examples/v0_5_cantilever/cantilever.ibra-project.json` | Material, thickness, clamping, load and displacement |
| Two-patch continuous shell | `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json` | Interface decisions and shell coupling |
| Perforated trimmed plate | `freecad/Mod/KratosIBRA/examples/perforated_plate/perforated_plate.ibra-project.json` | Hole, trimmed domain and pressure integration |

**Open Cantilever Example** opens an earlier example. Use the V0.5 project above for this release's full cantilever case.

The [validation record](Validation.md) reports a single-patch displacement magnitude of 0.02000000000080121 mm against 0.02 mm analytically, and a two-patch magnitude of 0.1600030769827438 mm against the 0.16 mm reference. For the perforated plate, the recorded integration area is 32.00000000000065 mm²; the pressure-condition RHS sum is approximately [0, 0, 0.032] N. These checks apply to their specified cases and configurations.

## 12. Validation limits

Recorded validation uses Windows x64, FreeCAD 1.1.3 and Kratos 10.4.3. It covers the three designated examples and targeted interface, language and result workflows. Arbitrary STEP trimming configurations require independent assessment.

The release supports surface shells and membranes. General beam, solid, contact and multiphysics analysis, volumetric parameterization and general trimming-point supports are outside its scope. Engineering assessment requires interface-intent review, trimmed-domain integration checks, convergence studies and force-balance verification appropriate to the model. Physical penalty-support reactions and global equilibrium remain unverified.

Mouse workflows were checked using native FreeCAD events and coordinate mapping. Physical mouse operation, navigation styles and display scaling require checks in the intended preview environment. When selection or export is refused, correct the indicated targets or inputs while retaining geometry-invalidation protection.
