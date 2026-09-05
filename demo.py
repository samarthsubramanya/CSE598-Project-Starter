"""Deterministic worked example: fixed orders, a fixed incident, no
randomness anywhere — the concrete input/output test case for the
proposal doc. Contrast with evaluate.py, which exercises the same system
at scale across random seeds.

Fixed input:
- 3 vehicles, 2 chargers, a 12x12 grid (defaults from sim.py)
- 4 orders, hand-placed at specific ticks/destinations/deadlines
- 1 breakdown, hand-placed on a specific vehicle at a specific tick

Everything below this point is the *output*: the event log the system
produces from that fixed input, a final grid snapshot, and the delivery
tally.
"""
from sim import Simulation, render_grid
from dispatch_agent import dispatch
from charge_scheduler import schedule_charging

FIXED_ORDERS = {
    0: (4, 0),
    2: (0, 6),
    5: (3, 3),
    9: (6, 2),
}
BREAKDOWN_TICK = 6
BREAKDOWN_VEHICLE = 1
TICKS = 20


def main():
    sim = Simulation(num_vehicles=3, seed=0)
    sim.chargers = sim.chargers[:2]

    for t in range(TICKS):
        if t in FIXED_ORDERS:
            sim.add_order(order_id=t, dest=FIXED_ORDERS[t], deadline=t + 12)

        for v in sim.vehicles:
            sim._step_vehicle(v)
        sim._step_chargers()

        if t == BREAKDOWN_TICK:
            sim.trigger_breakdown(BREAKDOWN_VEHICLE)

        dispatch(sim)
        schedule_charging(sim)
        sim.tick += 1

    print("-- Event log --")
    print("\n".join(sim.log))
    print()
    print("-- Final grid --")
    print(render_grid(sim))
    print()

    delivered = [o for o in sim.orders.values() if o.delivered_tick is not None]
    on_time = [o for o in delivered if o.delivered_tick <= o.deadline]
    print(f"orders: {len(sim.orders)} created, {len(delivered)} delivered, {len(on_time)} on time")

    assert len(sim.orders) == len(FIXED_ORDERS), "expected exactly the 4 hand-placed orders"
    assert len(delivered) >= 3, f"expected at least 3 of 4 fixed orders delivered by t={TICKS}, got {len(delivered)}"
    assert any("INCIDENT" in line for line in sim.log), "expected the fixed breakdown to appear in the log"
    print("self-check passed: deterministic scenario delivers the expected orders and logs the fixed incident.")


if __name__ == "__main__":
    main()
