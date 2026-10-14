"""Explicit native query results for Business tests; no distance-based hit inference."""


def contact(target=2, *, time=1., along=.5, position=(1., 0., 1.),
            normal=(-1., 0., 0.), direction=(1., 0., 0.), material=0, defenceWeaponId=0):
    return dict(targetId=target, ready=True, hit=True, material=material,
                position=list(position), normal=list(normal), direction=list(direction), time=time, along=along,
                defenceWeaponId=defenceWeaponId)


def publish(flow, actorId, hits, *, ready=True, spanMs=20):
    trace = dict(ready=ready, spanMs=spanMs, length=1.03, base=[0., 0., 1.],
                 tip=[1.03, 0., 1.], contacts=hits)
    flow.context['botWorld'].setdefault('blades', {})[str(actorId)] = trace
    return trace
