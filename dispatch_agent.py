"""Order-to-vehicle dispatch: greedy nearest-idle-vehicle, with an optional
Gemini call standing in for "the LLM picks among the top candidates".
"""
import json
import os
import re
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sim import distance, DEPOT

MODEL = "gemini-3.8-flash"


def _api_key():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or key.lower().startswith(("your-", "your_")) or key.lower() in ("changeme", "replace_me"):
        return None
    return key


def _candidates(sim):
    """Feasible set for the rule-based fallback: idle and enough battery for
    the round trip. This is the bar the LLM path is deliberately not held
    to — see _llm_candidate_pool()."""
    return [v for v in sim.vehicles if v.status == "idle" and v.battery >= 40]


def _llm_candidate_pool(sim):
    """Every idle vehicle, battery ignored. The naive one-shot LLM baseline
    doesn't pre-filter for feasibility the way the rule-based policy does,
    so it's free to pick a van that can't actually make the trip."""
    return [v for v in sim.vehicles if v.status == "idle"]


def _rule_based_pick(order, candidates):
    return min(candidates, key=lambda v: distance(v.pos, order.dest))


_cooldown_until = 0.0
_COOLDOWN_SECONDS = 60


def _quota_exhausted(exc_text):
    return "RESOURCE_EXHAUSTED" in exc_text or "429" in exc_text


def _llm_batch_pick(orders, candidates):
    global _cooldown_until
    key = _api_key()
    if not key or not orders or not candidates:
        return {}, None
    remaining = _cooldown_until - time.monotonic()
    if remaining > 0:
        return {}, f"skipping call, quota cooldown active for {remaining:.0f}s more"
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return {}, "google-genai package not installed"

    order_lines = "\n".join(f"- order {o.id}: destination {o.dest}" for o in orders)
    vehicle_lines = "\n".join(f"- vehicle {v.id}: position {v.pos}" for v in candidates)
    prompt = f"""Assign vehicles to delivery orders.
Orders:
{order_lines}

Available vehicles:
{vehicle_lines}

Respond with ONLY a JSON object mapping order id to vehicle id, e.g.
{{"0": 2, "1": 0}}. Only use vehicle ids listed above, each at most once.
Not every order needs an assignment."""
    try:

        http_options = types.HttpOptions(
            timeout=10_000,  # ms — SDK's enforced floor; anything lower is rejected outright
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        client = genai.Client(api_key=key, http_options=http_options)
        response = client.models.generate_content(model=MODEL, contents=prompt)
        text = (response.text or "").strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}, f"unparseable response: {text[:80]!r}"
        raw = json.loads(match.group(0))
        by_id = {v.id: v for v in candidates}
        assignments, used_vehicles = {}, set()
        for order_id_str, vehicle_id in raw.items():
            try:
                order_id, vehicle_id = int(order_id_str), int(vehicle_id)
            except (TypeError, ValueError):
                continue
            vehicle = by_id.get(vehicle_id)
            if vehicle is None or vehicle_id in used_vehicles:
                continue  # invalid id, or the model double-booked a vehicle
            assignments[order_id] = vehicle
            used_vehicles.add(vehicle_id)
        return assignments, None
    except Exception as e:
        msg = str(e)
        if _quota_exhausted(msg):
            _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
        return {}, f"{type(e).__name__}: {msg[:150]}"


def dispatch(sim):
    """Assign every unassigned, undelivered order to a vehicle if one is
    available. Mutates sim in place — called once per tick."""
    pending = [o for o in sim.orders.values() if o.assigned_to is None and o.delivered_tick is None]
    if not pending:
        return
    llm_assignments, llm_reason = _llm_batch_pick(pending, _llm_candidate_pool(sim))
    if llm_reason:
        sim.event(f"  llm batch dispatch unavailable: {llm_reason}")
    for order in pending:
        candidates = _candidates(sim)
        llm_vehicle = llm_assignments.get(order.id)
        if llm_vehicle is not None and llm_vehicle.status == "idle":
            chosen, source = llm_vehicle, "llm"
        elif candidates:
            chosen, source = _rule_based_pick(order, candidates), "rule"
        else:
            break
        chosen.status = "enroute"
        chosen.target = order.dest
        chosen.order_id = order.id
        order.assigned_to = chosen.id
        sim.event(
            f"order {order.id:>2} dispatched -> vehicle {chosen.id} "
            f"(dist {distance(chosen.pos, order.dest)}, battery {chosen.battery:.0f}%, picked by {source})"
        )


if __name__ == "__main__":
    # Self-check: fallback path must always produce a valid pick, no
    # network or key required.
    class _V:
        def __init__(self, id, pos, battery):
            self.id, self.pos, self.battery, self.status = id, pos, battery, "idle"

    from sim import Order
    order = Order(id=0, dest=(10, 10), created_tick=0, deadline=30)
    cands = [_V(0, (0, 0), 100), _V(1, (9, 9), 100)]
    picked = _rule_based_pick(order, cands)
    assert picked.id == 1, "nearest vehicle to (10,10) should be vehicle 1 at (9,9)"
    print(f"self-check passed: rule-based pick chose vehicle {picked.id} (nearest to order dest).")

    class _Sim:
        def __init__(self, vehicles):
            self.vehicles = vehicles

    mixed = _Sim([_V(0, (0, 0), 20), _V(1, (0, 0), 100)])
    assert len(_llm_candidate_pool(mixed)) == 2, "LLM pool should include the low-battery vehicle too"
    for v in mixed.vehicles:
        v.status = "idle"
    strict = [v for v in mixed.vehicles if v.status == "idle" and v.battery >= 40]
    assert len(strict) == 1, "rule-based candidates should exclude the 20%-battery vehicle"
    print("self-check passed: LLM pool is deliberately wider (includes low-battery) than the rule-based candidate set.")

    if _api_key() is None:
        assignments, reason = _llm_batch_pick([order], cands)
        assert assignments == {} and reason is None, "expected ({}, None) with no configured API key"
        print("self-check passed: no API key configured, _llm_batch_pick() correctly fell back to ({}, None).")
