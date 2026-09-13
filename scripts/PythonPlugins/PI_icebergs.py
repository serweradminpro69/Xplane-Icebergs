"""
PI_icebergs.py -- Realistic iceberg placement plugin for X-Plane 12
Requires: XPPython3 (https://xppython3.readthedocs.io/)

Install location (GLOBAL plugin, works anywhere in the world):
    <X-Plane 12>/Resources/plugins/PythonPlugins/PI_icebergs.py

Object files expected at (your EXISTING models/ folder -- nothing to move):
    <X-Plane 12>/Custom Scenery/Xplane-Icebergs/models/size1.obj ... size6.obj
    <X-Plane 12>/Custom Scenery/Xplane-Icebergs/models/bigboy1.obj, bigboy2.obj, bigboy4.obj

    Optional snow-dusted variants, same geometry/different texture, dropped
    in that same models/ folder next to the originals:
    <X-Plane 12>/Custom Scenery/Xplane-Icebergs/models/size1_snow.obj  (etc.)
    If a "_snow" file doesn't exist for a given model, the plugin silently
    falls back to the regular one -- so this works today with zero extra
    setup, and you can add snow variants later without touching the code.

WHAT THIS DOES:
    - Every few seconds, checks the user aircraft's lat/lon.
    - Determines if that position falls inside a real-world iceberg region
      (Iceberg Alley / Davis Strait / Greenland fjords / Antarctic Southern Ocean / etc).
    - Determines if it's currently "iceberg season" for that region, using
      X-Plane's own sim/time/local_date_days dataref (so it reflects the
      sim's actual date, not just real-world wall clock).
    - If both match, scatters icebergs around the aircraft on WATER ONLY
      (verified per-point via XPLMProbeTerrainXYZ's wet flag -- this also
      means it naturally works over orthophoto scenery, since ortho only
      changes ground textures, not the terrain mesh probe results).
    - Picks clean or snow-dusted object variants based on the sim's real
      current_season dataref.
    - Culls/spawns objects as the aircraft moves, capped at MAX_ICEBERGS
      for performance.
    - Adds a Plugins menu toggle ("Icebergs: ON/OFF") so the user can
      disable it entirely at runtime; preference persists across sessions
      in icebergs_config.txt next to this file.

CAVEATS (read before flying):
    - This is written for correctness of approach, not tested against a
      live X-Plane install -- dataref names/signatures should be verified
      against your installed XPPython3 version if anything errors on load
      (check Log.txt / XPPython3Log.txt).
    - Region boundaries and season windows below are rough approximations
      of real iceberg geography, not survey-grade drift model data.
    - Distance-from-shore/density weighting is a simplification -- real
      iceberg drift follows ocean currents, not a simple distance falloff.
"""

import os
import random
import math
import configparser

import xp

PLUGIN_NAME = "Xplane-Icebergs"
PLUGIN_SIG = "icebergs.serweradminpro69.placement"
PLUGIN_DESC = "Realistic seasonal iceberg placement"

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

SCENERY_ROOT = os.path.join(
    "Custom Scenery", "Xplane-Icebergs", "models"
)  # relative to X-Plane root -- this is your EXISTING models/ folder,
   # the same one size1-6.obj and bigboy1-4.obj already live in.
   # No clean/ or snow/ subfolders needed for this to work today (see
   # get_object_path below for how snow variants are optionally picked up).

UPDATE_INTERVAL_SEC = 6.0          # how often we re-evaluate (don't do this every frame)
SPAWN_RADIUS_M = 60000.0           # scatter icebergs within this radius of the plane -- maxed out
DESPAWN_RADIUS_M = 120000.0        # destroy instances beyond this radius -- wide hysteresis buffer
MAX_ICEBERGS = 3000                # hard cap -- doubled again, 2fps cost reported as negligible
MIN_SPACING_M = 25.0               # tighter clustering, close to real ice-field packing

# Ice melange / calving-front jam mode: real fjords near an active glacier
# front get choked wall-to-wall with brash ice and bergy bits -- a totally
# different density regime than open-ocean drift. Triggered when the
# aircraft is within MELANGE_TRIGGER_RADIUS_M of a coastline/land probe hit
# while still inside an iceberg region.
#
# Hysteresis: entering melange mode uses a SMALLER radius than leaving it
# (MELANGE_EXIT_RADIUS_M > MELANGE_TRIGGER_RADIUS_M). Without this gap, flying
# a route that repeatedly crosses the trigger distance flips the mode every
# ~6 seconds, which changes target counts/spacing rules mid-flight and reads
# as bergs "regenerating." With the gap, a mode switch only happens when you
# clearly commit to one side or the other.
MELANGE_MODE = True
MELANGE_TRIGGER_RADIUS_M = 3000.0  # distance to shore that triggers ENTERING jam mode
MELANGE_EXIT_RADIUS_M = 6000.0     # must be this far from shore before LEAVING jam mode
MELANGE_SPACING_M = 2.0            # packed almost edge-to-edge, real ice-jam density
MELANGE_MAX_COUNT = 10000          # pushed higher again for a genuinely dense field
MELANGE_RADIUS_M = 9000.0          # jam patch radius -- maxed out

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "icebergs_config.txt")

# Flat / tabular models, weighted. Near land (ice melange) uses a
# small-dominant pool; open ocean uses the full range with bigger models
# much rarer, matching how real bergs get smaller/more numerous the longer
# they've drifted, and rarer/larger the closer to their calving source.
#
# CAVEAT (still true): size6 and bigboy4 are the confirmed SCALE-BROKEN
# outliers from the STL import bug (592x907m and 171x148x248m). Re-including
# them here because you asked for them in the open-ocean pool specifically --
# they will still look wrong next to everything else until fixed in Blender.
# This is not "rare but correct," it's "rare because broken, and now visible
# again." Fix their scale whenever you get to it and nothing else here needs
# to change.

MELANGE_MODELS_WEIGHTED = [
    ("size1", 60), ("size2", 50), ("size3", 3), ("size4", 1), ("size5", 0.3),
]

OCEAN_FLAT_MODELS_WEIGHTED = [
    ("size1", 30), ("size2", 26), ("size3", 5), ("size4", 2), ("size5", 1), ("size6", 0.5),
]

# Near-shore pinnacle pool deliberately excludes bigboy4 entirely (confirmed
# scale-broken, and you asked for it gone near shore specifically) as well
# as bigboy3 (overly dense remesh). Only the two clean pinnacle models are
# eligible near land, and even then only rarely (see PINNACLE_CHANCE_MELANGE).
MELANGE_PINNACLE_MODELS_WEIGHTED = [
    ("bigboy1", 1), ("bigboy2", 1),
]

# Open ocean can still use bigboy4 (rare, and still visibly broken-scale --
# your call to keep it out here too if it looks wrong)
OCEAN_PINNACLE_MODELS_WEIGHTED = [
    ("bigboy1", 2), ("bigboy2", 2), ("bigboy4", 1),
]

PINNACLE_CHANCE = 0.03            # open ocean: rare, dramatic tall bergs
PINNACLE_CHANCE_MELANGE = 0.006   # near shore: a few dozen/hundred out of thousands, not zero anymore --
                                   # real ice fields do get the occasional big grounded/stranded berg mixed
                                   # into the small stuff, just far less often than out at sea

# ---------------------------------------------------------------------------
# REGIONS
# Each region: name, lat/lon bounding box, season window (day-of-year, using
# sim/time/local_date_days: Jan 1 = 1 ... Dec 31 = 365), relative density,
# and a size bias ("small" = mostly growlers/bergy bits, "large" = bigger
# tabular bergs closer to their calving source).
#
# Day-of-year reference (from X-Plane dataref documentation):
#   Jan1=1 Feb1=32 Mar1=60 Apr1=91 May1=121 Jun1=152 Jul1=182
#   Aug1=213 Sep1=244 Oct1=274 Nov1=305 Dec1=335
# ---------------------------------------------------------------------------

REGIONS = [
    {
        "name": "Iceberg Alley (Labrador/Newfoundland)",
        "lat_range": (46.0, 60.0),
        "lon_range": (-64.0, -47.0),
        "season": (32, 213),      # Feb - end of July, peak May/June
        "density": 1.0,
        "size_bias": "small",     # mostly growlers/bergy bits; 85% already melted by the time they arrive
    },
    {
        "name": "Davis Strait / Baffin Bay",
        "lat_range": (60.0, 72.0),
        "lon_range": (-70.0, -50.0),
        "season": (1, 365),       # near year-round this far north
        "density": 0.8,
        "size_bias": "medium",
    },
    {
        "name": "Greenland fjords / Denmark Strait",
        "lat_range": (60.0, 70.0),
        "lon_range": (-45.0, -18.0),
        "season": (1, 365),
        "density": 0.9,
        "size_bias": "large",     # closer to the actual calving glaciers
    },
    {
        "name": "Svalbard / Barents Sea",
        "lat_range": (74.0, 81.0),
        "lon_range": (5.0, 35.0),
        "season": (1, 365),
        "density": 0.5,
        "size_bias": "medium",
    },
    {
        "name": "Antarctic / Southern Ocean",
        "lat_range": (-78.0, -55.0),
        "lon_range": (-180.0, 180.0),
        "season": (1, 365),       # Antarctic bergs are present essentially year-round
        "density": 1.0,
        "size_bias": "large",     # Antarctic tabular bergs are enormous compared to Northern Hemisphere bergs
    },
]

# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------

class Iceberg:
    __slots__ = ("instance", "lat", "lon", "x", "y", "z")
    def __init__(self, instance, lat, lon, x, y, z):
        self.instance = instance
        self.lat = lat
        self.lon = lon
        self.x, self.y, self.z = x, y, z


class IcebergPlugin:
    def __init__(self):
        self.enabled = True
        self.icebergs = []          # list[Iceberg]
        self.obj_refs = {}          # path -> XPLMObjectRef (loaded lazily, kept forever)
        self.flight_loop_id = None
        self.menu_id = None
        self.menu_toggle_idx = None
        self.probe = None
        self.in_melange = False     # sticky state -- see near_land() for hysteresis logic

    # -- lifecycle -----------------------------------------------------

    def start(self):
        self.load_config()
        self.probe = xp.createProbe(xp.ProbeY)
        self.flight_loop_id = xp.createFlightLoop(self.flight_loop_callback)
        xp.scheduleFlightLoop(self.flight_loop_id, UPDATE_INTERVAL_SEC, True)
        self.build_menu()
        xp.log(f"{PLUGIN_NAME}: started, enabled={self.enabled}")

    def stop(self):
        self.despawn_all()
        if self.flight_loop_id:
            xp.destroyFlightLoop(self.flight_loop_id)
        if self.probe:
            xp.destroyProbe(self.probe)

    # -- config persistence ---------------------------------------------

    def load_config(self):
        cfg = configparser.ConfigParser()
        if os.path.exists(CONFIG_FILE):
            cfg.read(CONFIG_FILE)
            self.enabled = cfg.getboolean("icebergs", "enabled", fallback=True)

    def save_config(self):
        cfg = configparser.ConfigParser()
        cfg["icebergs"] = {"enabled": str(self.enabled)}
        with open(CONFIG_FILE, "w") as f:
            cfg.write(f)

    # -- menu -------------------------------------------------------------

    def build_menu(self):
        self.menu_id = xp.createMenu("Icebergs", handler=self.menu_handler)
        self.refresh_menu_label()

    def refresh_menu_label(self):
        if self.menu_toggle_idx is not None:
            xp.removeMenuItem(self.menu_id, self.menu_toggle_idx)
        label = "Disable Icebergs" if self.enabled else "Enable Icebergs"
        self.menu_toggle_idx = xp.appendMenuItem(self.menu_id, label, 1)

    def menu_handler(self, menu_ref, item_ref):
        if item_ref == 1:
            self.enabled = not self.enabled
            self.save_config()
            self.refresh_menu_label()
            if not self.enabled:
                self.despawn_all()
            xp.log(f"{PLUGIN_NAME}: enabled={self.enabled}")

    # -- geography / season -----------------------------------------------

    def find_region(self, lat, lon):
        for region in REGIONS:
            lat_lo, lat_hi = region["lat_range"]
            lon_lo, lon_hi = region["lon_range"]
            if lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi:
                return region
        return None

    def in_season(self, region):
        day_ref = xp.findDataRef("sim/time/local_date_days")
        day = xp.getDatai(day_ref) if day_ref else 182
        lo, hi = region["season"]
        if lo <= hi:
            return lo <= day <= hi
        return day >= lo or day <= hi  # wraps around year boundary

    def is_winter(self):
        # Public XP12 dataref: 0=winter 1=spring 2=summer 3=fall (verify against
        # your XPPython3/X-Plane version -- name has been stable since XP11.50).
        ref = xp.findDataRef("sim/graphics/scenery/current_season")
        if ref is None:
            return False
        return xp.getDatai(ref) == 0

    # -- object loading -----------------------------------------------------

    def get_object_path(self, model_name, snow):
        system_path = xp.getSystemPath()
        base_dir = os.path.join(system_path, SCENERY_ROOT)
        if snow:
            # Optional: if you later export a snow-dusted texture variant,
            # save it as e.g. models/size1_snow.obj (same geometry, different
            # texture) and it'll be picked up automatically in winter.
            snow_path = os.path.join(base_dir, model_name + "_snow.obj")
            if os.path.exists(snow_path):
                return snow_path
        # Default / fallback: your existing plain files, e.g. models/size1.obj
        return os.path.join(base_dir, model_name + ".obj")

    def get_obj_ref(self, model_name, snow):
        path = self.get_object_path(model_name, snow)
        if path in self.obj_refs:
            return self.obj_refs[path]
        if not os.path.exists(path):
            xp.log(f"{PLUGIN_NAME}: missing object file, skipping: {path}")
            return None
        ref = xp.loadObject(path)
        self.obj_refs[path] = ref
        return ref

    # -- spawning / culling ---------------------------------------------

    def despawn_all(self):
        for berg in self.icebergs:
            xp.destroyInstance(berg.instance)
        self.icebergs = []

    def near_land(self, x, y, z):
        """Sample a ring of points around (x,y,z) -- if any comes back as
        land, we're close to a coastline/calving front. 16 rays (up from 8)
        for finer-grained detection along irregular coastlines. Uses
        hysteresis: the radius used to ENTER melange mode is smaller than
        the radius used to LEAVE it, and self.in_melange is sticky between
        calls, so flying near (but not decisively past) the threshold
        doesn't flip the mode every single loop tick."""
        check_radius = MELANGE_EXIT_RADIUS_M if self.in_melange else MELANGE_TRIGGER_RADIUS_M
        found_land = False
        for i in range(16):
            angle = (2 * math.pi / 16) * i
            sx = x + check_radius * math.cos(angle)
            sz = z + check_radius * math.sin(angle)
            info = xp.probeTerrainXYZ(self.probe, sx, y, sz)
            if info is not None and not getattr(info, "is_wet", True):
                found_land = True
                break
        self.in_melange = found_land
        return found_land

    def too_close_to_existing(self, x, y, z, min_spacing):
        for berg in self.icebergs:
            dx, dy, dz = x - berg.x, y - berg.y, z - berg.z
            if (dx * dx + dy * dy + dz * dz) ** 0.5 < min_spacing:
                return True
        return False

    def pick_model(self, size_bias, melange):
        chance = PINNACLE_CHANCE_MELANGE if melange else PINNACLE_CHANCE
        pinnacle_pool = MELANGE_PINNACLE_MODELS_WEIGHTED if melange else OCEAN_PINNACLE_MODELS_WEIGHTED
        if pinnacle_pool and random.random() < chance:
            names, weights = zip(*pinnacle_pool)
            return random.choices(names, weights=weights, k=1)[0]
        pool = MELANGE_MODELS_WEIGHTED if melange else OCEAN_FLAT_MODELS_WEIGHTED
        names, weights = zip(*pool)
        return random.choices(names, weights=weights, k=1)[0]

    STACK_CHANCE = 0.04  # ~4% of spawns skip the spacing check entirely -- lets ice
                          # pile up/overlap like real rafted, jammed sea ice instead
                          # of every piece staying politely separated

    def try_spawn_one(self, plane_x, plane_y, plane_z, snow, size_bias, melange):
        if melange:
            radius_max = MELANGE_RADIUS_M
            min_spacing = MELANGE_SPACING_M
            near_dist = 5.0   # allow spawning almost right where the check originates,
                              # including hard up against a shoreline -- real ice edges
                              # do touch/graze the coast, they don't stay in a clean ring
        else:
            radius_max = SPAWN_RADIUS_M
            min_spacing = MIN_SPACING_M
            near_dist = 2000.0

        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(near_dist, radius_max)
        ox = plane_x + dist * math.cos(angle)
        oz = plane_z + dist * math.sin(angle)

        info = xp.probeTerrainXYZ(self.probe, ox, plane_y, oz)
        if info is None or not getattr(info, "is_wet", False):
            return  # land or probe failure -- skip

        oy = info.locationY
        allow_stack = random.random() < self.STACK_CHANCE
        if not allow_stack and self.too_close_to_existing(ox, oy, oz, min_spacing):
            return

        model_name = self.pick_model(size_bias, melange)
        obj_ref = self.get_obj_ref(model_name, snow)
        if obj_ref is None:
            return

        instance = xp.createInstance(obj_ref, [])
        heading = random.uniform(0, 360)
        # small random vertical jitter when stacking so overlapping pieces don't
        # perfectly z-fight -- mimics one floe riding up slightly on another
        stack_y = oy + (random.uniform(0.1, 0.6) if allow_stack else 0.0)
        xp.instanceSetPosition(instance, (ox, stack_y, oz, 0, heading, 0), [])
        self.icebergs.append(Iceberg(instance, 0, 0, ox, stack_y, oz))

    def cull_far_instances(self, plane_x, plane_y, plane_z):
        kept = []
        for berg in self.icebergs:
            dx, dy, dz = plane_x - berg.x, plane_y - berg.y, plane_z - berg.z
            dist = (dx * dx + dy * dy + dz * dz) ** 0.5
            if dist > DESPAWN_RADIUS_M:
                xp.destroyInstance(berg.instance)
            else:
                kept.append(berg)
        self.icebergs = kept

    # -- main loop -----------------------------------------------------------

    def flight_loop_callback(self, since_last, since_loop, counter, refcon):
        if not self.enabled:
            return UPDATE_INTERVAL_SEC

        lat = xp.getDatad(xp.findDataRef("sim/flightmodel/position/latitude"))
        lon = xp.getDatad(xp.findDataRef("sim/flightmodel/position/longitude"))
        plane_x = xp.getDatad(xp.findDataRef("sim/flightmodel/position/local_x"))
        plane_y = xp.getDatad(xp.findDataRef("sim/flightmodel/position/local_y"))
        plane_z = xp.getDatad(xp.findDataRef("sim/flightmodel/position/local_z"))

        self.cull_far_instances(plane_x, plane_y, plane_z)

        region = self.find_region(lat, lon)
        if region is None or not self.in_season(region):
            return UPDATE_INTERVAL_SEC  # not in an iceberg zone/season -- do nothing further

        snow = self.is_winter()
        melange = MELANGE_MODE and self.near_land(plane_x, plane_y, plane_z)
        target_count = MELANGE_MAX_COUNT if melange else int(MAX_ICEBERGS * region["density"])

        # Fill the ENTIRE target in one pass instead of trickling a few in per
        # tick -- attempts allowance is generous (several x target) so that
        # spacing-collision rejections still converge to a full field in a
        # single flight_loop call, rather than visibly "growing" over many
        # seconds. Once len(self.icebergs) reaches target_count this loop
        # exits immediately and does nothing further until you move far
        # enough to need new bergs.
        attempts = 0
        max_attempts = target_count * 6
        while len(self.icebergs) < target_count and attempts < max_attempts:
            self.try_spawn_one(plane_x, plane_y, plane_z, snow, region["size_bias"], melange)
            attempts += 1

        return UPDATE_INTERVAL_SEC


plugin = IcebergPlugin()


class PythonInterface:
    """XPPython3 requires plugins to expose exactly this class name, with
    these methods -- top-level module functions are NOT recognized by this
    version of XPPython3, only class methods."""

    def XPluginStart(self):
        return PLUGIN_NAME, PLUGIN_SIG, PLUGIN_DESC

    def XPluginEnable(self):
        plugin.start()
        return 1

    def XPluginDisable(self):
        plugin.stop()

    def XPluginStop(self):
        pass
