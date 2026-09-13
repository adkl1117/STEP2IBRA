"""Run from any working directory. --check initializes without solving."""
import argparse
import json
import os
from pathlib import Path
import KratosMultiphysics as KM
import KratosMultiphysics.IgaApplication
import KratosMultiphysics.LinearSolversApplication
from KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis import StructuralMechanicsAnalysis


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    os.chdir(Path(__file__).resolve().parent)
    parameters = KM.Parameters(Path("ProjectParameters.json").read_text(encoding="utf-8"))
    model = KM.Model()
    analysis = StructuralMechanicsAnalysis(model, parameters)
    if args.check:
        analysis.Initialize()
    else:
        analysis.Run()
    part = model[parameters["solver_settings"]["model_part_name"].GetString()]
    displacements = [list(n.GetSolutionStepValue(KM.DISPLACEMENT)) for n in part.Nodes]
    report = {"mode": "initialize" if args.check else "solve", "nodes": part.NumberOfNodes(),
              "elements": part.NumberOfElements(), "conditions": part.NumberOfConditions(),
              "max_abs_displacement": max((abs(v) for row in displacements for v in row), default=0),
              "time": part.ProcessInfo[KM.TIME]}
    Path("run_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
