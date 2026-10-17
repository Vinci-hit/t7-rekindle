"""Bounded remote playback clock and arrival-jitter estimator."""
import math

SNAPSHOT_MS = 50
BASE_DELAY_MS = 2 * SNAPSHOT_MS
MAX_DELAY_MS = 180
JITTER_WINDOW = 32
MAX_RATE_CHANGE = .1
GROUND_EXTRAPOLATION_MS = 100
AIR_EXTRAPOLATION_MS = 150
MAX_EXTRAPOLATION_SPEED = 25.
# One lost 100 ms monster sample (or three lost 50 ms player samples) must not disable prediction.
MAX_EXTRAPOLATION_SPAN_MS = 250
# Monsters publish at 10 Hz: bridge one lost sample (next one is due 200 ms after the last received).
MONSTER_EXTRAPOLATION_MS = 250


def observe(state, tick, arrived):
    previous = state.get('arrival')
    jitter = state.setdefault('jitter', [])
    if previous is not None and tick > previous[0]:
        jitter.append(min(MAX_DELAY_MS - BASE_DELAY_MS,
                          abs((arrived - previous[1]) - (tick - previous[0]))))
        del jitter[:-JITTER_WINDOW]
    state['arrival'] = [tick, arrived]
    ordered = sorted(jitter)
    p95 = ordered[max(0, math.ceil(len(ordered) * .95) - 1)] if ordered else 0
    state['delayMs'] = BASE_DELAY_MS + p95


def advance(state, now, earliest):
    target = now - state['clockOffset'] - state.get('delayMs', BASE_DELAY_MS)
    previous = state.get('playbackAt')
    if previous is None:
        at = max(earliest, target)
    else:
        elapsed = max(0, now - previous)
        normal = state['renderAt'] + elapsed
        correction = max(-elapsed * MAX_RATE_CHANGE,
                         min(elapsed * MAX_RATE_CHANGE, target - normal))
        at = max(state['renderAt'], normal + correction)
    state['playbackAt'] = now
    state['renderAt'] = at
    return at


def extrapolate(before, after, at, airborne=False, limit=None):
    pose = dict(after['pose'])
    if pose.get('motion') is not None:
        pose['motion'] = dict(pose['motion'], velocity=[0., 0., 0.])
    span = after['at'] - before['at']
    age = at - after['at']
    limit = limit or (AIR_EXTRAPOLATION_MS if airborne else GROUND_EXTRAPOLATION_MS)
    if not 0 < span <= MAX_EXTRAPOLATION_SPAN_MS or age <= 0:
        return pose
    exhausted = age >= limit
    age = min(age, limit)
    velocity = [(b - a) * 1000 / span
                for a, b in zip(before['pose']['position'], after['pose']['position'])]
    if math.sqrt(sum(v * v for v in velocity)) > MAX_EXTRAPOLATION_SPEED:
        return pose
    # Ground snapshots cannot predict a vertical trajectory or cross a landing.
    if not airborne:
        velocity[2] = 0.
    if pose.get('motion') is not None and not exhausted:
        pose['motion']['velocity'] = velocity
    pose['position'] = [p + v * age / 1000 for p, v in zip(pose['position'], velocity)]
    return pose
