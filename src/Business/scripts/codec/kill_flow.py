"""Instance kill feed, streak announcements and actor/camp scoreboard updates."""
import struct

INSTANCE_COMMAND, KILL_EVENT = 10, 6
SCRIPT_COMMAND, SCRIPT_UPDATE_ITEM_MSG = 23, 1
BLUE_KILLS_KEY, RED_KILLS_KEY = 7346418, 206159  # Client script-name keys: blue, red.
CONTINUOUS_KILL_COMMAND, CONTINUOUS_KILL_EVENT = 19, 1
SCORE_COMMAND, SCORE_UPDATE_EVENT, SCORE_STAT_MSG = 12, 1, 2
MAX_STREAK = MAX_SCORE = 0x7FFFFFFF


def pack(layout, *values):
    if any(type(value) is not int for value in values):
        raise ValueError('kill notification fields must be integers')
    try:
        return struct.pack(layout, *values)
    except struct.error as error:
        raise ValueError('kill notification field is outside its TDR wire range') from error


def encode_kill_event(*, server_time_ms, source_rid, target_rid, weapon_tid, hit_part=0):
    return pack('>HQQQii', KILL_EVENT, server_time_ms, source_rid, target_rid, weapon_tid, hit_part)


def encode_camp_kill_update(*, server_time_ms, instance_id, blue_kills, red_kills):
    if any(type(value) is not int or not 0 <= value <= MAX_SCORE for value in (blue_kills, red_kills)):
        raise ValueError('camp score values must fit a nonnegative int32')
    # SCRIPT items use int32 name keys and int64 absolute values, not ordinal IDs.
    return pack('>HQQiiqiq', SCRIPT_UPDATE_ITEM_MSG, server_time_ms, instance_id, 2,
                BLUE_KILLS_KEY, blue_kills, RED_KILLS_KEY, red_kills)


def encode_continuous_kill_event(*, entity_rid, count):
    if type(count) is not int or not 0 <= count <= MAX_STREAK:
        raise ValueError('continuous kill count must fit a nonnegative int32')
    return pack('>HQi', CONTINUOUS_KILL_EVENT, entity_rid, count)


def encode_score_stat_message(*, server_time_ms, entity_rid, count):
    if type(count) is not int or not 2 <= count <= MAX_STREAK:
        raise ValueError('kill streak announcement requires a count of at least two')
    # The score resource has no 710100 row; 710110 supplies the ten-plus banner.
    messageId = 710110 if count >= 10 else 710000 + count * 10
    return pack('>HQQii', SCORE_STAT_MSG, server_time_ms, entity_rid, messageId, count)


def encode_score_update_event(*, server_time_ms, entity_rid, previous_kills, kills, previous_points, points,
                              previous_deaths=0, deaths=0):
    if any(type(value) is not int or not 0 <= value <= MAX_SCORE
           for value in (previous_kills, kills, previous_points, points, previous_deaths, deaths)):
        raise ValueError('score values must fit a nonnegative int32')
    # Modes 7/10/18 read ALL_KILL_NUM (44), others read KILL_NUM (2), never both.
    # Publish the same absolute total for either consumer, not two kill increments.
    # stat_msg_id=0 updates the board without requesting an additional announcement.
    return pack('>HQQii12i', SCORE_UPDATE_EVENT, server_time_ms, entity_rid, 0, 4,
                2, previous_kills, kills, 44, previous_kills, kills,
                3, previous_deaths, deaths, 1, previous_points, points)
