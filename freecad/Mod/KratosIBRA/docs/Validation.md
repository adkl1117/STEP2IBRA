# Validation record

Distribution: 0.5.0-preview. Validation date: 2026-09-26.

## Target environment

- Windows x64 and FreeCAD 1.1.3 with its bundled Qt/PySide.
- Python 3.11.9, OCP 7.8.1.1, and Kratos core, Iga, StructuralMechanics and LinearSolvers 10.4.3.
- Dependency pins are in `requirements-backend.txt`; the verified Windows environment is recorded in `requirements-windows-lock.txt`.

## Acceptance summary

- P0-1 through P0-5 passed their staged contract and host acceptance, including real Kratos initialization/solution checks, FreeCAD document save/reopen, geometry invalidation and rebind guards, pressure integration checks, and time-table behavior.
- P0-6 passed within its revised scope. The viewer reads only native Kratos IGA `results.vtkhdf`; target-FreeCAD checks covered all three V0.5 examples, an actual two-step result, wrong-model rejection, isolated runs, and clearing stale frames. Targeted result-viewer regression: 159 checks passed.
- P0-7 passed for the three shipped schema-3 projects: single-patch cantilever, continuous two-patch shell, and perforated trimmed plate. Evidence includes real Kratos solves and checks against analytical or independent geometry/load references, plus target FreeCAD panel load/save/cross-process reopen.
- Final backend regression: 415 tests passed, 54 skipped. This recorded run used the normal host process; a restricted sandbox is not a substitute for the Kratos DLL runtime.

The values and checks above apply to the shipped examples and target software versions. They do not prove general engineering convergence or correctness for arbitrary STEP geometry.

## Case results

| Case | Recorded check |
| --- | --- |
| Single-patch cantilever | Maximum displacement `0.02000000000080121 mm` versus `0.02 mm` analytical value. |
| Continuous two-patch shell | Maximum displacement `0.1600030769827438 mm` versus `0.16 mm` single-patch reference; measured interface jump `1.000045e-11 mm` and normal-angle difference `9.99922e-11 rad`. |
| Perforated trimmed plate | Refined integration area `32.00000000000065 mm²`; 112 load-condition RHS values sum to approximately `[0, 0, 0.032] N`. An unrefined variant with an area mismatch is rejected by the guard. |

## Known limits

- VTKHDF does not contain enough information to recover CAD Face/UV identities. Result viewing adds no run-level input-hash value or manifest; existing project geometry-change invalidation remains in place.
- Penalty-condition RHS is reported separately from strong-support reactions. Its signed physical-reaction convention has not been fully verified, so overall equilibrium involving penalty conditions remains unverified.
- Pressure acts on the reference configuration. Follower pressure is unsupported. Automatic unit conversion for rotational coupling rejects unsupported cases.
- The three cases passed panel data round-trips, but their interactive rebinding after arbitrary geometry changes and visual 3D judgment were not tested as part of P0-7. Exact nonlinear support reactions and the target C++ condition's internal `diff_phi` are also unverified.
- Only the listed Windows software stack was validated. Independent beams, volumetric parameterization, solid elements, contact, multiphysics and general trimming-point supports are out of scope.

Historical V0.2 validation is preserved in the [V0.2 tag](https://github.com/adkl1117/STEP2IBRA/releases/tag/V0.2).
