# Navigation rules (grid version 1)

The exact rules of pakgrid's rider navigation: straight-line distance and
direction to a target, and the navigation engine that turns a stream of GPS
and compass readings into what a screen shows. They need no grid database.

These rules are what the conformance file `navigation.json` tests. A port that
follows them passes it. The Python library (`pakgrid/navigate.py`,
`pakgrid/geodesy.py`) and the JavaScript copy (`viewer/pakgrid.js`) are both
implementations of these rules.

Conventions:

- Angles are in degrees unless a name says radians. Bearings and headings are
  clockwise from north: 0 = N, 90 = E, 180 = S, 270 = W.
- `x mod 360` is the **floored** modulo: the result is always in [0, 360), also
  for negative `x` (`-15 mod 360 = 345`). Many languages' `%` keeps the sign of
  `x` (JavaScript, C, Java); use `((x % 360) + 360) % 360` there. Dart's `%`
  on doubles already gives a non-negative result.
- `round_half_even(x)` rounds to the nearest integer, and exact halves to the
  even neighbour: 0.5 → 0, 1.5 → 2, 2.5 → 2, -2.5 → -2. **Not** half away from
  zero (Dart's and Java's `round()` give 2.5 → 3; that fails the file).
- All arithmetic is IEEE 754 double precision, done in the order written.
  Basic operations (`+ - * /`, `sqrt`) are exactly rounded, so they give the
  same result in every language; only `sin`, `cos`, `atan2` and `pow` may
  differ in the last digit (see §6).
- `FEET_PER_METRE = 3.280839895013123`: write this literal, which is
  `1 / 0.3048` divided in double precision. Feet are always
  `metres * FEET_PER_METRE`, never `metres / 0.3048`, and don't write the
  constant as `1 / 0.3048` in a language that folds constants exactly (Go
  gives 3.2808398950131235): a last-digit difference changes
  `format_distance` at a rounding tie, and the file tests this.

## 1. Scale of a degree (WGS 84)

```
a  = 6378137.0                    # semi-major axis, metres
f  = 1 / 298.257223563
e2 = f * (2 - f)

metres_per_degree(lat):
    phi = lat * pi / 180
    s   = sin(phi)
    M   = a * (1 - e2) / (1 - e2 * s * s) ^ 1.5      # meridian radius of curvature
    N   = a / sqrt(1 - e2 * s * s)                  # prime-vertical radius of curvature
    return (M * pi / 180,  N * cos(phi) * pi / 180)  # (lat_m, lon_m)
```

## 2. Direction from one point to another

```
direction(from_lat, from_lon, to_lat, to_lon, heading = none):
    (lat_m, lon_m) = metres_per_degree((from_lat + to_lat) / 2)
    east  = (to_lon - from_lon) * lon_m
    north = (to_lat - from_lat) * lat_m
    distance_m  = hypot(east, north)                  # sqrt(east² + north²)
    distance_ft = distance_m * FEET_PER_METRE
    bearing_deg = (atan2(east, north) * 180 / pi) mod 360    # note: atan2(east, north)
    compass       = COMPASS_8 [round_half_even(bearing_deg / 45)   mod 8]
    compass_short = COMPASS_16[round_half_even(bearing_deg / 22.5) mod 16]
    if heading is given and finite:
        turn_deg = ((bearing_deg - heading + 180) mod 360) - 180     # in [-180, 180)
        turn     = turn_words(turn_deg)
    else:
        turn_deg = none; turn = none
```

At distance 0, `atan2(0, 0)` is 0, so the bearing is 0 (North).

```
COMPASS_8  = North, North-East, East, South-East, South, South-West, West, North-West
COMPASS_16 = N, NNE, NE, ENE, E, ESE, SE, SSE, S, SSW, SW, WSW, W, WNW, NW, NNW
```

## 3. Turn words

Negative `turn_deg` is to the left, positive to the right.

```
turn_words(turn_deg):
    size = |turn_deg|
    if size < 10:   "ahead"
    if size >= 135: "turn around"
    side = "right" if turn_deg > 0 else "left"
    if size < 30:   "slightly " + side
    if size < 60:   "bear " + side
    otherwise:      "turn " + side
```

## 4. Distance as text

Rounded so the number does not flicker as the rider moves.

```
format_distance(metres, units = "m"):
    if units == "ft":
        ft = metres * FEET_PER_METRE
        if ft < 300:   text(round_half_even(ft / 5) * 5) + " ft"
        if ft < 5280:  text(round_half_even(ft / 10) * 10) + " ft"
        otherwise:     one_decimal(round_half_even(ft / 528) / 10) + " mi"
    else:
        if metres < 100:   text(round_half_even(metres / 5) * 5) + " m"
        if metres < 1000:  text(round_half_even(metres / 10) * 10) + " m"
        otherwise:         one_decimal(round_half_even(metres / 100) / 10) + " km"
```

`text(n)` writes the integer with no decimals and no thousands separator
(`1000 m`); `one_decimal(x)` writes exactly one decimal (`1.0 km`, `12.3 km`).

## 5. The navigation engine

A session guides a rider to one target. It is fed readings as they arrive and
returns a **state** after every call.

### Options (defaults)

| Option | Default | Meaning |
|---|---|---|
| `arrived_m` | 15 | Under this distance: status `arrived` |
| `almost_m` | 50 | Under this distance: status `almost` |
| `moving_mps` | 2.0 | At this speed or faster, the GPS course is the heading |
| `compass_fresh_s` | 3.0 | A compass reading younger than this is used |
| `smoothing_window_s` | 10.0 | GPS readings younger than this are averaged |
| `smoothing_count` | 3 | At most this many readings are averaged |

### Session data

```
target         (lat, lon)
position       (lat, lon) or none
accuracy_m     number or none      # as given by the last GPS reading
heading        (degrees, source) or none
fixes          list of (lat, lon, acc, time), oldest first
anchor         (lat, lon) or none  # for set_position's movement heading
live           boolean             # the position comes from GPS readings
compass        (degrees, time) or none
```

A new session: `target` set, `compass = none`, then `reset()`.

A heading value is **valid** when it is given and finite (not NaN, not ±∞).

### reset()

`position = none; accuracy_m = none; heading = none; fixes = []; anchor = none; live = false`.
The last compass reading is kept.

### set_target(lat, lon)

`target = (lat, lon)`. Nothing else changes.

### add_fix(lat, lon, accuracy, speed, heading, time)

Any of `accuracy`, `speed`, `heading` may be missing. A number is *finite*
when it is a real number, not NaN or ±infinity (a boolean is not a number).

```
if lat or lon is not finite, or lat is outside -90..90, or lon outside -180..180:
    return the state unchanged                   # a bad reading is ignored
acc = max(accuracy if accuracy is finite and > 0 else 20, 1)
if not live, or direction(position, (lat, lon)).distance_m > max(acc, 5):
    fixes = []                                   # real movement: start afresh
append (lat, lon, acc, time) to fixes
keep only fixes with  time - fix.time < smoothing_window_s,  then only the last smoothing_count
sw = slat = slon = 0                             # sum in list order, oldest first
for each fix:  w = 1 / (fix.acc * fix.acc);  sw += w;  slat += fix.lat * w;  slon += fix.lon * w
position   = (slat / sw, slon / sw)
accuracy_m = accuracy if it is finite and >= 0 else none
live = true;  anchor = none

if speed is finite and speed >= moving_mps and heading is valid:
    heading = (heading mod 360, "gps")
else if compass is not none and time - compass.time < compass_fresh_s:
    heading = (compass.degrees, "compass")
else:
    heading = none
```

### add_compass(heading, time)

Heading in degrees from north, already corrected for the screen's rotation.

```
if heading is not valid: do nothing
compass = (heading mod 360, time)
if live and not (heading is set and its source is "gps"):
    heading = (compass.degrees, "compass")
```

So a compass reading never replaces the GPS course, and is not used while the
position comes from `set_position`; it is still stored, and `add_fix` may use
it later while it is fresh.

### set_position(lat, lon, heading)

Places the rider directly (a manual pick, a simulation, a replay). No smoothing.

```
if lat or lon is not finite or out of range (as in add_fix):
    return the state unchanged
if anchor is not none:
    moved = direction(anchor, (lat, lon))
else:
    heading = none;  moved = none;  anchor = (lat, lon)
if the given heading is valid:
    heading = (given heading mod 360, "manual");  anchor = (lat, lon)
else if moved is not none and moved.distance_m >= 2:
    heading = (moved.bearing_deg, "movement");  anchor = (lat, lon)
# otherwise the previous heading stays
position = (lat, lon);  accuracy_m = none;  fixes = [];  live = false
```

### The state

Returned by every call.

| Field | Value |
|---|---|
| `status` | `waiting` if there is no position; else from `d = direction(position, target, heading.degrees)`: `arrived` if `d.distance_m < arrived_m`, `almost` if `< almost_m`, else `navigating` |
| `target_lat`, `target_lon` | The target |
| `lat`, `lon`, `accuracy_m` | Position and accuracy (none while waiting) |
| `heading_deg`, `heading_source` | The heading in use and its source: `gps`, `compass`, `manual`, `movement`; none when there is no heading |
| `distance_m`, `distance_ft`, `bearing_deg`, `compass`, `compass_short`, `turn_deg`, `turn` | From `d` (§2). `turn_deg` and `turn` are none without a heading |
| `arrow_deg` | `turn_deg` when there is a heading, else `bearing_deg` |
| `arrow_mode` | `relative` when there is a heading (0 = straight ahead), else `north-up` (0 = north) |

While waiting, every field except `status`, `target_lat` and `target_lon` is none.

## 6. What the file does not test

Distances, bearings and turns come from `sin`, `cos`, `atan2` and `sqrt`,
whose last digit can differ between platforms. The file therefore contains no
case where such a computed value lies within 1e-6 (metres or degrees) of a
decision threshold, so a correct port always gets the same words and status.
For the same reason, the comparisons on computed values (`> max(acc, 5)` for a
GPS jump, `>= 2` for movement, `< arrived_m`, `< almost_m`) cannot be told
apart from `>=` / `>` by the file. Follow the rules above exactly anyway. Ties
of inputs given directly (times, speeds, compass bearings, turn angles,
distances for `format_distance`) are tested exactly.
