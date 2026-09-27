# STEP2IBRA V0.5 Preview — Release Notes and Usage

Version: 0.5.0 · Date: 2026-09-27 · Workbench: **IBRA Preprocessor for Kratos**

[中文版](V0_5_RELEASE_NOTES_zh.md)

V0.5 brings STEP surface geometry, materials and sections, analysis domains, boundary and initial conditions, interface decisions, solving and result viewing into the FreeCAD workbench. It adds mouse picking, coupling-edge isolation and hover identification. This document covers features and usage; it does not contain installation instructions.

## Launch and open a first project

1. Install using [the installation guide](V0_5_INSTALLATION_en.md), then launch FreeCAD with the selected user profile.
2. Select **IBRA Preprocessor for Kratos** in FreeCAD.
3. Use **Open Project** at the top of the panel. Backend settings are under **Output → Analysis and Output**.
4. Start with `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json` in the extracted download.

Paths below are relative to the extracted release package. The same examples are copied to the installed `Mod/KratosIBRA/examples` directory.

## Select the interface language

Choose **English** or **中文** using **Interface language** at the top of the panel. Buttons, navigation, fields, tooltips, hover information and picking guidance update immediately. The choice is saved for the next launch. Switching preserves the model, condition drafts, targets and active picking stage; it does not change FreeCAD's global language. Technical variables, paths, IDs and user names stay unchanged. Raw backend logs and numerical diagnostics retain their technical source text.

## Main changes in V0.5

- Units, materials and sections: explicit unit systems, elastic properties, density and positive thickness, with deliberate conversion of existing values.
- Interfaces: candidate, confirmed and excluded states; users decide connection intent and method. Geometric adjacency alone does not establish a mechanical connection.
- Analysis domains and named groups: choose participating surfaces and reuse geometry sets as condition targets, with document association and controlled rebinding.
- Inspectable preprocessing: support and load graphics, surface parameter directions, reference normals, material/section information and discretization diagnostics.
- Pressure and time tables: scalar reference-normal pressure, explicit direction reversal and piecewise linear amplitude tables.
- Solving and results: separate exported cases and runs, Kratos initialization/analysis, and displacement/deformation viewing from native `results.vtkhdf`.
- Mouse workflows: left-click selection and middle-click confirmation for conditions; surface, surface, coupling-edge selection for interfaces; continuous next-step guidance.

## Inspect geometry, IDs and coupling edges

In **Geometry**, use the **Hover audit** selector:

| Mode | Hover information |
| --- | --- |
| Edge / Patch / Coupling (all) | Type and ID of the registered entity |
| Edge (one-sided boundary) | Ordinary boundary Edge ID |
| Patch (surface) | Surface Patch ID |
| Coupling (interface edge) | Candidate-interface Coupling ID |

IDs belong to the current project's geometry mapping. Arbitrary FreeCAD `FaceN` / `EdgeN` identifiers cannot replace them.

In **Interfaces**, click **Coupling edges only** to temporarily show only coupling edges; click again to restore previous visibility. Selecting an interface table row highlights its edge and can temporarily reveal a hidden interface. Isolation does not change the analysis domain or interface decisions.

## Pick supports, loads and initial conditions

1. Open **Boundary / Initial** and select the appropriate support, load or initial-field editor.
2. Click **Start physics picking (left select / middle confirm)**.
3. Left-click valid geometry in the 3D view. Follow the panel guidance to select additional required targets.
4. Middle-click in the view to confirm the selection and populate the right-hand editor.
5. Enter parameters and click **Add / Update** to save the condition. Middle-click confirms geometry; it does not submit a condition automatically.

Initial fields and reference-normal pressure target surfaces. Ordinary boundary conditions cannot use a Coupling edge as an ordinary boundary. Objects from other documents, result objects, preprocessing preview objects, and stale or ambiguous mappings are rejected.

Use **New** to discard the current draft and **Remove** to delete the selected condition. **Esc** or the cancel button ends picking. Switching pages or condition subpages, closing the panel, reimporting geometry, closing the document or leaving the workbench also clears temporary interaction and restores temporary visibility. Outside an active picking workflow, the middle button retains normal view navigation.

## Pick and configure an interface

1. In **Interfaces**, click **Start interface picking**.
2. Left-click the first candidate coupling surface.
3. Follow **Left-click the other candidate coupling face** and choose a second surface with an existing candidate interface to the first.
4. Left-click the corresponding candidate coupling edge. If several interfaces connect these surfaces, choose the required one.
5. Follow **Middle-click the coupling edge again to confirm the target** on the same edge.
6. Set the connection type, penalty parameters and other options, then click **Confirm Selected Interface**. Use **Exclude Selected Interface** to explicitly exclude it, or **Reset to Candidate** to leave the decision pending.

This workflow applies a new connection decision to an already identified candidate. Picking arbitrary adjacent surfaces does not generate a new candidate geometry. A shared edge or spatial proximity alone does not prove that mechanical coupling is intended.

## Materials, units, analysis domain and refinement

In **Materials**, define Young's modulus, Poisson's ratio, density and section thickness, then assign materials and sections in **Patch Assignments**. Thickness must be positive and every participating surface needs valid assignments.

| Unit system | Young's modulus | Density | Thickness |
| --- | --- | --- | --- |
| mm-N-s | N/mm² | tonne/mm³ | mm |
| m-N-s | Pa | kg/m³ | m |

FreeCAD's internal CAD length unit is millimetres. Changing the unit selector chooses a target system; **Convert Values to Selected Unit System** converts existing values. First submit a draft with **Add / Update**, or discard it with **New**. Automatic conversion of a common penalty parameter involving rotational coupling can currently be refused; follow the diagnostic rather than assuming every penalty parameter can be converted without qualification.

Control analysis participation in the patch assignment table. Geometry visibility does not change the analysis domain, and inactive surfaces may remain visible. Keep at least one valid active surface; conditions outside the domain are checked and rejected.

Create face or edge groups in **Groups** and reference them from supported condition types. Group targets cannot be mixed with direct geometry targets; missing members block valid export. Initial fields currently use direct face targets. Groups and document association rely on explicit mappings; CAD changes require review and controlled rebinding.

Optional refinement stores p/q degrees and knot insertion settings. Shell analysis requires adequate degrees and internal continuity; degree elevation does not automatically repair an existing C0 discontinuity. Display sampling, analysis refinement and integration-point counts are separate controls.

## Conditions, pressure and time tables

Supports may use strong or penalty constraints. Shell clamping constrains the adjacent control-point row on supported complete natural-parameter boundaries; arbitrary trimmed edges or CAD vertices are not general clamping targets.

Loads include vector surface/edge loads and scalar reference-normal surface pressure. The pressure sign and explicit reversal determine its direction. The reference normal comes from reference-geometry `du × dv` and is not guaranteed to be a CAD solid's outward normal. Pressure remains a reference-configuration load, including in nonlinear analysis; it is not follower pressure.

An amplitude table needs at least two finite points with strictly increasing times. Values use piecewise linear interpolation and are zero outside the active interval. Table breakpoints do not automatically become solver time steps; check time-step settings. Unit conversion leaves amplitude and time-table values unchanged.

Initial velocity and acceleration apply to dynamic analysis; nonzero initial displacement must meet nonlinear-analysis requirements. Initial fields are applied at initialization and do not continuously fix degrees of freedom.

## Preview, save and solve

In **Preview**, choose support, load, parameter-direction, reference-normal and material/section layers. Use **Rebuild Preview** to update them or **Clear Preview** to clear them. Graphics and counts help inspect inputs; they do not establish integration accuracy or mechanical correctness.

**Save Project** writes `.ibra-project.json`. In **Output**, use **Save to Document** to store document association, then save the `.FCStd` document. After reopening it, use **Restore Document** directly. Geometry changes invalidate old bindings and require the indicated checks and rebinding.

Recommended sequence:

1. **Validate Model** to check inputs.
2. **Export Analysis Model →** to export the case.
3. **Initialize Exported Model** to check Kratos initialization.
4. **Run Exported Analysis** to solve.

Export and analysis create separate `ibra_case*` and `ibra_run*` directories. Successful initialization verifies only that stage; solver termination and a result image alone do not establish physical correctness. Check the backend status in **Output**.

## View results

1. Choose **Use Last Run**, or specify a run with **Browse Run…**.
2. Click **Load Result Metadata**.
3. Select a frame, displacement X/Y/Z or magnitude, and deformation scale.
4. Click **Draw Result Frame**. Scale 0 displays the reference configuration. **Clear Result Display** removes the result layer.

The viewer reads Kratos-native `results.vtkhdf`. Display subdivision does not change analysis discretization. Native output currently lacks complete CAD face/UV mapping. Strong-support `REACTION` and raw penalty RHS are identified separately. Physical support reactions for penalty constraints are unavailable, so these fields cannot establish verified global equilibrium.

## Three included V0.5 examples

Open these projects with **Open Project**, inspect their existing settings and run them:

| Example | Project path relative to extracted package | Purpose |
| --- | --- | --- |
| Single-patch cantilever | `freecad/Mod/KratosIBRA/examples/v0_5_cantilever/cantilever.ibra-project.json` | Material, thickness, support, load and displacement |
| Two-patch continuous shell | `freecad/Mod/KratosIBRA/examples/two_patch/two_patch_shell.ibra-project.json` | Candidate interfaces, connection decisions and coupling |
| Perforated trimmed plate | `freecad/Mod/KratosIBRA/examples/perforated_plate/perforated_plate.ibra-project.json` | Internal hole, trimmed domain and pressure |

The historical **Open Cantilever Example** button opens an earlier example. Use the V0.5 project listed above for this release's full cantilever example.

## Preview scope and limitations

Local validation uses Windows x64, FreeCAD 1.1.3 and the existing Kratos backend. Release checks cover the three designated examples and new interaction workflows, not every complex STEP trimming configuration. The current scope is surface shells and membranes, without general beam, solid or contact analysis. Users must confirm interface intent and assess trimming integration, convergence and force balance with numerical checks appropriate to their model.

The mouse workflows have been checked using native FreeCAD events and coordinate mapping. Different navigation styles, display scaling and physical mouse operation should still be checked in this preview. When an operation is refused, follow the next-step guidance to correct targets or inputs and preserve geometry-invalidation protection.
