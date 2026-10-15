from types import SimpleNamespace
import struct

from Business.scripts import contracts, pve


def test_great_wall_test_sends_map_resource_id():
    flow = SimpleNamespace(
        state={"pveZombieTest": True},
        context={},
    )

    assert pve.resourceId(flow) == pve.MAP_RESOURCE_ID
    assert struct.unpack_from(">i", contracts.instanceInfo(1, resourceId=pve.resourceId(flow)), 26)[0] == 10088


def test_pve_test_resource_override_is_opt_in():
    flow = SimpleNamespace(state={"pveZombieTest": False}, context={})

    assert pve.resourceId(flow) == contracts.RESOURCE_ID
