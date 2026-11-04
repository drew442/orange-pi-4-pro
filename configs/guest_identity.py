"""Pure, fixed numeric identities for A733 guest service accounts."""
import json
import re

LIMIT = 65536


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def validate_config(data):
    require(isinstance(data, (str, bytes)) and 0 < len(data) <= LIMIT, 'invalid config size')
    value = json.loads(data, object_pairs_hook=unique_pairs)
    require(type(value) is dict and set(value) == {'schema', 'guests'} and
            type(value['schema']) is int and value['schema'] == 1, 'invalid identity schema')
    guests = value['guests']
    require(type(guests) is list and 1 <= len(guests) <= 16, 'one to sixteen guests required')
    names, numbers = set(), set()
    for guest in guests:
        require(type(guest) is dict and set(guest) == {'name', 'uid', 'gid'}, 'invalid guest fields')
        name, uid, gid = guest['name'], guest['uid'], guest['gid']
        require(isinstance(name, str) and re.fullmatch(r'[a-z](?:[a-z0-9-]{0,14}[a-z0-9])?', name)
                is not None, 'invalid guest name')
        require(type(uid) is int and type(gid) is int and uid == gid and
                60000 <= uid <= 60999, 'fixed equal UID/GID in 60000..60999 required')
        require(name not in names and uid not in numbers, 'duplicate guest identity')
        names.add(name)
        numbers.add(uid)
    return {'schema': 1, 'guests': sorted(guests, key=lambda guest: guest['name'])}


def render(config):
    config = validate_config(json.dumps(config))
    lines = []
    for guest in config['guests']:
        account = 'a733-vm-' + guest['name']
        lines.extend([f'g {account} {guest["gid"]}',
                      f'u {account} {guest["uid"]}:{guest["gid"]} "A733 guest {guest["name"]}" '
                      '/nonexistent /usr/sbin/nologin'])
    return lines
