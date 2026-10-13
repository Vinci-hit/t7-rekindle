"""Native weapon slot requests and full equipment-use broadcasts (command 5)."""
import struct

WEAPON_COMMAND = 5
CHANGE_REQUEST, CHANGE_RESPONSE, USE_UPDATE = 1, 2, 3
MAIN_SLOT, FIST_SLOT = 1, 13
CANNOT_CHANGE, INVALID_SLOT = 620000, 620001


def decode_change_request(body):
    if type(body) is not bytes or len(body) != 4:
        raise ValueError('weapon change request requires exactly 4 bytes')
    selector, slot = struct.unpack('>Hh', body)
    if selector != CHANGE_REQUEST:
        raise ValueError('unexpected weapon change selector')
    return slot


def encode_change_response(result, right_weapon_id):
    return struct.pack('>Hiii', CHANGE_RESPONSE, result, right_weapon_id, 0)


def encode_use_update(*, actor_id, main_weapon_id, fist_weapon_id, equipped_weapon_id, now_ms):
    entries = b''.join(struct.pack('>iBBB', tid, slot, 0, int(tid == equipped_weapon_id)) + bytes(22)
                       for tid, slot in ((main_weapon_id, MAIN_SLOT), (fist_weapon_id, FIST_SLOT)))
    return struct.pack('>HQh', USE_UPDATE, actor_id, 2) + entries + struct.pack('>hQ', 1, now_ms)
