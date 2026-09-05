"""Depot charger assignment: rule-based only (no LLM) — greedy
lowest-battery-first onto any free, online charger.

Kept deterministic on purpose: the problem statement's "improve" step adds
per-vehicle agents negotiating for charger slots against time-of-use rates;
this baseline just proves the eval loop (cost, missed-departure tracking)
works before that negotiation layer exists.
"""
from sim import LOW_BATTERY, DEPOT


def schedule_charging(sim):
    waiting = [
        v for v in sim.vehicles
        if v.status == "idle" and v.pos == DEPOT and v.battery < LOW_BATTERY
    ]
    waiting.sort(key=lambda v: v.battery)
    free_chargers = [c for c in sim.chargers if c.busy_with is None and c.offline_until < sim.tick]
    for v, c in zip(waiting, free_chargers):
        v.status = "charging"
        c.busy_with = v.id
        sim.event(f"vehicle {v.id} (battery {v.battery:.0f}%) assigned to charger {c.id} at {sim.rate():.2f}$/%")


if __name__ == "__main__":
    from sim import Simulation
    sim = Simulation(num_vehicles=4, seed=1)
    for v in sim.vehicles:
        v.battery = 20.0
    schedule_charging(sim)
    charging = [v for v in sim.vehicles if v.status == "charging"]
    assert len(charging) == min(4, len(sim.chargers)), "expected one vehicle per free charger"
    print(f"self-check passed: {len(charging)} of {len(sim.vehicles)} low-battery vehicles assigned to chargers.")
