"""Fixed little-endian frames for the independent motion WebSocket."""
import math
import struct
import uuid

INPUT = struct.Struct('<4sQ16sI3fhbbBQQH3f16sQQQ')
OUTPUT = struct.Struct('<4s16s16sIQIQ3fhbbBQQH3f')
PREFIX = 'T7M1:'


def _pose(values):
    x, y, z, heading, forward, lateral, flags, tick, action_tick, action_id, vx, vy, vz = values
    if (flags & ~31 or not -180 <= heading <= 180 or not -1 <= forward <= 1
            or not -1 <= lateral <= 1 or flags & 1 and forward != -1
            or any(not math.isfinite(v) or abs(v) >= 100000 for v in (x, y, z))):
        raise ValueError('invalid binary motion pose')
    result = dict(position=[x, y, z], heading=heading, forwardBack=forward,
                  leftRight=lateral, running=bool(flags & 1))
    if flags & 2:
        if (action_tick > tick or flags & 8 and not flags & 4
                or any(not math.isfinite(v) or abs(v) > 40 for v in (vx, vy, vz))):
            raise ValueError('invalid binary motion frame')
        result['motion'] = dict(tick=tick, actionTick=action_tick, actionId=action_id,
            velocity=[vx, vy, vz], airborne=bool(flags & 4), jumping=bool(flags & 8))
    elif flags & 12 or any((tick, action_tick, action_id, vx, vy, vz)):
        raise ValueError('unexpected binary motion fields')
    return result


def encode_input(envelope):
    if envelope['protocolVersion'] != 1 or envelope['command']['type'] != 'poseInput':
        raise ValueError('binary motion requires pose input')
    payload = envelope['command']['payload']
    pose = payload['pose']
    motion = pose.get('motion')
    stamp = envelope.get('timeline')
    flags = int(pose['running']) | (2 if motion else 0) | (16 if stamp else 0)
    if stamp and stamp['source'] != 'nativePose':
        raise ValueError('invalid motion timeline source')
    if motion:
        flags |= int(motion['airborne']) << 2 | int(motion['jumping']) << 3
    frame = INPUT.pack(b'T7M1', int(envelope['requestId']), uuid.UUID(payload['matchId']).bytes,
        payload['inputSeq'], *pose['position'], pose['heading'], pose['forwardBack'], pose['leftRight'],
        flags, motion['tick'] if motion else 0, motion['actionTick'] if motion else 0,
        motion['actionId'] if motion else 0, *(motion['velocity'] if motion else (0., 0., 0.)),
        uuid.UUID(stamp['epoch']).bytes if stamp else bytes(16), stamp['combatSeq'] if stamp else 0,
        stamp['sampledMs'] if stamp else 0, stamp['gatewayMs'] if stamp else 0)
    return PREFIX + frame.hex()


def decode_input(frame):
    """Also used to inspect native gateway effects in diagnostics and tests."""
    if type(frame) is str:
        if not frame.startswith(PREFIX) or len(frame) != len(PREFIX) + INPUT.size * 2:
            raise ValueError('invalid binary motion envelope')
        frame = bytes.fromhex(frame[len(PREFIX):])
    if len(frame) != INPUT.size:
        raise ValueError('invalid binary motion length')
    magic, seq, match, input_seq, *values = INPUT.unpack(frame)
    if magic != b'T7M1' or not seq or not input_seq:
        raise ValueError('invalid binary motion header')
    pose = _pose(values[:13])
    epoch, combat, sampled, gateway = values[13:]
    flags = values[6]
    if sampled > gateway or not flags & 16 and (epoch != bytes(16) or any((combat, sampled, gateway))):
        raise ValueError('invalid binary motion timeline')
    result = dict(protocolVersion=1, requestId=str(seq), command=dict(type='poseInput', payload=dict(
        matchId=str(uuid.UUID(bytes=match)), inputSeq=input_seq, pose=pose)))
    if flags & 16:
        result['timeline'] = dict(epoch=str(uuid.UUID(bytes=epoch)), combatSeq=combat,
            sampledMs=sampled, gatewayMs=gateway, source='nativePose')
    return result


def decode_output(frame):
    if len(frame) != OUTPUT.size:
        raise ValueError('invalid binary snapshot length')
    magic, room, match, actor, event_seq, input_seq, server_time, *values = OUTPUT.unpack(frame)
    if magic != b'T7S1' or values[6] & 16 or not actor or not event_seq or not input_seq:
        raise ValueError('invalid binary snapshot header')
    update = dict(roomId=str(uuid.UUID(bytes=room)), matchId=str(uuid.UUID(bytes=match)),
        actorId=actor, eventSeq=event_seq, inputSeq=input_seq, serverTimeMs=server_time, pose=_pose(values))
    return dict(protocolVersion=1, requestId=None, event=dict(type='poseChanged', payload=dict(update=update)))
