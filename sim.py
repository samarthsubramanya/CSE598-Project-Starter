"""City-grid fleet simulation: vehicles, depot chargers, orders, incidents.

Discrete-tick simulation. Each tick: vehicles move one grid step toward
their target, chargers add battery, then policy hooks (dispatch + charge
scheduling, both pluggable) react to the new state.
"""
import random
from dataclasses import dataclass, field

GRID_SIZE = 10
DEPOT = (0, 0)
NUM_CHARGERS = 3
CHARGE_RATE = 20        # % battery per tick while charging
DRAIN_PER_UNIT = 2      # % battery per grid unit moved
LOW_BATTERY = 45        # vehicle heads to depot to charge below this
PEAK_TICKS = range(20, 40)
RATE_PEAK = 0.40        # $ per % battery charged
RATE_OFFPEAK = 0.15


def distance(a, b):
    """Manhattan distance — the one tool an LLM dispatcher is given."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def render_grid(sim):
    """ASCII snapshot of the grid: D=depot, digit=vehicle id (charging
    vehicles are parenthesized), .=empty. Gives a human a quick visual
    read of the fleet's spatial state at a given tick."""
    cells = {DEPOT: "D"}
    for v in sim.vehicles:
        marker = f"({v.id})" if v.status == "charging" else str(v.id)
        cells[v.pos] = marker if v.pos not in cells or v.pos != DEPOT else f"D{v.id}"
    rows = []
    for y in range(GRID_SIZE, -1, -1):
        row = " ".join(f"{cells.get((x, y), '.'):>3}" for x in range(GRID_SIZE + 1))
        rows.append(row)
    fleet_line = "  ".join(
        f"v{v.id}: pos={v.pos} batt={v.battery:5.1f}% status={v.status}"
        for v in sim.vehicles
    )
    return f"t={sim.tick}\n" + "\n".join(rows) + "\n" + fleet_line


@dataclass
class Order:
    id: int
    dest: tuple
    created_tick: int
    deadline: int
    assigned_to: int = None
    delivered_tick: int = None


@dataclass
class Vehicle:
    id: int
    pos: tuple = DEPOT
    battery: float = 100.0
    status: str = "idle"   # idle, enroute, returning, charging, broken
    target: tuple = None
    order_id: int = None
    km_driven: float = 0.0


@dataclass
class Charger:
    id: int
    busy_with: int = None
    offline_until: int = -1


class Simulation:
    def __init__(self, num_vehicles=6, seed=0):
        self.rng = random.Random(seed)
        self.vehicles = [Vehicle(id=i) for i in range(num_vehicles)]
        self.chargers = [Charger(id=i) for i in range(NUM_CHARGERS)]
        self.orders = {}
        self.tick = 0
        self.charging_cost = 0.0
        self.log = []   # human-readable event trace, for descriptive test output

    def event(self, text):
        self.log.append(f"t={self.tick:>3}  {text}")

    def rate(self):
        return RATE_PEAK if self.tick in PEAK_TICKS else RATE_OFFPEAK

    def add_order(self, order_id, dest, deadline):
        """Insert an order with an explicit dest/deadline — used by demo.py
        for a fully deterministic, hand-scripted scenario. spawn_order()
        below is the randomized version evaluate.py uses at scale."""
        o = Order(id=order_id, dest=dest, created_tick=self.tick, deadline=deadline)
        self.orders[order_id] = o
        self.event(f"order {order_id:>2} created  -> dest {dest}, deadline t={deadline}")
        return o

    def spawn_order(self, order_id):
        dest = (self.rng.randint(1, GRID_SIZE), self.rng.randint(1, GRID_SIZE))
        # deadline = round-trip travel time from depot + slack, so it's
        # achievable if a vehicle is dispatched promptly, not an arbitrary
        # fixed window unrelated to distance.
        window = distance(DEPOT, dest) + self.rng.randint(10, 20)
        return self.add_order(order_id, dest, self.tick + window)

    def trigger_breakdown(self, vehicle_id):
        """Deterministically break down one vehicle — the hand-scripted
        counterpart to maybe_incident()'s random breakdown."""
        v = self.vehicles[vehicle_id]
        v.status = "broken"
        self.event(
            f"INCIDENT: vehicle {v.id} broke down at {v.pos}"
            + (f" (order {v.order_id} needs redispatch)" if v.order_id is not None else "")
        )
        if v.order_id is not None:
            self.orders[v.order_id].assigned_to = None
            v.order_id = None

    def maybe_incident(self, p_breakdown=0.02, p_charger_fail=0.01):
        for v in self.vehicles:
            if v.status == "enroute" and self.rng.random() < p_breakdown:
                v.status = "broken"
                self.event(f"INCIDENT: vehicle {v.id} broke down at {v.pos}"
                            + (f" (order {v.order_id} needs redispatch)" if v.order_id is not None else ""))
                if v.order_id is not None:
                    self.orders[v.order_id].assigned_to = None
                    v.order_id = None
        for c in self.chargers:
            if c.busy_with is None and c.offline_until < self.tick and self.rng.random() < p_charger_fail:
                c.offline_until = self.tick + self.rng.randint(3, 8)
                self.event(f"INCIDENT: charger {c.id} offline until t={c.offline_until}")

    def _step_vehicle(self, v):
        if v.status in ("idle", "broken", "charging"):
            return
        if v.pos == v.target:
            if v.status == "enroute":
                o = self.orders[v.order_id]
                o.delivered_tick = self.tick
                on_time = "ON TIME" if self.tick <= o.deadline else "LATE"
                self.event(f"order {o.id:>2} delivered by vehicle {v.id} at {v.pos} -- {on_time} (deadline t={o.deadline})")
                v.order_id = None
                v.status = "returning"
                v.target = DEPOT
            elif v.status == "returning":
                v.status = "idle"
                v.target = None
            return
        step = tuple(
            p + (1 if t > p else -1 if t < p else 0)
            for p, t in zip(v.pos, v.target)
        )
        v.pos = step
        v.km_driven += 1
        v.battery = max(0.0, v.battery - DRAIN_PER_UNIT)

    def _step_chargers(self):
        for c in self.chargers:
            if c.busy_with is None:
                continue
            v = self.vehicles[c.busy_with]
            gained = min(CHARGE_RATE, 100.0 - v.battery)
            v.battery += gained
            self.charging_cost += gained * self.rate()
            if v.battery >= 100.0:
                v.status = "idle"
                c.busy_with = None
                self.event(f"vehicle {v.id} finished charging on charger {c.id} (battery 100%)")

    def step(self, dispatch_fn, charge_fn):
        for v in self.vehicles:
            self._step_vehicle(v)
        self._step_chargers()
        self.maybe_incident()
        dispatch_fn(self)
        charge_fn(self)
        self.tick += 1
