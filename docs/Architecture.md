# Architecture and input contracts

The GUI is a Python FreeCAD workbench registered through `InitGui.py` and `package.xml`. Its dock uses FreeCAD's own PySide bindings and Part geometry. It does not import OCP or Kratos binaries into the FreeCAD process.

```text
FreeCAD Part / PySide
  -> JSON request + external Python process
  -> bundled cad_pipeline + OCP
  -> exact CAD JSON + face B-reps + stable selection index
  -> project validation and export
  -> CadIoModeler -> RefinementModeler -> IgaModeler
  -> StructuralMechanicsAnalysis
```

`settings.py` resolves the bundled conversion directory relative to the installed plugin. Installation defaults are overridden by user-specific backend settings. Runtime caches and logs are written to a configurable user directory. Project file references resolve against the project location rather than the process working directory.

The geometry index is generated from the same STEP reader and face traversal used by the converter. GUI selections reference B-rep IDs; FreeCAD `FaceN` order is not assumed to match the solver's geometry IDs. Source and converted-geometry checksums prevent reuse after geometry changes.

The `.cad.json` contains NURBS geometry and topology, not constitutive or boundary-condition semantics. Refinement definitions, physics assignments, material properties and analysis parameters are separate files. The official `IgaApplication`, `IgaModeler`, `IgaMembraneElement` and process names remain unchanged.

Strong boundary conditions use control-net boundary rows and natural corners. The local parameters 0/1 denote first/last rows and −1 denotes all control points in a direction, not arbitrary normalized UV interpolation. General trimming boundaries require a supported weak enforcement method. The current penalty support acts on XYZ for the full analysis interval, while shell interface coupling explicitly sets displacement and rotational flags.

The custom exported process initializes fields only once and clears loads outside their active interval. Numerical verification includes an independently computed cantilever displacement, rather than relying only on successful process termination.

Primary implementation references:

- [FreeCAD minimal workbench](https://freecad.github.io/Addon-Academy/Demos/Minimal-Workbench/)
- [FreeCAD workbench API](https://freecad.github.io/SourceDoc/d7/dc3/group__workbench.html)
- [Kratos CAD JSON input](https://github.com/KratosMultiphysics/Kratos/blob/master/kratos/input_output/cad_json_input.h)
- [Kratos IgaApplication](https://github.com/KratosMultiphysics/Kratos/tree/master/applications/IgaApplication)

Runtime compatibility is verified against Kratos 10.4.3; upstream default-branch source can evolve independently.
