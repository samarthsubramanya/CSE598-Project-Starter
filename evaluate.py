"""Run the simulation for a fixed horizon with a steady stream of synthetic
orders and random incidents, then grade the result: on-time delivery %,
total km driven, total charging cost. Regression self-check asserts a
floor so the methodology is validated before the real project scopes in
per-vehicle negotiating agents.

Two output modes:
- trace_run(): one concrete, human-readable test case — full event log
  (dispatch decisions, incidents, deliveries, charging) plus periodic
  ASCII grid snapshots. This is the "test case and baseline output" the
  proposal's Section 4 grades.
- run(): the metrics-only aggregate used across multiple seeds.
"""
from sim import Simulation, render_grid
from dispatch_agent import dispatch
from charge_scheduler import schedule_charging

TICKS = 120
ORDER_EVERY = 4
SNAPSHOT_EVERY = 30


def _drive(sim, next_order_id):
    if sim.tick % ORDER_EVERY == 0:
        sim.spawn_order(next_order_id)
        next_order_id += 1
    sim.step(dispatch, schedule_charging)
    return next_order_id


def run(seed=0, num_vehicles=6):
    """Metrics only — used for the multi-seed aggregate table."""
    sim = Simulation(num_vehicles=num_vehicles, seed=seed)
    next_order_id = 0
    for _ in range(TICKS):
        next_order_id = _drive(sim, next_order_id)
    return _grade(sim)


def _grade(sim):
    delivered = [o for o in sim.orders.values() if o.delivered_tick is not None]
    on_time = [o for o in delivered if o.delivered_tick <= o.deadline]
    return {
        "orders_created": len(sim.orders),
        "orders_delivered": len(delivered),
        "on_time_pct": 100.0 * len(on_time) / len(sim.orders) if sim.orders else 0.0,
        "total_km": sum(v.km_driven for v in sim.vehicles),
        "charging_cost": round(sim.charging_cost, 2),
    }


def trace_run(seed=3, num_vehicles=6):
    """One fully-narrated run: event log + grid snapshots + final metrics.
    Returns the report text (also written to trace.txt)."""
    sim = Simulation(num_vehicles=num_vehicles, seed=seed)
    next_order_id = 0
    snapshots = []
    for _ in range(TICKS):
        next_order_id = _drive(sim, next_order_id)
        if sim.tick % SNAPSHOT_EVERY == 0:
            snapshots.append(render_grid(sim))

    metrics = _grade(sim)
    lines = [
        f"=== Trace run (seed={seed}, {num_vehicles} vehicles, {TICKS} ticks) ===",
        "",
        "-- Event log --",
        *sim.log,
        "",
        "-- Grid snapshots (D=depot, digit=vehicle id, (n)=vehicle n charging) --",
    ]
    lines += [s + "\n" for s in snapshots]
    lines += [
        "-- Final metrics --",
        f"orders created:    {metrics['orders_created']}",
        f"orders delivered:  {metrics['orders_delivered']}",
        f"on-time %:         {metrics['on_time_pct']:.1f}",
        f"total km driven:   {metrics['total_km']:.0f}",
        f"charging cost:     ${metrics['charging_cost']:.2f}",
    ]
    return "\n".join(lines)


def _table(results):
    header = f"{'seed':>4} {'created':>8} {'delivered':>10} {'on-time %':>10} {'km':>8} {'cost $':>8}"
    rows = [header, "-" * len(header)]
    for i, r in enumerate(results):
        rows.append(
            f"{i:>4} {r['orders_created']:>8} {r['orders_delivered']:>10} "
            f"{r['on_time_pct']:>10.1f} {r['total_km']:>8.0f} {r['charging_cost']:>8.2f}"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    trace = trace_run(seed=3)
    with open("trace.txt", "w") as f:
        f.write(trace + "\n")
    print(trace)
    print()

    results = [run(seed=s) for s in range(5)]
    avg_on_time = sum(r["on_time_pct"] for r in results) / len(results)
    table = _table(results)

    with open("results.txt", "w") as f:
        f.write(table + "\n")
        summary = f"avg on-time % across {len(results)} seeds: {avg_on_time:.1f}"
        f.write(summary + "\n")

    print("=== Aggregate across 5 seeds ===")
    print(table)
    print(f"avg on-time % across {len(results)} seeds: {avg_on_time:.1f}")

    assert avg_on_time >= 50.0, f"on-time rate floor not met: {avg_on_time:.1f}% < 50%"
    print("self-check passed: greedy baseline clears the 50% on-time floor.")
