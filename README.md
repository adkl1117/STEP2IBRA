# IBRA Preprocessor for Kratos

A native FreeCAD workbench for **isogeometric B-rep analysis (IBRA)**. Import STEP geometry, assign shell or membrane formulations, materials, refinement, boundary and initial conditions, and export a complete Kratos structural analysis model.

**Branch:** `release/v0.5preview-20260927` · **Version:** V0.5preview (`0.5.0-preview`)

[V0.5preview release / V0.5preview 发布说明](https://github.com/adkl1117/STEP2IBRA/releases/tag/V0.5preview) · [Change log](CHANGELOG.md) · [Validation](docs/Validation.md)

[Installation — English](docs/V0_5_INSTALLATION_en.md) · [安装指南 — 中文](docs/V0_5_INSTALLATION_zh.md)

[User guide — English](docs/V0_5_RELEASE_NOTES_en.md) · [使用说明 — 中文](docs/V0_5_RELEASE_NOTES_zh.md)

## Download and configure

Download the **V0.5preview** source archive from Releases and extract it into a stable directory. The V0.2 release remains available under its original tag. Alternatively, clone the preview branch:

```text
git clone --branch release/v0.5preview-20260927 https://github.com/adkl1117/STEP2IBRA.git
```

Follow the installation guide. It covers a separate Python 3.11 environment, dependency installation, an environment check, and copying the workbench into the exact FreeCAD user module directory. FreeCAD, OCCT and Kratos binaries are not included. The STEP conversion source **is bundled**, so no previous project or author-specific folder is required.

The verified platform is Windows x64 with FreeCAD 1.1.3. Other platforms require compatible binary wheels and have not been validated in this distribution.

## Capabilities

- Exact NURBS surfaces, trimming p-curves and B-rep topology from STEP/STP.
- Kirchhoff–Love shells and membranes, multiple materials and patch assignments.
- Knot insertion, degree elevation and quadrature settings.
- Essential boundary conditions, distributed loads and initial fields.
- Penalty interface coupling, with optional rotational continuity for shells.
- Named analysis groups, document association, geometry-change invalidation and explicit rebind guards.
- Reference-configuration pressure loads and tabulated time amplitudes.
- Read-only result display from Kratos IGA's native `results.vtkhdf` output.
- Three schema-3 examples: a single-patch cantilever, a two-patch shell and a perforated trimmed plate.
- Standalone Kratos export, initialization and solution.
- Selectable English/Chinese GUI, guided mouse picking and bilingual installation/usage documentation.

`geometry.cad.json` contains geometry. A runnable case also includes physics and refinement definitions, materials, solver parameters and Python processes. Official Kratos identifiers such as `IgaApplication` remain unchanged for compatibility.

## Repository layout

```text
freecad/Mod/KratosIBRA/   Installable, self-contained workbench
  backend/cad_pipeline/  STEP conversion source
  examples/              V0.2 cantilever plus three V0.5preview STEP projects
  kratos_iga/            GUI, project validation and Kratos export
scripts/                 Installation and example verification
tests/                   Geometry, export, solver and portability tests
docs/                    Installation, operation and validation evidence
requirements-backend.txt Required backend packages
```

## Verification and scope

V0.5preview is a preview distribution based on the V0.2 tag. The accepted stage evidence covers the three shipped cases, native FreeCAD project round-trips and targeted result-viewer checks. See [validation evidence](docs/Validation.md) for the target environment and the scope of those checks.

The current scope is surface shells and membranes. Independent beams, volumetric parameterization, solid elements, contact and general trimming-vertex point conditions are not implemented. Pressure acts on the reference configuration; follower pressure is unsupported. The result viewer cannot recover CAD Face/UV identities from native VTKHDF. Signed physical reactions from penalty conditions and full force equilibrium remain unverified. Geometric interface detection does not determine connection mechanics; engineering use requires constraint, penalty and convergence studies.

## License

The workbench and bundled project conversion source are provided under the [MIT License](LICENSE). External dependencies retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
