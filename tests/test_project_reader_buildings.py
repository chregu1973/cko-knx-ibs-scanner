from cko_ibs.project_reader import _building_tree


def test_building_tree_collects_rooms_functions_and_group_addresses() -> None:
    locations = {"b1": {"type": "Building", "name": "Feldacher", "devices": [], "functions": [], "spaces": {
        "f1": {"type": "Floor", "name": "EG", "devices": [], "functions": [], "spaces": {
            "r1": {"type": "Room", "name": "Küche", "usage_text": "Küche", "devices": ["1.1.3"], "functions": ["fn1"], "spaces": {}},
            "r2": {"type": "Room", "name": "Bad", "devices": [], "functions": [], "spaces": {}},
        }}}}}
    functions = {"fn1": {"name": "Licht Decke", "function_type": "SwitchableLight",
                         "group_addresses": {"a": {"address": "1/1/1", "role": "SwitchOnOff"}, "b": {"address": "1/2/1", "role": "InfoOnOff"}}}}
    devices = {"1.1.3": {"name": "Schaltaktor", "communication_object_ids": ["co1"]}}
    objects = {"co1": {"group_address_links": ["1/1/1", "1/1/9"]}}
    tree = _building_tree(locations, functions, devices, objects)
    building = tree[0]
    assert building["type_label"] == "Gebäude" and building["name"] == "Feldacher"
    floor = building["children"][0]
    kitchen = floor["children"][0]
    assert floor["type_label"] == "Etage" and kitchen["type_label"] == "Raum"
    assert kitchen["functions"][0]["name"] == "Licht Decke"
    assert kitchen["group_addresses"] == ["1/1/1", "1/1/9", "1/2/1"]
    assert kitchen["devices"] == [{"address": "1.1.3", "name": "Schaltaktor"}]
    assert floor["children"][1]["group_addresses"] == []


def test_building_tree_without_locations() -> None:
    assert _building_tree({}, {}, {}, {}) == []
