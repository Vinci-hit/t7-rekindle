"""Synthetic character properties; no client animation assets."""
import struct

from dash_fixture import tagInteger as integer, tagString as string

PROPERTIES = ('Speed_MoveF_Slow', 'Speed_MoveLR_Slow', 'Speed_MoveB_Slow', 'Speed_MoveF_Sprint')
VALUES = (2.25, 1.75, .75, 4.25)


def character(values=VALUES, names=PROPERTIES, types=None, references=(3, 4)):
    header = bytes.fromhex('1e0db0cacefa11d0') + b'\x02\x0a\x1chk_2014.2.5-r1\x15\x00\x01\x00\x14\x00'

    def definition(name, fields):
        return (integer(2) + string(name) + integer(0) * 2 + integer(len(fields))
                + b''.join(string(key) + integer(kind) + (string(subtype) if subtype else b'')
                           for key, kind, subtype in fields))

    definitions = (
        definition('hkbVariableInfo', [('type', 2, None)])
        + definition('hkbVariableValue', [('value', 2, None)])
        + definition('hkbCharacterData', [('characterPropertyInfos', 25, 'hkbVariableInfo'),
                                         ('stringData', 8, 'hkbCharacterStringData'),
                                         ('characterPropertyValues', 8, 'hkbVariableValueSet')])
        + definition('hkbCharacterStringData', [('characterPropertyNames', 26, None)])
        + definition('hkbVariableValueSet', [('wordVariableValues', 25, 'hkbVariableValue')])
    )
    # An unrelated object precedes the character: references, not encounter order, bind its properties.
    objects = integer(4) + integer(1) + b'\x01' + integer(5)
    objects += (integer(4) + integer(3) + b'\x07' + integer(len(names)) + b'\x01' + integer(4)
                + b''.join(integer(v) for v in (types or [4] * len(names)))
                + integer(references[0]) + integer(references[1]))
    objects += integer(4) + integer(4) + b'\x01' + integer(len(names)) + b''.join(string(v) for v in names)
    objects += (integer(4) + integer(5) + b'\x01' + integer(len(values)) + b'\x01' + integer(4)
                + b''.join(integer(struct.unpack('<i', struct.pack('<f', v))[0]) for v in values))
    return header + definitions + objects + integer(7)


def profile():
    return dict(enabled=True, weaponId=1030411, forwardSlow=VALUES[0], lateralSlow=VALUES[1],
                backwardSlow=VALUES[2], forwardSprint=VALUES[3])
