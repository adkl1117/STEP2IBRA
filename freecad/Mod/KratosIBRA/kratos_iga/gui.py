"""Native Qt dock inside FreeCAD. No web runtime or OCP DLL imports."""
from pathlib import Path
import hashlib
import json
import tempfile
import uuid
import os
import subprocess
import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui
try:
    from PySide import QtWidgets as W
except ImportError:
    W = QtGui
from .project import new_project, populate, read_json, write_json, validate, read_project, write_project

BASE = Path(__file__).resolve().parents[1]
from .settings import load_settings, save_settings
_panel = None


def spin(value, lo=0, hi=100, decimals=None):
    widget = W.QSpinBox() if decimals is None else W.QDoubleSpinBox()
    widget.setRange(lo, hi)
    if decimals is not None:
        widget.setDecimals(decimals)
    widget.setValue(value)
    return widget


def button(text, callback):
    widget = W.QPushButton(text)
    widget.clicked.connect(callback)
    return widget


def element_selector():
    widget = W.QComboBox()
    widget.addItem("Kirchhoff–Love Shell", "Shell3pElement")
    widget.addItem("Membrane", "IgaMembraneElement")
    widget.setMinimumContentsLength(22)
    widget.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    return widget


def text_hint(text):
    label = W.QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("hint")
    return label


class VectorEditor(W.QWidget):
    def __init__(self, optional=False):
        super().__init__()
        layout = W.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.checks, self.values = [], []
        for axis in "XYZ":
            box = W.QWidget()
            column = W.QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, 0)
            check = W.QCheckBox("Displacement "+axis if optional else axis)
            check.setChecked(True)
            check.setEnabled(optional)
            value = W.QLineEdit("0.0")
            value.setMinimumWidth(60)
            check.toggled.connect(value.setEnabled)
            column.addWidget(check)
            column.addWidget(value)
            layout.addWidget(box)
            self.checks.append(check)
            self.values.append(value)

    def get(self):
        return [float(w.text()) if c.isChecked() else None for c, w in zip(self.checks, self.values)]

    def set(self, values):
        for value, check, edit in zip(values, self.checks, self.values):
            check.setChecked(value is not None)
            edit.setText(str(value if value is not None else 0.0))


class Panel(W.QDockWidget):
    def __init__(self, parent=None):
        super().__init__("IBRA Preprocessor for Kratos", parent)
        self.setObjectName("KratosIBRAPreprocessor")
        self.setMinimumWidth(690)
        self.user_data = Path(App.getUserAppDataDir())
        self.settings = load_settings(self.user_data, BASE)
        self.work_dir = Path(self.settings['work_dir'])
        self.project = new_project()
        self.index = {"faces": [], "edges": [], "source_sha256": ""}
        self.cache = None
        self.doc = None
        self.process = None
        self.last_export = None
        self.entities = {}
        self.display_fingerprints = {}
        self.pending_project = None
        self.editing = {}
        self.form = W.QWidget()
        self.form.setObjectName("igaPanel")
        self.setWidget(self.form)
        layout = W.QVBoxLayout(self.form)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)
        brand = W.QWidget()
        brand.setObjectName("brand")
        brand_layout = W.QHBoxLayout(brand)
        brand_layout.setContentsMargins(18, 13, 18, 13)
        brand_text = W.QVBoxLayout()
        brand_text.setSpacing(2)
        header = W.QLabel("IBRA Preprocessor")
        header.setObjectName("title")
        brand_text.addWidget(header)
        caption = W.QLabel("B-rep-based structural analysis for Kratos")
        caption.setObjectName("brandCaption")
        brand_text.addWidget(caption)
        brand_layout.addLayout(brand_text)
        brand_layout.addStretch()
        badge = W.QLabel("KRATOS")
        badge.setObjectName("brandTag")
        brand_layout.addWidget(badge)
        layout.addWidget(brand)
        top = W.QHBoxLayout()
        import_button = button("+ Import STEP", self.import_step)
        import_button.setObjectName("primary")
        top.addWidget(import_button)
        top.addWidget(button("Open Project", self.open_project))
        top.addWidget(button("Save Project", self.save_project))
        layout.addLayout(top)
        self.summary = text_hint("Import STEP geometry to define shell or membrane models on trimmed surfaces.")
        self.summary.setObjectName("projectSummary")
        layout.addWidget(self.summary)
        body = W.QHBoxLayout()
        body.setSpacing(10)
        self.navigation = W.QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(132)
        self.navigation.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.navigation.setFocusPolicy(QtCore.Qt.NoFocus)
        body.addWidget(self.navigation)
        self.tabs = W.QTabWidget()
        self.tabs.tabBar().hide()
        body.addWidget(self.tabs, 1)
        layout.addLayout(body, 1)
        self.navigation.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.navigation.setCurrentRow)
        self.build_geometry()
        self.build_materials()
        self.build_patches()
        self.build_supports()
        self.build_loads()
        self.build_initial()
        self.build_couplings()
        self.build_output()
        self.navigation.setCurrentRow(0)
        self.status = W.QLabel("Ready | Import geometry to define the analysis model.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = W.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.log = W.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.setPlaceholderText("Geometry conversion, model validation and solver log")
        self.log.setMaximumBlockCount(1500)
        self.log.hide()
        self.log_toggle = button("Show Log", self.toggle_log)
        self.log_toggle.setObjectName("quiet")
        layout.addWidget(self.log_toggle, 0, QtCore.Qt.AlignRight)
        layout.addWidget(self.log)
        actions = W.QHBoxLayout()
        actions.addWidget(button("Validate Model", self.check_project))
        export_button = button("Export Analysis Model  →", self.export_case)
        export_button.setObjectName("export")
        actions.addWidget(export_button, 1)
        self.stop_button = button("Stop", self.cancel)
        self.stop_button.setObjectName("quiet")
        self.stop_button.setEnabled(False)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)
        from .theme import stylesheet
        self.form.setStyleSheet(stylesheet())

    def toggle_log(self):
        self.log.setVisible(not self.log.isVisible())
        self.log_toggle.setText("Hide Log" if self.log.isVisible() else "Show Log")

    def page(self, title):
        page = W.QWidget()
        page.setObjectName("contentPage")
        layout = W.QVBoxLayout(page)
        layout.setContentsMargins(16, 15, 16, 15)
        layout.setSpacing(10)
        titles = [("GEOMETRY", "Geometry and Topology", "Geometry"), ("MATERIALS", "Constitutive Properties", "Materials"),
                  ("DISCRETIZATION", "Discretization and Refinement", "Refinement"), ("SUPPORTS", "Essential Boundary Conditions", "Support"),
                  ("LOADS", "Loads", "Loads"), ("INITIAL STATE", "Initial Conditions", "Initial"),
                  ("INTERFACES", "Patch Interfaces", "Interfaces"), ("ANALYSIS", "Analysis and Output", "Output")]
        number = self.tabs.count()
        eyebrow = W.QLabel(titles[number][0])
        eyebrow.setObjectName("eyebrow")
        heading = W.QLabel(titles[number][1])
        heading.setObjectName("sectionTitle")
        layout.addWidget(eyebrow)
        layout.addWidget(heading)
        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        self.navigation.addItem(f"{number+1:02}  {titles[number][2]}")
        return layout

    def table(self, headers):
        table = W.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(36)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def build_geometry(self):
        layout = self.page("Geometry")
        self.geometry_table = self.table(["Type", "BRep ID", "Patch IDs / Degrees"])
        self.geometry_table.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        self.geometry_table.setSelectionMode(W.QAbstractItemView.ExtendedSelection)
        self.geometry_table.itemSelectionChanged.connect(self.select_table_geometry)
        layout.addWidget(self.geometry_table)
        layout.addWidget(text_hint("Select patches or edges in the table or viewport; hold Ctrl for multiple entities. Orange curves indicate candidate interfaces. Display polylines do not replace the NURBS analysis geometry."))
        layout.addWidget(button("Open Cantilever Example", lambda: self.guard(lambda: self.load_project(BASE/"examples/cantilever/cantilever.ibra-project.json"))))
        self.selection_text = text_hint("No target entity selected")
        layout.addWidget(self.selection_text)
        self.profile = W.QLineEdit()
        self.profile.setPlaceholderText("Optional model-specific conversion profile (JSON)")
        layout.addWidget(self.profile)
        layout.addWidget(button("Select Conversion Profile", self.choose_profile))
        layout.addWidget(text_hint("Reimport after changing the profile. Face exclusion and duplicate-face handling follow the configured conversion pipeline."))

    def build_materials(self):
        layout = self.page("Materials")
        layout.addWidget(text_hint("Constitutive law: isotropic linear elasticity under plane stress. Assign material IDs to patches in Discretization."))
        self.material_table = self.table(["ID", "Name", "E", "ν", "ρ", "Thickness"])
        layout.addWidget(self.material_table)
        row = W.QHBoxLayout()
        row.addWidget(button("Add Material", self.add_material))
        row.addWidget(button("Remove Selected Rows", lambda: self.delete_row(self.material_table)))
        layout.addLayout(row)
        self.units = W.QComboBox()
        self.units.addItems(["mm-N-s", "m-N-s"])
        layout.addWidget(self.units)
        layout.addWidget(text_hint("OCCT imports STEP coordinates in mm. The m-N-s system scales exported geometry by 0.001. Enter consistent material, thickness and load values. Density in mm-N-s is expressed in tonne/mm³."))

    def build_patches(self):
        layout = self.page("Discretization")
        layout.addWidget(text_hint("Degrees p/q are target polynomial degrees. h-u/h-v specify knot insertions per existing span. GP = 0 retains the default quadrature rule."))
        self.patch_table = self.table(["Face", "Material ID", "Element", "p", "q", "h-u", "h-v", "GP"])
        layout.addWidget(self.patch_table)
        self.bulk_material = spin(1,1,9999)
        self.bulk_element = element_selector()
        self.bulk_degree = spin(2,1,8)
        self.bulk_h = spin(1,0,100)
        batch = W.QFormLayout()
        batch.addRow("Material ID",self.bulk_material)
        batch.addRow("Element Formulation",self.bulk_element)
        dimensions = W.QWidget()
        row = W.QHBoxLayout(dimensions)
        row.setContentsMargins(0,0,0,0)
        row.addWidget(W.QLabel("Minimum Degree"))
        row.addWidget(self.bulk_degree)
        row.addWidget(W.QLabel("Knots per Span"))
        row.addWidget(self.bulk_h)
        batch.addRow(dimensions)
        layout.addLayout(batch)
        row = W.QHBoxLayout()
        row.addWidget(button("Select All",self.patch_table.selectAll))
        row.addWidget(button("Apply to Selected Patches",self.bulk_patch_settings),1)
        layout.addLayout(row)
        layout.addWidget(text_hint("The shell formulation requires degree ≥ 2 and internal C¹ continuity. Degree elevation preserves existing C⁰ continuity. Membranes require degree ≥ 1. Beam and volumetric formulations are outside the current scope."))

    def condition_common(self, category, title):
        layout = self.page(title)
        target = W.QLineEdit()
        target.setReadOnly(True)
        target.setPlaceholderText("Select geometric entities, then assign the selection.")
        layout.addWidget(target)
        layout.addWidget(button("Assign Selected Entities", lambda: self.capture(category)))
        name = W.QLineEdit(title+"_1")
        form = W.QFormLayout()
        form.addRow("Name", name)
        layout.addLayout(form)
        setattr(self, category+"_target", target)
        setattr(self, category+"_name", name)
        setattr(self, category+"_targets", [])
        return layout, form

    def interval_editor(self, form, category):
        box = W.QWidget()
        row = W.QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        a, b = W.QLineEdit("0.0"), W.QLineEdit("End")
        row.addWidget(a)
        row.addWidget(W.QLabel("→"))
        row.addWidget(b)
        form.addRow("Active Time Interval", box)
        setattr(self, category+"_interval", (a, b))

    def condition_list(self, layout, category):
        row = W.QHBoxLayout()
        row.addWidget(button("Add / Update", lambda: self.add_condition(category)))
        row.addWidget(button("Remove", lambda: self.delete_condition(category)))
        row.addWidget(button("New", lambda: self.editing.pop(category, None)))
        layout.addLayout(row)
        items = W.QListWidget()
        items.itemDoubleClicked.connect(lambda item: self.edit_condition(category, items.row(item)))
        layout.addWidget(items)
        layout.addWidget(text_hint("Double-click a condition to edit it. New exits the current edit mode."))
        setattr(self, category+"_list", items)

    def build_supports(self):
        layout, form = self.condition_common("supports", "Support")
        self.support_method = W.QComboBox()
        self.support_method.addItems(["Strong Enforcement | Control Points", "Penalty Enforcement | Boundary XYZ"])
        form.addRow("Enforcement", self.support_method)
        self.support_side = W.QComboBox()
        for label, value in [("U-min Boundary", "u0"), ("U-max Boundary", "u1"), ("V-min Boundary", "v0"), ("V-max Boundary", "v1"),
            ("Corner U-min / V-min", "00"), ("Corner U-max / V-min", "10"), ("Corner U-min / V-max", "01"), ("Corner U-max / V-max", "11"), ("All Control Points", "all")]:
            self.support_side.addItem(label, value)
        form.addRow("Parametric Location", self.support_side)
        self.interval_editor(form, "supports")
        self.support_vector = VectorEditor(True)
        layout.addWidget(self.support_vector)
        self.clamp = W.QCheckBox("Clamp Adjacent Control-Point Row")
        self.clamp.setToolTip("Applies to a complete natural parametric boundary. The adjacent control-point row receives the same prescribed displacement components and values.")
        layout.addWidget(self.clamp)
        self.penalty = W.QLineEdit("1e7")
        form.addRow("Penalty Factor", self.penalty)
        layout.addWidget(text_hint("Strong enforcement supports individual displacement components on natural boundaries and corners. General trimming curves require penalty enforcement, currently restricted to XYZ throughout the analysis interval."))
        self.condition_list(layout, "supports")
        self.support_method.currentIndexChanged.connect(self.update_support_method)
        self.update_support_method()

    def update_support_method(self):
        penalty = self.support_method.currentIndex() == 1
        self.penalty.setEnabled(penalty)
        self.support_side.setEnabled(not penalty)
        self.clamp.setEnabled(not penalty)
        if penalty:
            self.clamp.setChecked(False)
            self.supports_interval[0].setText(self.start_time.text() if hasattr(self,"start_time") else "0.0")
            self.supports_interval[1].setText("End")
        for w in self.supports_interval:
            w.setEnabled(not penalty)
        for check in self.support_vector.checks:
            check.setEnabled(not penalty)
            if penalty:
                check.setChecked(True)

    def bulk_patch_settings(self):
        rows = sorted({item.row() for item in self.patch_table.selectedItems()})
        if not rows:
            self.error("Select patch rows or use Select All before applying batch properties.")
            return
        faces = {f["id"]:f for f in self.index["faces"]}
        for row in rows:
            face = faces[int(self.patch_table.item(row,0).text())]
            self.patch_table.item(row,1).setText(str(self.bulk_material.value()))
            self.patch_table.cellWidget(row,2).setCurrentText(self.bulk_element.currentText())
            minimum = max(self.bulk_degree.value(), 2 if self.bulk_element.currentData()=="Shell3pElement" else 1)
            for col, degree in zip((3,4),face["degrees"]):
                self.patch_table.item(row,col).setText(str(max(minimum,degree)))
            for col in (5,6):
                self.patch_table.item(row,col).setText(str(self.bulk_h.value()))
        self.status.setText(f"Material, formulation and refinement updated for {len(rows)} patches.")

    def build_loads(self):
        layout, form = self.condition_common("loads", "Loads")
        self.load_variable = W.QComboBox()
        self.load_variable.addItems(["LINE_LOAD", "SURFACE_LOAD", "DEAD_LOAD"])
        form.addRow("Variable", self.load_variable)
        self.interval_editor(form, "loads")
        self.load_vector = VectorEditor()
        layout.addWidget(self.load_vector)
        layout.addWidget(text_hint("LINE_LOAD: force per unit length. SURFACE_LOAD: force per unit area. DEAD_LOAD is integrated over the selected geometric measure. Loads are zero outside the active interval."))
        self.condition_list(layout, "loads")

    def build_initial(self):
        layout, form = self.condition_common("initial_conditions", "Initial")
        self.initial_variable = W.QComboBox()
        self.initial_variable.addItems(["DISPLACEMENT", "VELOCITY", "ACCELERATION"])
        form.addRow("Initial Field", self.initial_variable)
        self.initial_vector = VectorEditor(True)
        layout.addWidget(self.initial_vector)
        layout.addWidget(text_hint("Initial fields are assigned once to all control points of a patch without constraining degrees of freedom. Velocity and acceleration require dynamic analysis; nonzero initial displacement requires nonlinear analysis."))
        self.condition_list(layout, "initial_conditions")

    def build_couplings(self):
        layout = self.page("Interfaces")
        self.coupling_table = self.table(["Edge", "Adjacent Patches", "Enabled", "Penalty Factor", "Rotational Continuity"])
        layout.addWidget(self.coupling_table)
        layout.addWidget(text_hint("Penalty coupling uses the two-sided B-rep topology. Displacement-only coupling does not enforce shell bending continuity. Enable rotational continuity where required and review every candidate interface against the intended connection model."))

    def build_output(self):
        layout = self.page("Analysis")
        form = W.QFormLayout()
        self.solver_type = W.QComboBox()
        self.solver_type.addItem("Static", "static")
        self.solver_type.addItem("Implicit Dynamic", "dynamic")
        self.analysis_type = W.QComboBox()
        self.analysis_type.addItem("Linear", "linear")
        self.analysis_type.addItem("Nonlinear", "non_linear")
        self.start_time, self.end_time, self.time_step = W.QLineEdit("0.0"), W.QLineEdit("1.0"), W.QLineEdit("1.0")
        for label, w in [("Solver", self.solver_type), ("Analysis", self.analysis_type), ("Start Time", self.start_time), ("End Time", self.end_time), ("Time Increment", self.time_step)]:
            form.addRow(label, w)
        self.vtk = W.QCheckBox("Export Displacement to VTKHDF")
        self.vtk.setChecked(True)
        self.output_refinement = spin(4, 1, 100)
        form.addRow(self.vtk)
        form.addRow("Visualization Subdivision", self.output_refinement)
        layout.addLayout(form)
        self.backend_python = W.QLineEdit(self.settings["backend_python"])
        self.pipeline_root = W.QLineEdit(self.settings["pipeline_root"])
        form.addRow("Backend Python Executable", self.backend_python)
        form.addRow("Conversion Pipeline Directory", self.pipeline_root)
        self.work_directory = W.QLineEdit(str(self.work_dir))
        form.addRow("Working Directory", self.work_directory)
        layout.addWidget(button("Save Backend Configuration", lambda: self.guard(self.save_backend_configuration)))
        layout.addWidget(button("Check Backend Environment", lambda: self.guard(self.check_backend_environment)))
        layout.addWidget(text_hint("Export CAD geometry, refinement, constitutive properties, boundary conditions and solver configuration. Backend: Kratos 10.4.3 with the required structural analysis applications."))
        layout.addWidget(button("Export CAD Geometry Only", self.export_geometry))
        layout.addWidget(button("Initialize Exported Model", lambda: self.run_case(True)))
        layout.addWidget(button("Run Exported Analysis", lambda: self.run_case(False)))
        layout.addWidget(button("Stop Background Task", self.cancel))
        layout.addStretch()

    def error(self, error):
        self.status.setText("Operation failed: "+str(error))
        self.log.appendPlainText(str(error))
        self.log.show()
        self.log_toggle.setText("Hide Log")

    def guard(self, callback):
        try:
            return callback()
        except Exception as exc:
            self.error(exc)
            return None

    def choose_profile(self):
        path, _ = W.QFileDialog.getOpenFileName(self, "Conversion Profile", str(Path.home()), "JSON (*.json)")
        if path:
            self.profile.setText(path)

    def import_step(self):
        path, _ = W.QFileDialog.getOpenFileName(self, "Import STEP", str(Path.home()), "STEP (*.stp *.step *.STP *.STEP)")
        if path:
            self.pending_project = None
            self.guard(lambda: self.start_import(path))

    def start_import(self, path):
        if self.process:
            raise ValueError("A background task is already running.")
        self.import_path = str(Path(path).resolve())
        cache = self.work_dir/"cache"/uuid.uuid4().hex
        cache.mkdir(parents=True)
        self.import_cache = cache
        self.start_worker({"action": "convert", "source_step": self.import_path,
            "profile": self.profile.text().strip(), "cache_dir": str(cache)}, self.import_finished)

    def start_worker(self, request, callback):
        request["pipeline_root"] = self.pipeline_root.text()
        root = self.work_dir/"requests"
        root.mkdir(parents=True, exist_ok=True)
        filename = root/(uuid.uuid4().hex+".json")
        write_json(filename, request)
        self.start_process([str(BASE/"kratos_iga"/"worker.py"), str(filename)], callback)

    def start_process(self, arguments, callback):
        if self.process:
            raise ValueError("A background task is already running.")
        python = self.backend_python.text().strip()
        if not Path(python).is_file():
            raise ValueError("Backend Python was not found. Configure its executable in Analysis and Output.")
        environment = os.environ.copy()
        environment.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", OMP_NUM_THREADS="4")
        jobs = self.work_dir/"jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        logfile = jobs/(uuid.uuid4().hex+".log")
        # File-backed logs also work where Qt's Windows named pipes are unavailable.
        with logfile.open("wb") as stream:
            proc = subprocess.Popen([python]+arguments, stdout=stream, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.process = proc
        self.stop_button.setEnabled(True)
        self.progress.show()
        self.tabs.setEnabled(False)
        self.status.setText("Background task in progress…")
        timer = QtCore.QTimer(self)
        offset = 0
        def poll():
            nonlocal offset
            with logfile.open("rb") as stream:
                stream.seek(offset)
                chunk = stream.read()
                offset = stream.tell()
            if chunk:
                self.log.appendPlainText(chunk.decode("utf-8", "replace").rstrip())
            code = proc.poll()
            if code is None:
                return
            timer.stop()
            timer.deleteLater()
            self.status.setText("Completed" if code == 0 else "Background task failed or was stopped. Inspect the log.")
            if code == 0:
                self.guard(callback)
            self.process = None
            self.stop_button.setEnabled(False)
            self.progress.hide()
            self.tabs.setEnabled(True)
            if code != 0:
                self.log.show()
                self.log_toggle.setText("Hide Log")
        timer.timeout.connect(poll)
        timer.start(250)

    def cancel(self):
        if self.process:
            self.process.kill()

    def import_finished(self):
        new_index = read_json(self.import_cache/"index.json")
        if self.pending_project:
            if self.pending_project["source_sha256"] != new_index["source_sha256"]:
                raise ValueError("The source STEP file has changed.")
            if self.pending_project.get("geometry_sha256", new_index.get("geometry_sha256")) != new_index.get("geometry_sha256"):
                raise ValueError("The profile or converted geometry has changed; previous entity assignments cannot be reused.")
            self.project = self.pending_project
            self.pending_project = None
        else:
            self.project = new_project()
            self.project.update({"source_step": self.import_path, "source_sha256": new_index["source_sha256"], "geometry_sha256": new_index.get("geometry_sha256"), "profile": self.profile.text().strip()})
            populate(self.project, new_index)
        self.cache = self.import_cache
        self.index = new_index
        self.show_geometry()
        self.last_export = None
        self.editing.clear()
        for category in ("supports", "loads", "initial_conditions"):
            setattr(self, category+"_targets", [])
            getattr(self, category+"_target").clear()
        self.refresh()
        self.status.setText("Geometry imported | Define material properties and analysis conditions.")

    def show_geometry(self):
        self.doc = App.newDocument("KratosIBRA")
        root = self.doc.addObject("App::DocumentObjectGroup", "IBRA_Geometry")
        root.Label = "IBRA Analysis Geometry"
        self.entities = {}
        self.display_fingerprints = {}
        for face in self.index["faces"]:
            shape = Part.Shape()
            shape.read(str(self.cache/face["brep_file"]))
            obj = self.doc.addObject("Part::Feature", f"IBRA_Face_{face['id']}")
            obj.Shape = shape
            obj.Label = f"Patch {face['id']} · p{face['degrees'][0]} q{face['degrees'][1]}"
            obj.ViewObject.ShapeColor = (0.64, 0.80, 0.86)
            obj.ViewObject.LineColor = (0.16, 0.33, 0.41)
            self.tag(obj, "face", face["id"])
            root.addObject(obj)
        for edge in self.index["edges"]:
            obj = self.doc.addObject("Part::Feature", f"IBRA_Edge_{edge['id']}")
            points = [App.Vector(*point) for point in edge["points"]]
            obj.Shape = Part.makePolygon(points)
            obj.Label = ("Coupling " if edge["kind"] == "coupling" else "Boundary ")+str(edge["id"])
            obj.ViewObject.LineColor = (0.94, 0.51, 0.12) if edge["kind"] == "coupling" else (0.12, 0.52, 0.48)
            obj.ViewObject.LineWidth = 4.0 if edge["kind"] == "coupling" else 2.5
            self.tag(obj, edge["kind"], edge["id"])
            root.addObject(obj)
        self.doc.recompute()
        self.display_fingerprints = {name: (self.doc.getObject(name).Shape.hashCode(), str(self.doc.getObject(name).Placement)) for name in self.entities.values()}
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()

    def tag(self, obj, kind, ident):
        obj.addProperty("App::PropertyInteger", "IbraBrepId", "IBRA")
        obj.IbraBrepId = ident
        obj.setEditorMode("IbraBrepId", 1)
        obj.addProperty("App::PropertyString", "IbraKind", "IBRA")
        obj.IbraKind = kind
        obj.setEditorMode("IbraKind", 1)
        obj.setEditorMode("Placement", 1)
        self.entities[(kind, ident)] = obj.Name
        self.display_fingerprints[obj.Name] = (obj.Shape.hashCode(), str(obj.Placement))

    def table_row(self, table, values, readonly=()):
        row = table.rowCount()
        table.insertRow(row)
        for col, value in enumerate(values):
            if isinstance(value, W.QWidget):
                table.setCellWidget(row, col, value)
            else:
                item = W.QTableWidgetItem(str(value))
                if col in readonly:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                table.setItem(row, col, item)

    def refresh(self):
        self.geometry_table.blockSignals(True)
        self.geometry_table.setRowCount(0)
        self.geometry_rows = []
        for f in self.index["faces"]:
            self.table_row(self.geometry_table, ["Patch", f["id"], str(f["degrees"])])
            self.geometry_rows.append(("face", f["id"]))
        for e in self.index["edges"]:
            self.table_row(self.geometry_table, ["Interfaces" if e["kind"] == "coupling" else "Edge", e["id"], str(e["face_ids"])])
            self.geometry_rows.append((e["kind"], e["id"]))
        self.geometry_table.blockSignals(False)
        self.material_table.setRowCount(0)
        for m in self.project["materials"]:
            self.table_row(self.material_table, [m[k] for k in ("id", "name", "young", "poisson", "density", "thickness")])
        self.patch_table.setRowCount(0)
        for p in self.project["patches"]:
            element = element_selector()
            element.setCurrentIndex(element.findData(p["element"]))
            self.table_row(self.patch_table, [p["face_id"], p["material_id"], element, p["degree_u"], p["degree_v"], p["insert_u"], p["insert_v"], p["quadrature"]], (0,))
        edges = {e["id"]: e for e in self.index["edges"]}
        self.coupling_table.setRowCount(0)
        for c in self.project["couplings"]:
            enabled, rotation = W.QCheckBox(), W.QCheckBox()
            enabled.setChecked(c["enabled"])
            rotation.setChecked(c.get("rotation", False))
            self.table_row(self.coupling_table, [c["edge_id"], str(edges[c["edge_id"]]["face_ids"]), enabled, c["penalty"], rotation], (0, 1))
        for category in ("supports", "loads", "initial_conditions"):
            self.refresh_conditions(category)
        self.units.setCurrentText(self.project["units"])
        for key in ("start_time", "end_time", "time_step"):
            getattr(self, key).setText(str(self.project["solver"][key]))
        self.solver_type.setCurrentIndex(self.solver_type.findData(self.project["solver"]["type"]))
        self.analysis_type.setCurrentIndex(self.analysis_type.findData(self.project["solver"]["analysis_type"]))
        self.vtk.setChecked(self.project["output"]["vtk"])
        self.output_refinement.setValue(self.project["output"]["refinement"])
        self.summary.setText(f"{Path(self.project['source_step']).name}   |   Patches: {len(self.index['faces'])}   |   Interfaces: {len(self.project['couplings'])}")
        for table in (self.geometry_table, self.material_table, self.patch_table, self.coupling_table):
            table.resizeColumnsToContents()

    def sync(self):
        if not self.cache:
            raise ValueError("Import STEP geometry first.")
        if not self.doc or self.doc.Name not in App.listDocuments():
            raise ValueError("The analysis document is closed. Reimport geometry or reopen the project.")
        for name, signature in self.display_fingerprints.items():
            obj = self.doc.getObject(name)
            if obj is None or (obj.Shape.hashCode(), str(obj.Placement)) != signature:
                raise ValueError("The displayed CAD geometry has changed. Save the modified model as STEP and reimport it.")
        def cell(table, row, col):
            return table.item(row, col).text().strip()
        self.project["materials"] = [{"id": int(cell(self.material_table, r, 0)), "name": cell(self.material_table, r, 1),
            **{key: float(cell(self.material_table, r, c)) for c, key in enumerate(("young", "poisson", "density", "thickness"), 2)}} for r in range(self.material_table.rowCount())]
        self.project["patches"] = [{"face_id": int(cell(self.patch_table, r, 0)), "material_id": int(cell(self.patch_table, r, 1)),
            "element": self.patch_table.cellWidget(r, 2).currentData(),
            **{key: int(cell(self.patch_table, r, c)) for c, key in enumerate(("degree_u", "degree_v", "insert_u", "insert_v", "quadrature"), 3)}} for r in range(self.patch_table.rowCount())]
        self.project["couplings"] = [{"edge_id": int(cell(self.coupling_table, r, 0)), "enabled": self.coupling_table.cellWidget(r, 2).isChecked(),
            "penalty": float(cell(self.coupling_table, r, 3)), "rotation": self.coupling_table.cellWidget(r, 4).isChecked()} for r in range(self.coupling_table.rowCount())]
        self.project["units"] = self.units.currentText()
        self.project["solver"] = {"type": self.solver_type.currentData(), "analysis_type": self.analysis_type.currentData(),
            **{k: float(getattr(self, k).text()) for k in ("start_time", "end_time", "time_step")}}
        self.project["output"] = {"vtk": self.vtk.isChecked(), "refinement": self.output_refinement.value()}

    def add_material(self):
        ids = [int(self.material_table.item(r, 0).text()) for r in range(self.material_table.rowCount())]
        self.table_row(self.material_table, [max(ids, default=0)+1, "Material", 210000, 0.3, 7.85e-9, 1.0])

    def delete_row(self, table):
        if table.currentRow() >= 0:
            table.removeRow(table.currentRow())

    def select_table_geometry(self):
        if not self.doc:
            return
        Gui.Selection.clearSelection()
        for row in sorted({i.row() for i in self.geometry_table.selectedItems()}):
            key = self.geometry_rows[row]
            Gui.Selection.addSelection(self.doc.Name, self.entities[key])

    def selections(self):
        result = []
        for selection in Gui.Selection.getSelectionEx():
            obj = selection.Object
            if not self.doc or obj.Document.Name != self.doc.Name or not hasattr(obj, "IbraBrepId"):
                continue
            kind, ident = obj.IbraKind, obj.IbraBrepId
            target = {"target_kind": kind, "target_id": ident}
            if kind == "face" and selection.SubElementNames:
                subs = selection.SubElementNames
                if len(subs) != 1:
                    raise ValueError("Assign subedges or vertices of a patch individually, or select independent boundary entities in the geometry table.")
                sub = obj.Shape.getElement(subs[0])
                if subs[0].startswith("Vertex"):
                    face = next(f for f in self.index["faces"] if f["id"] == ident)
                    matches = [side for side, xyz in face["corners"].items() if (sub.Point-App.Vector(*xyz)).Length < 1e-6]
                    if len(matches) != 1:
                        raise ValueError("The vertex does not uniquely match a natural NURBS corner. Strong enforcement at general trimming vertices is not supported.")
                    target["side"] = matches[0]
                elif subs[0].startswith("Edge"):
                    matches = []
                    for edge in self.index["edges"]:
                        if edge["kind"] != "edge" or edge["face_ids"] != [ident]:
                            continue
                        points = edge["points"]
                        if all(sub.distToShape(Part.Vertex(App.Vector(*p)))[0] < 1e-5 for p in points) and abs(sub.Length-sum((App.Vector(*b)-App.Vector(*a)).Length for a, b in zip(points, points[1:]))) < max(1e-4, sub.Length*.01):
                            matches.append(edge["id"])
                    if len(matches) != 1:
                        raise ValueError("Boundary matching is ambiguous. Select the boundary by its B-rep ID in the geometry table.")
                    target = {"target_kind": "edge", "target_id": matches[0]}
            if target not in result:
                result.append(target)
        return result

    def capture(self, category):
        def operation():
            targets = self.selections()
            if not targets:
                raise ValueError("Select an analysis patch or a one-sided boundary.")
            if any(t["target_kind"] == "coupling" for t in targets):
                raise ValueError("Define interface conditions in Interfaces. For supports or loads, select a one-sided patch boundary.")
            if category == "initial_conditions" and any(t["target_kind"] != "face" for t in targets):
                raise ValueError("Select a patch for initial conditions.")
            setattr(self, category+"_targets", targets)
            getattr(self, category+"_target").setText("; ".join(f"{t['target_kind']} {t['target_id']}"+(" · corner "+t["side"] if "side" in t else "") for t in targets))
            if category == "supports" and len(targets) == 1 and "side" in targets[0]:
                self.support_side.setCurrentIndex(self.support_side.findData(targets[0]["side"]))
        self.guard(operation)

    def add_condition(self, category):
        def operation():
            targets = getattr(self, category+"_targets")
            if not targets:
                raise ValueError("Assign selected entities first.")
            name = getattr(self, category+"_name").text().strip()
            if not name:
                raise ValueError("A condition name is required.")
            records = []
            for index, target in enumerate(targets):
                record = dict(target, name=name if len(targets) == 1 else name+"_"+str(index+1))
                if category == "supports":
                    record.update({"method": "strong" if self.support_method.currentIndex() == 0 else "penalty", "value": self.support_vector.get(),
                        "penalty": float(self.penalty.text()), "clamp": self.clamp.isChecked(), "side": target.get("side", self.support_side.currentData())})
                elif category == "loads":
                    record.update({"variable": self.load_variable.currentText(), "value": self.load_vector.get()})
                else:
                    record.update({"variable": self.initial_variable.currentText(), "value": self.initial_vector.get()})
                if category != "initial_conditions":
                    a, b = getattr(self, category+"_interval")
                    record["interval"] = [float(a.text()), "End" if b.text().strip() == "End" else float(b.text())]
                records.append(record)
            if category in self.editing:
                if len(records) != 1:
                    raise ValueError("Assign one target entity when editing a single condition.")
                self.project[category][self.editing.pop(category)] = records[0]
            else:
                self.project[category].extend(records)
            self.refresh_conditions(category)
            self.status.setText("Condition recorded | Geometric and formulation compatibility will be validated before export.")
        self.guard(operation)

    def refresh_conditions(self, category):
        items = getattr(self, category+"_list")
        items.clear()
        for r in self.project[category]:
            items.addItem(f"{r['name']}  ·  {r['target_kind']} {r['target_id']}  ·  {r.get('method', r.get('variable'))}  {r['value']}")

    def delete_condition(self, category):
        row = getattr(self, category+"_list").currentRow()
        if row >= 0:
            self.project[category].pop(row)
            self.editing.pop(category, None)
            self.refresh_conditions(category)

    def edit_condition(self, category, row):
        record = self.project[category][row]
        self.editing[category] = row
        getattr(self, category+"_name").setText(record["name"])
        setattr(self, category+"_targets", [{k: record[k] for k in ("target_kind", "target_id")}])
        getattr(self, category+"_target").setText(f"{record['target_kind']} {record['target_id']}")
        if category == "supports":
            self.support_method.setCurrentIndex(0 if record["method"] == "strong" else 1)
            self.support_side.setCurrentIndex(self.support_side.findData(record.get("side", "u0")))
            self.support_vector.set(record["value"])
            self.penalty.setText(str(record["penalty"]))
            self.clamp.setChecked(record.get("clamp", False))
        elif category == "loads":
            self.load_variable.setCurrentText(record["variable"])
            self.load_vector.set(record["value"])
        else:
            self.initial_variable.setCurrentText(record["variable"])
            self.initial_vector.set(record["value"])
        if category != "initial_conditions":
            for widget, value in zip(getattr(self, category+"_interval"), record["interval"]):
                widget.setText(str(value))

    def check_project(self):
        def operation():
            self.sync()
            errors, warnings = validate(self.project, self.index)
            self.log.appendPlainText("\n".join(["Error: "+e for e in errors]+["Notice: "+w for w in warnings]))
            self.status.setText(f"Errors: {len(errors)} | Notices: {len(warnings)}")
            if errors:
                self.log.show()
                self.log_toggle.setText("Hide Log")
            return not errors
        return self.guard(operation)

    def save_project(self):
        def operation():
            self.sync()
            path, _ = W.QFileDialog.getSaveFileName(self, "Save Analysis Project", str(self.work_dir/"project.ibra-project.json"), "IBRA Project (*.ibra-project.json);;Project JSON (*.json)")
            if path:
                write_project(path, self.project)
                self.status.setText("Project saved: "+path)
        self.guard(operation)

    def open_project(self):
        def operation():
            path, _ = W.QFileDialog.getOpenFileName(self, "Open Analysis Project", str(self.work_dir), "IBRA Project (*.ibra-project.json);;Project JSON (*.json)")
            if not path:
                return
            self.load_project(path)
        self.guard(operation)

    def load_project(self, path):
        project = read_project(path)
        if project.get("schema_version") != 1:
            raise ValueError("Unsupported project schema version.")
        if hashlib.sha256(Path(project["source_step"]).read_bytes()).hexdigest() != project["source_sha256"]:
            raise ValueError("The STEP checksum has changed; previous boundary conditions cannot be reused.")
        self.pending_project = project
        self.profile.setText(project.get("profile", ""))
        self.start_import(project["source_step"])

    def save_backend_configuration(self):
        if self.process:
            raise ValueError("Wait for the current background task to finish.")
        self.settings = save_settings(self.user_data, {
            "backend_python": self.backend_python.text().strip(),
            "pipeline_root": self.pipeline_root.text().strip(),
            "work_dir": self.work_directory.text().strip()})
        self.work_dir = Path(self.settings["work_dir"])
        self.status.setText("Backend configuration saved.")

    def check_backend_environment(self):
        self.save_backend_configuration()
        self.log.show()
        self.log_toggle.setText("Hide Log")
        self.start_process([str(BASE/"backend/check_environment.py")],
                           lambda: self.status.setText("Backend environment verified."))

    def export_case(self):
        if not self.check_project():
            return
        folder = W.QFileDialog.getExistingDirectory(self, "Select Parent Directory for a New Analysis Model", str(Path.home()))
        if not folder:
            return
        destination = Path(folder)/("ibra_case_"+uuid.uuid4().hex[:8])
        project_file = self.cache/("project_"+uuid.uuid4().hex+".json")
        write_json(project_file, self.project)
        def done():
            self.last_export = destination
            self.status.setText("Analysis model exported: "+str(destination))
        self.guard(lambda: self.start_worker({"action": "export", "cache_dir": str(self.cache), "project_file": str(project_file), "destination": str(destination)}, done))

    def export_geometry(self):
        def operation():
            if not self.cache:
                raise ValueError("Import STEP geometry first.")
            path, _ = W.QFileDialog.getSaveFileName(self, "Export Source CAD Geometry (mm)", str(Path.home()/"geometry.cad.json"), "CAD JSON (*.cad.json)")
            if path:
                write_json(path, read_json(self.cache/"geometry.cad.json"))
                self.status.setText("CAD geometry exported (mm; analysis settings excluded): "+path)
        self.guard(operation)

    def run_case(self, check):
        def operation():
            if not self.last_export:
                raise ValueError("Export a complete analysis model first.")
            arguments = [str(self.last_export/"MainKratos.py")]+(["--check"] if check else [])
            self.start_process(arguments, lambda: self.status.setText(("Model initialization completed: " if check else "Analysis completed: ")+str(self.last_export)))
        self.guard(operation)


def show_panel():
    global _panel
    if _panel is None:
        _panel = Panel(Gui.getMainWindow())
        Gui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, _panel)
    _panel.show()
    _panel.raise_()
    return _panel


class OpenCommand:
    def GetResources(self):
        return {"MenuText": "IBRA Preprocessor", "ToolTip": "Open the IBRA preprocessor for Kratos", "Pixmap": str(BASE/"Resources"/"iga.svg")}

    def IsActive(self):
        return True

    def Activated(self):
        show_panel()
