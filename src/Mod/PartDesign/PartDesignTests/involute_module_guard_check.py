# SPDX-License-Identifier: LGPL-2.1-or-later
"""Model-level checks for the involute-gear module editor guard.

Runs without a FreeCAD build. The involute math and fcgear arc construction
are the real modules. Part/OCC is replaced by a recorder, so this does not
prove that OpenCascade accepts the resulting wire. It does prove:

* valid modules still reach the generator and scale the profile
* zero, negative, and the empty-field sentinel (~2.2e-308 mm) do not
* a later valid module rebuilds
* a real generator failure (pressure angle 90 deg, or an injected error)
  still propagates
"""

import os
import sys
import types
import unittest
from pathlib import Path

PARTDESIGN = Path(
    os.environ.get("INVOLUTE_PARTDESIGN", Path(__file__).resolve().parents[1])
).resolve()
REPO_ROOT = PARTDESIGN.parents[2] if len(PARTDESIGN.parents) > 2 else PARTDESIGN
if str(PARTDESIGN) not in sys.path:
    sys.path.insert(0, str(PARTDESIGN))

EMPTY_FIELD_MM = 2.2250738585072014e-308  # numeric_limits<double>::min()


class _Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]


class _Edge:
    def __init__(self, kind, points):
        self.kind = kind
        self.points = points

    def toShape(self):
        return self


class _Wire:
    def __init__(self, edges):
        self.edges = list(edges)

    def digest(self):
        coords = []
        for edge in self.edges:
            for point in edge.points:
                coords.append((round(point.x, 9), round(point.y, 9)))
        return (len(self.edges), tuple(coords[:8]), tuple(coords[-4:]))


class _Part:
    def __init__(self):
        self.wire_count = 0

    def LineSegment(self, start, end):
        return _Edge("line", (start, end))

    def Arc(self, start, mid, end):
        return _Edge("arc", (start, mid, end))

    def BezierCurve(self):
        edge = _Edge("bezier", [])

        def set_poles(poles):
            edge.points = tuple(poles)

        edge.setPoles = set_poles
        return edge

    def Wire(self, edges):
        self.wire_count += 1
        return _Wire(edges)


class _Console:
    def __init__(self):
        self.warnings = []

    def PrintWarning(self, message):
        self.warnings.append(message)


class _FreeCAD:
    GuiUp = False
    Console = _Console()


class _Quantity:
    def __init__(self, value):
        self.Value = float(value)


def _install_stubs():
    part = _Part()
    freecad = _FreeCAD()
    base = types.ModuleType("FreeCAD.Base")
    base.Vector = _Vector
    freecad_module = types.ModuleType("FreeCAD")
    freecad_module.GuiUp = False
    freecad_module.Console = freecad.Console
    freecad_module.Base = base
    pyside = types.ModuleType("PySide")
    qtcore = types.ModuleType("PySide.QtCore")

    def qt_translate_noop(_context, text):
        return text

    qtcore.QT_TRANSLATE_NOOP = qt_translate_noop
    pyside.QtCore = qtcore
    part_module = types.ModuleType("Part")
    part_module.LineSegment = part.LineSegment
    part_module.Arc = part.Arc
    part_module.BezierCurve = part.BezierCurve
    part_module.Wire = part.Wire
    sys.modules["FreeCAD"] = freecad_module
    sys.modules["FreeCAD.Base"] = base
    sys.modules["Part"] = part_module
    sys.modules["PySide"] = pyside
    sys.modules["PySide.QtCore"] = qtcore
    return part, freecad


class _Gear:
    def __init__(self, feature):
        self.feature = feature
        self.Modules = _Quantity(2.5)
        self.NumberOfTeeth = 26
        self.PressureAngle = _Quantity(20.0)
        self.HighPrecision = True
        self.ExternalGear = True
        self.AddendumCoefficient = 1.0
        self.DedendumCoefficient = 1.25
        self.RootFilletCoefficient = 0.38
        self.ProfileShiftCoefficient = 0.0
        self.Shape = None
        self.Proxy = self
        self.position_calls = 0

    def execute(self, obj):
        self.feature._InvoluteGear.execute(self, obj)

    def positionBySupport(self):
        self.position_calls += 1

    def __setattr__(self, name, value):
        if name == "Modules" and not isinstance(value, _Quantity):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raw = getattr(value, "Value", None)
                if not isinstance(raw, (int, float)):
                    raise TypeError(f"cannot store module from {value!r}")
                value = _Quantity(raw)
            else:
                value = _Quantity(value)
        object.__setattr__(self, name, value)


class InvoluteModuleGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.part, cls.freecad = _install_stubs()
        import InvoluteGearFeature as feature

        cls.feature = feature

    def setUp(self):
        self.part.wire_count = 0
        self.freecad.Console.warnings.clear()
        self.gear = _Gear(self.feature)
        self.gear.Proxy.execute(self.gear)
        self.baseline = self.gear.Shape.digest()
        self.baseline_wires = self.part.wire_count
        self.assertGreater(self.baseline[0], 0)

    def test_valid_modules_scale_and_recover(self):
        sizes = {}
        for module in (2.5, 1.0, 0.5, 0.1, 0.05, 1e-3, 1e-6, 2.0, 4.5):
            self.gear.Modules = module
            self.gear.Proxy.execute(self.gear)
            sizes[module] = self._span(self.gear.Shape)
            self.assertGreater(sizes[module], 0.0)
            self.assertEqual(self.freecad.Console.warnings, [])
        # Profile is proportional to the module.
        self.assertAlmostEqual(sizes[4.5] / sizes[2.5], 4.5 / 2.5, places=6)
        self.assertAlmostEqual(sizes[0.1] / sizes[2.5], 0.1 / 2.5, places=6)

        self.gear.ExternalGear = False
        self.gear.Modules = 2.5
        self.gear.Proxy.execute(self.gear)
        internal = self.gear.Shape.digest()
        self.assertNotEqual(internal, self.baseline)
        self.gear.HighPrecision = False
        self.gear.Proxy.execute(self.gear)
        self.assertNotEqual(self.gear.Shape.digest(), internal)

    def test_zero_negative_and_empty_sentinel_keep_shape(self):
        for bad in (0.0, -0.0, -1.0, -2.5, EMPTY_FIELD_MM, float("nan"), float("inf")):
            wires_before = self.part.wire_count
            warnings_before = len(self.freecad.Console.warnings)
            self.gear.Modules = bad
            self.gear.Proxy.execute(self.gear)
            self.assertEqual(self.gear.Shape.digest(), self.baseline)
            self.assertEqual(self.part.wire_count, wires_before)
            self.assertEqual(len(self.freecad.Console.warnings), warnings_before + 1)
            self.assertIn("previous shape", self.freecad.Console.warnings[-1])

    def test_editor_skips_transients_then_rebuilds(self):
        feature = self.feature
        for transient in (0, -0.0, -2.5, EMPTY_FIELD_MM, float("nan")):
            wires_before = self.part.wire_count
            applied = feature._apply_editor_value(self.gear, "Modules", transient)
            self.assertFalse(applied)
            self.assertAlmostEqual(self.gear.Modules.Value, 2.5)
            self.assertEqual(self.gear.Shape.digest(), self.baseline)
            self.assertEqual(self.part.wire_count, wires_before)
            self.assertEqual(self.freecad.Console.warnings, [])

        # Quantity payload, as emitted by valueChanged(Quantity).
        pending = _Quantity(EMPTY_FIELD_MM)
        self.assertFalse(feature._apply_editor_value(self.gear, "Modules", pending))
        self.assertAlmostEqual(self.gear.Modules.Value, 2.5)

        # Typing 4.5 can pass through "4" before the final value.
        self.assertTrue(feature._apply_editor_value(self.gear, "Modules", 4.0))
        interim = self.gear.Shape.digest()
        self.assertNotEqual(interim, self.baseline)
        self.assertTrue(feature._apply_editor_value(self.gear, "Modules", _Quantity(4.5)))
        self.assertAlmostEqual(self.gear.Modules.Value, 4.5)

        fresh = _Gear(self.feature)
        fresh.Modules = 4.5
        fresh.Proxy.execute(fresh)
        self.assertEqual(self.gear.Shape.digest(), fresh.Shape.digest())

        # Other properties are still applied.
        self.assertTrue(feature._apply_editor_value(self.gear, "NumberOfTeeth", 30))
        self.assertEqual(self.gear.NumberOfTeeth, 30)

    def test_constructible_module_does_not_swallow_generator_errors(self):
        self.gear.PressureAngle = _Quantity(90.0)
        with self.assertRaises(ValueError):
            self.gear.Proxy.execute(self.gear)
        self.assertEqual(self.gear.Shape.digest(), self.baseline)

        self.gear.PressureAngle = _Quantity(20.0)
        import fcgear.involute as involute

        real = involute.CreateExternalGear

        def explode(*_args, **_kwargs):
            raise RuntimeError("injected generator failure")

        involute.CreateExternalGear = explode
        try:
            with self.assertRaisesRegex(RuntimeError, "injected generator failure"):
                self.gear.Proxy.execute(self.gear)
        finally:
            involute.CreateExternalGear = real
        self.assertEqual(self.gear.Shape.digest(), self.baseline)

    def test_unpatched_generator_still_raises_for_bad_modules(self):
        """The guard must be the thing that stops the traceback, not a silent math change."""
        import fcgear.fcgear as fcgear
        import fcgear.involute as involute

        def build(module, internal=False):
            wire = fcgear.FCWireBuilder()
            function = involute.CreateInternalGear if internal else involute.CreateExternalGear
            function(
                wire,
                module,
                26,
                20.0,
                split=True,
                addCoeff=0.6 if internal else 1.0,
                dedCoeff=1.25,
                filletCoeff=0.38,
                shiftCoeff=0.0,
            )

        with self.assertRaises(ZeroDivisionError):
            build(0.0)
        with self.assertRaises(ValueError):
            build(-1.0)
        with self.assertRaises(ZeroDivisionError):
            build(EMPTY_FIELD_MM)
        with self.assertRaises(ZeroDivisionError):
            build(EMPTY_FIELD_MM, internal=True)
        build(2.5)
        build(1e-6)
        build(1e-6, internal=True)

    def _span(self, shape):
        xs = [point.x for edge in shape.edges for point in edge.points]
        ys = [point.y for edge in shape.edges for point in edge.points]
        return max(max(xs) - min(xs), max(ys) - min(ys))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(InvoluteModuleGuardTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"repo={REPO_ROOT}")
    print(f"empty_field_mm={EMPTY_FIELD_MM!r}")
    print(f"min_constructible_mm={InvoluteModuleGuardTest.feature._MIN_CONSTRUCTIBLE_MODULE_MM!r}")
    sys.exit(0 if result.wasSuccessful() else 1)
