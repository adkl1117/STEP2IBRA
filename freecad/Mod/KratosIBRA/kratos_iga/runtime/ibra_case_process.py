"""Portable process: initial fields once; vector loads with explicit interval clearing."""
import KratosMultiphysics as KM


def Factory(settings, model):
    return CaseProcess(model, settings["Parameters"])


class CaseProcess(KM.Process):
    def __init__(self, model, settings):
        super().__init__()
        self.part = model[settings["model_part_name"].GetString()]
        self.mode = settings["mode"].GetString()
        self.name = settings["variable_name"].GetString()
        self.variable = KM.KratosGlobals.GetVariable(self.name)
        self.values = [None if settings["value"][i].IsNull() else settings["value"][i].GetDouble() for i in range(3)]
        self.interval = KM.IntervalUtility(settings) if self.mode == "load" else None

    def ExecuteInitialize(self):
        if self.mode != "initial":
            return
        for node in self.part.Nodes:
            for i, value in enumerate(self.values):
                if value is not None:
                    component = KM.KratosGlobals.GetVariable(self.name+"_"+"XYZ"[i])
                    # Seed the history as well as current state before time integration.
                    for step in range(self.part.GetBufferSize()):
                        node.SetSolutionStepValue(component, step, value)

    def ExecuteInitializeSolutionStep(self):
        if self.mode == "load":
            active = self.interval.IsInInterval(self.part.ProcessInfo[KM.TIME])
            value = KM.Vector([float(v) if active else 0.0 for v in self.values])
            for condition in self.part.Conditions:
                condition.SetValue(self.variable, value)

    def ExecuteBeforeSolutionLoop(self):
        self.ExecuteInitializeSolutionStep()
