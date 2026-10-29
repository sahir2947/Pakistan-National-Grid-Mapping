"""Official content checksums of the complete grid databases.

`pakgrid build` is deterministic: the same grid rules and border give the same
content on every machine, whatever the Python version or operating system.
So anyone can build the database themselves and prove it is the official one:
`pakgrid verify` recomputes the checksum over every stored record and compares
it with the value below.

The checksum covers the content (layout, every cell and sub-cell record,
border flags, and the border itself), not the file's bytes, which can differ
between SQLite versions.
"""

# (grid version, edition) -> content SHA-256 of a complete build
OFFICIAL = {
    (1, "full"): "f8da541f3865862a18ac25dded339c2a84eeea4b5ff26e59d27676a4309e0109",
    (1, "pakistan"): "6c5c2ec492a38f93020b593c554e9d437b39d859948a2cc82e94e6605c30cb01",
}


def official_checksum(meta):
    """The official content checksum for a database's grid version and edition,
    or None if it is a partial build (some quadrants or rows) or unknown."""
    if meta.get("coverage") != "full":
        return None
    try:
        version = int(meta.get("grid_version"))
    except (TypeError, ValueError):
        return None
    return OFFICIAL.get((version, meta.get("edition")))


# places version -> content SHA-256 of the places layer of a complete build. The same for the full
# and Pakistan editions: cells wholly outside Pakistan carry no place tags in either.
OFFICIAL_PLACES = {
    1: "61db16961ad906b21972bf2796aecbd29c13cd9f1db45577fc88ccb8bc4a21a9",
}


def official_places_checksum(meta):
    """The official checksum of a database's places layer, or None (partial build, no layer, unknown)."""
    if meta.get("coverage") != "full" or "places_version" not in meta:
        return None
    try:
        return OFFICIAL_PLACES.get(int(meta["places_version"]))
    except ValueError:
        return None
