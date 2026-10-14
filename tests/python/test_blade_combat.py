from copy import deepcopy

import pytest

from Business.scripts import combat
from test_melee_combat import arena, hp, swing


def contact(target=2, *, time=.5, along=.5):
    return {"targetId": target, "ready": True, "hit": True, "material": 0, 'defenceWeaponId': 0,
            "position": [1., 0., 1.], "normal": [-1., 0., 0.], "direction": [1., 0., 0.],
            "time": time, "along": along}


def trace(flow, contacts, *, ready=True):
    world = flow.context["botWorld"]
    world["blades"] = {"1": {"ready": ready, "spanMs": 20, "length": 1.03,
                              "base": [0., 0., 1.], "tip": [1.03, 0., 1.], "contacts": contacts}}


def sample(flow, *, fresh=True):
    # This fixture describes a 20 ms sweep wholly inside the active strike.
    flow.now += 20
    if fresh:
        flow.context["botWorld"]["sampledAt"] = flow.now
    combat.worldUpdated(flow)


def test_native_contact_replaces_actor_distance_and_attack_facing(arena):
    swing(arena)
    arena.context["botWorld"].update(bot=[3., 0., 2.], playerHeading=180)
    trace(arena, [contact()])
    sample(arena)
    assert hp(arena, 2) == 80


@pytest.mark.parametrize("contacts,ready", [([], True), ([contact()], False)])
def test_nearby_actor_does_not_hit_without_a_ready_native_contact(arena, contacts, ready):
    swing(arena)
    trace(arena, contacts, ready=ready)
    sample(arena)
    assert hp(arena, 2) == 100


def test_missing_blade_data_never_falls_back_to_the_old_sector(arena):
    arena.context["botWorld"].pop("blades", None)
    swing(arena)
    sample(arena)
    assert hp(arena, 2) == 100


@pytest.mark.parametrize("first,second,health", [(0, 2, 100), (2, 0, 80)])
def test_wall_and_enemy_are_resolved_in_sweep_contact_order(arena, first, second, health):
    swing(arena)
    trace(arena, [contact(second, time=.8), contact(first, time=.2)])
    sample(arena)
    assert hp(arena, 2) == health
    assert any("combat-terrain" in log["message"] for log in arena.result["logs"])


def test_contact_feedback_uses_the_native_point_and_normal(arena):
    from Business.scripts.codec import battle_flow

    swing(arena)
    value = contact()
    value["position"] = [.75, .2, 1.7]
    trace(arena, [value])
    sample(arena)
    packet = next(p for p in arena.result["send"] if p["reason"] == "combat-hit-point")
    decoded = battle_flow.decode_battle_hit_point_notify(packet["body"])
    assert decoded.hit_pos == pytest.approx(value["position"])


@pytest.mark.parametrize("field,value", [("time", float("nan")), ("time", 1.1),
                                         ("along", -.1), ("targetId", 21),
                                         ("normal", [0., 0., 0.]), ("position", [float("inf"), 0., 1.])])
def test_invalid_native_contact_is_not_damage_evidence(arena, field, value):
    swing(arena)
    bad = deepcopy(contact())
    bad[field] = value
    trace(arena, [bad])
    sample(arena)
    assert hp(arena, 2) == 100


def test_sweep_from_before_the_attack_does_not_replay_during_the_window(arena):
    swing(arena)
    trace(arena, [contact(time=0)])
    arena.context["botWorld"]["sampledAt"] = arena.now + 5
    sample(arena, fresh=False)
    assert hp(arena, 2) == 100


@pytest.mark.parametrize("field,value", [("spanMs", 101), ("spanMs", -1), ("spanMs", True),
                                        ("length", float("nan")), ("length", 0),
                                        ("base", [float("inf"), 0, 0])])
def test_invalid_native_sweep_is_not_damage_evidence(arena, field, value):
    swing(arena)
    trace(arena, [contact()])
    arena.context["botWorld"]["blades"]["1"][field] = value
    sample(arena)
    assert hp(arena, 2) == 100
