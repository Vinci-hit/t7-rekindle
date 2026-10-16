"""Original command-11 task presentation; text is resolved from client resources."""
import struct

COMMAND = 11
SHOW_TRAIN_INFO = 3


def encode_task(*, task_id, current=0, total=0, server_time_ms):
    """Task zero hides the panel; counters do not grant server-side progress."""
    for value in (task_id, current, total):
        if type(value) is not int or not 0 <= value <= 0x7fffffff:
            raise ValueError('invalid native task field')
    if current > total or type(server_time_ms) is not int or not 0 <= server_time_ms < 2**64:
        raise ValueError('invalid native task counter or clock')
    return struct.pack('>HQiiii', SHOW_TRAIN_INFO, server_time_ms, task_id, 0, current, total)
