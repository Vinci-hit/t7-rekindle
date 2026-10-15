"""PVE chapter progress carried by native login and chapter notifications."""
from dataclasses import dataclass
import struct

MAX_STAGES = 64
_STAGE = struct.Struct('>ibbi')


@dataclass(frozen=True, slots=True)
class StageProgress:
    stage_id: int
    unlocked: bool
    rewarded: bool = False
    completion: int = 0


def encode_progress(stages):
    if not isinstance(stages, tuple) or len(stages) > MAX_STAGES:
        raise ValueError('PVE progress must contain at most 64 stages')
    ids = set()
    records = []
    for stage in stages:
        if (not isinstance(stage, StageProgress) or type(stage.stage_id) is not int
                or not 1 <= stage.stage_id <= 0x7fffffff or stage.stage_id in ids
                or type(stage.unlocked) is not bool or type(stage.rewarded) is not bool
                or type(stage.completion) is not int or not 0 <= stage.completion <= 0x7fffffff):
            raise ValueError('invalid or duplicate PVE stage progress')
        ids.add(stage.stage_id)
        records.append(_STAGE.pack(stage.stage_id,stage.unlocked,stage.rewarded,stage.completion))
    return struct.pack('>h',len(stages)) + b''.join(records)


def decode_progress(data, offset):
    if offset < 0 or offset+2 > len(data):
        raise ValueError('truncated PVE stage count')
    count=struct.unpack_from('>h',data,offset)[0]
    offset+=2
    if not 0 <= count <= MAX_STAGES or offset+count*_STAGE.size > len(data):
        raise ValueError('invalid or truncated PVE progress')
    stages=[]
    for _ in range(count):
        stage,unlocked,rewarded,completion=_STAGE.unpack_from(data,offset)
        if unlocked not in (0,1) or rewarded not in (0,1):
            raise ValueError('invalid PVE stage flags')
        stages.append(StageProgress(stage,bool(unlocked),bool(rewarded),completion))
        offset+=_STAGE.size
    values=tuple(stages)
    encode_progress(values)
    return values,offset
