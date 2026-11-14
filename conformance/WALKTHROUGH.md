# Walkthrough rider rules (walkthrough version 1)

The exact rule a rider's app follows to show a walkthrough: which photo to
show, how far it is to the door, and the next turn, from a stream of GPS
readings (or the rider swiping through the photos). It needs no grid
database: only the walkthrough's JSON, made by the backend with
`pakgrid walkthrough build` or `grid.walkthrough(...)`.

These rules are what `walkthrough.json` tests. The Python library
(`pakgrid/walkthrough.py`, `WalkthroughGuide`) and the JavaScript copy
(`viewer/pakgrid.js`, `PakGrid.WalkthroughGuide`) both follow them, and
[`runners/walkthrough.js`](runners/walkthrough.js) is a complete runner.

The conventions of [NAVIGATION.md](NAVIGATION.md) apply: IEEE 754 doubles,
operations in the order written, `metres_per_degree` (§1) and
`format_distance` (§4) exactly as there.

## 1. What the guide reads from the walkthrough

| Field | Used for |
|---|---|
| `path`: `[[lat, lon, t_s, along_m], ...]` | The line the rider follows; `along_m` is each point's distance from the start, as stored (do not recompute it) |
| `length_m` | The distance from the start to the door |
| `frames`: `[{along_m, ...}, ...]` | The photos, in order along the path |
| `turns`: `[{along_m, turn}, ...]` | The turns, in order; `turn` is `turn left`, `bear right`, `turn around`, … |

## 2. Constants

```
WEAK_GPS_M         = 30    # a less accurate reading does not move the rider
OFF_PATH_M         = 30    # further than this from the path is off it
BACK_M             = 12    # a reading this far behind the progress moves it back
ARRIVED_M          = 6
TURN_NOW_M         = 4
ASSUMED_ACCURACY_M = 20    # for a reading without a usable accuracy
```

## 3. The flat frame

All distances are in one flat frame: metres east and north of the path's
first point, at that point's scale.

```
(ref_lat, ref_lon) = path[0].lat, path[0].lon
(lat_m, lon_m) = metres_per_degree(ref_lat)
xy(lat, lon) = ((lon - ref_lon) * lon_m, (lat - ref_lat) * lat_m)      # (x east, y north)
P[k] = xy(path[k].lat, path[k].lon)     # computed once
A[k] = path[k].along_m
```

## 4. Projecting a reading onto the path

```
project(x, y):
    best = none
    for k in 0 .. len(P) - 2:
        (ax, ay) = P[k];  dx = P[k+1].x - ax;  dy = P[k+1].y - ay
        seg2 = dx * dx + dy * dy
        t = 0 if seg2 == 0 else clamp(((x - ax) * dx + (y - ay) * dy) / seg2, 0, 1)
        d = hypot(x - (ax + t * dx), y - (ay + t * dy))
        if best is none or d < best.d:            # strictly less: the first segment wins a tie
            best = (d, A[k] + t * (A[k+1] - A[k]))
    return best                                    # (distance from the path, along)
```

## 5. The guide

State: `progress` (metres along the path), starting at 0.

```
update(lat, lon, accuracy):
    if lat or lon is not finite, or lat not in [-90, 90], or lon not in [-180, 180]:
        return state(off = none, gps = "ignored")
    acc = accuracy if accuracy is a finite number > 0 else ASSUMED_ACCURACY_M
    (off, raw) = project(xy(lat, lon))
    if acc > WEAK_GPS_M:   return state(off, "weak")
    if off > OFF_PATH_M:   return state(off, "off_path")
    if raw >= progress or progress - raw > BACK_M:
        progress = raw                              # small steps back are GPS wobble
    return state(off, "ok")

show(i):                                            # a swipe, or tapping a photo
    i = clamp(trunc(i), 0, len(frames) - 1)
    progress = frames[i].along_m
    return state(none, "manual")
next():     return show(current_state.frame + 1)
previous(): return show(current_state.frame - 1)
```

A new guide's state is `state(none, "start")`.

## 6. What the screen shows

```
state(off, gps):
    frame = 0
    for i in 0 .. len(frames) - 1:                  # the nearest photo; a tie goes to the later one
        if |frames[i].along_m - progress| <= |frames[frame].along_m - progress|: frame = i
    to_go = max(length_m - progress, 0)
    next  = the first turn with turn.along_m >= progress, or none
    in_m  = next.along_m - progress   (none without a next turn)
    arrived = to_go <= ARRIVED_M
    text =
        gps == "off_path":         "Go back to the path: it is " + format_distance(off) + " away"
        arrived:                   "You have arrived"
        next and in_m <= TURN_NOW_M:  capitalise(next.turn) + " now"          # "Turn left now"
        next:                      "In " + format_distance(in_m) + ", " + next.turn
        otherwise:                 "The door is " + format_distance(to_go) + " ahead"
    return {frame, progress_m: progress, to_go_m: to_go, off_path_m: off,
            next_turn: next.turn or none, next_turn_in_m: in_m, arrived, gps, text}
```

`capitalise` makes only the first letter upper case. Apps may show their own
words (in Urdu, say) from `next_turn`, `next_turn_in_m` and `to_go_m`; the
`text` is what the file tests.

## 7. The file

```json
{"format": "pakgrid-conformance", "suite": "walkthrough", "format_version": 1,
 "tolerance": {"metres": 1e-06},
 "field_tolerance": {"progress_m": "metres", "to_go_m": "metres", "off_path_m": "metres", "next_turn_in_m": "metres"},
 "sessions": [{"name": "L-shape", "walkthrough": {...}, "start": {...},
               "steps": [{"call": "update", "args": {"lat": 31.52, "lon": 74.35, "accuracy_m": 8.0},
                          "expect": {"frame": 4, "progress_m": 20.6, ..., "text": "In 20 m, turn right"}}, ...]}]}
```

- `call` is `update` (args `lat`, `lon`, `accuracy_m`), `show` (`index`),
  `next` or `previous`. `"NaN"` stands for NaN (JSON has none); an
  `accuracy_m` of `null` means none was given.
- Compare numbers within the field's tolerance, everything else exactly.
- No case lies within 10⁻⁶ m of a decision (an accuracy, distance or progress
  threshold, a tie between photos or path segments, a step of
  `format_distance`), so last-digit differences between platforms cannot
  change a photo or a word.
