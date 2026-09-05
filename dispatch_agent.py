"""Order-to-vehicle dispatch: greedy nearest-idle-vehicle, with an optional
Gemini call standing in for "the LLM picks among the top candidates".

Same pattern as degree_plan_baseline/llm_agent.py: the LLM call returns
None whenever a key isn't configured, the SDK is missing, or the response
can't be parsed — falling back to the rule-based nearest-vehicle pick.

Deliberately weak/inefficient by design, not by accident — this is the
baseline the real project needs to beat, not a preview of it:
- The LLM sees every idle vehicle, not just the battery-feasible ones the
  rule-based path restricts itself to (`_candidates` vs.
  `_llm_candidate_pool`) — so it can send an underpowered van the rule-based
  policy would have skipped.
- The prompt withholds precomputed distance/battery numbers, so the model
  has to estimate them from raw positions instead of being handed the
  answer — a plain "closest vehicle" heuristic would just compute this.
- It's called once per pending order per tick with no memoization or
  batching — an order that failed to parse last tick pays for a fresh API
  call again next tick, identical prompt, no caching.
"""
import json
import os
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sim import distance, DEPOT

MODEL = "gemini-2.5-flash"


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


def _llm_pick(order, candidates):
    """Returns (chosen_vehicle_or_None, failure_reason_or_None). The reason
    is surfaced by dispatch() into the event log — a silently-swallowed
    exception is exactly what made a rate-limited/misconfigured key look
    identical to "no key configured" when testing manually."""
    key = _api_key()
    if not key or not candidates:
        return None, None
    try:
        from google import genai
    except ImportError:
        return None, "google-genai package not installed"

    # Deliberately withholds the distance/battery numbers the rule-based
    # policy uses directly — the model has to estimate them from raw
    # coordinates, which a plain nearest-vehicle heuristic never needs to do.
    lines = "\n".join(f"- vehicle {v.id}: position {v.pos}" for v in candidates)
    prompt = f"""A delivery order needs to go to {order.dest}.
Pick a vehicle to send.
Candidates:
{lines}

Respond with ONLY the chosen vehicle id as an integer, nothing else."""
    try:
        from google.genai import types
        # Without an explicit timeout/retry cap, a blocked or slow network
        # path (e.g. a sandboxed dev environment with no outbound access)
        # makes this call hang or retry for minutes instead of falling back
        # — exactly the hang reported when GEMINI_API_KEY was first set.
        http_options = types.HttpOptions(
            timeout=10_000,  # ms — SDK's enforced floor; anything lower is rejected outright
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        client = genai.Client(api_key=key, http_options=http_options)
        response = client.models.generate_content(model=MODEL, contents=prompt)
        text = (response.text or "").strip()
        match = re.search(r"\d+", text)
        if not match:
            return None, f"unparseable response: {text[:80]!r}"
        vid = int(match.group(0))
        picked = next((v for v in candidates if v.id == vid), None)
        if picked is None:
            return None, f"model picked invalid/ineligible vehicle id {vid}"
        return picked, None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:150]}"


def dispatch(sim):
    """Assign every unassigned, undelivered order to a vehicle if one is
    available. Mutates sim in place — called once per tick."""
    pending = [o for o in sim.orders.values() if o.assigned_to is None and o.delivered_tick is None]
    for order in pending:
        llm_choice, llm_reason = _llm_pick(order, _llm_candidate_pool(sim))
        candidates = _candidates(sim)
        if llm_choice:
            chosen, source = llm_choice, "llm"
        elif candidates:
            chosen, source = _rule_based_pick(order, candidates), "rule"
            if llm_reason:
                sim.event(f"  llm unavailable for order {order.id}: {llm_reason}")
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
        picked, reason = _llm_pick(order, cands)
        assert picked is None and reason is None, "expected (None, None) with no configured API key"
        print("self-check passed: no API key configured, _llm_pick() correctly fell back to (None, None).")
