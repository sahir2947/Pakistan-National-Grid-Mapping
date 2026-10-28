// Reference runner: checks the JavaScript port in viewer/pakgrid.js against navigation.json.
//
//   node conformance/runners/javascript.js [conformance/navigation.json] [viewer/pakgrid.js]
//
// Prints one line per failed case and a summary; exits with status 1 if anything failed.
// A port to another language does the same four things: read the file, call its own
// functions with each case's inputs, compare each result field (numbers within the file's
// tolerance for that field, everything else exactly), and report.
"use strict";

const fs = require("fs");
const path = require("path");

const here = __dirname;
const suitePath = process.argv[2] || path.join(here, "..", "navigation.json");
const libPath = process.argv[3] || path.join(here, "..", "..", "viewer", "pakgrid.js");
const suite = JSON.parse(fs.readFileSync(suitePath, "utf8"));
const P = require(path.resolve(libPath));

// The file writes NaN as the string "NaN" (JSON has no NaN).
function num(v) { return v === "NaN" ? NaN : v; }

let checked = 0;
const failures = [];

function fail(where, field, want, got) {
  failures.push(where + ": " + field + " expected " + JSON.stringify(want) + ", got " + JSON.stringify(got));
}

// Compare every field of an expected result with the actual one.
function compare(where, expected, actual) {
  checked++;
  for (const field of Object.keys(expected)) {
    const want = expected[field], got = actual[field];
    const tol = suite.field_tolerance[field];
    if (typeof want === "number" && tol) {
      if (typeof got !== "number" || !(Math.abs(got - want) <= suite.tolerance[tol])) fail(where, field, want, got);
    } else if (want !== got) {
      fail(where, field, want, got);
    }
  }
}

suite.compass.forEach((c, i) => {
  const r = P.compass(c.bearing_deg);
  compare("compass[" + i + "]", { compass: c.compass, compass_short: c.compass_short },
          { compass: r.name, compass_short: r.short });
});

suite.turn_words.forEach((c, i) => compare("turn_words[" + i + "]", { turn: c.turn }, { turn: P.turnWords(c.turn_deg) }));

suite.format_distance.forEach((c, i) =>
  compare("format_distance[" + i + "]", { text: c.text }, { text: P.formatDistance(c.metres, c.units) }));

suite.metres_per_degree.forEach((c, i) => {
  const m = P.metresPerDegree(c.lat);
  compare("metres_per_degree[" + i + "]", { lat_m: c.lat_m, lon_m: c.lon_m }, { lat_m: m.lat, lon_m: m.lon });
});

suite.direction.forEach((c, i) =>
  compare("direction[" + i + "]", c.expected, P.direction(c.from[0], c.from[1], c.to[0], c.to[1], c.heading)));

suite.sessions.forEach((s, i) => {
  const nav = new P.NavigationSession(s.target[0], s.target[1], s.options);
  s.steps.forEach((step, k) => {
    const a = step.args;
    let state;
    switch (step.call) {
      case "add_fix":
        state = nav.addFix(a.lat, a.lon, { accuracy: num(a.accuracy), speed: num(a.speed),
                                           heading: num(a.heading), time: a.time });
        break;
      case "add_compass": state = nav.addCompass(num(a.heading), a.time); break;
      case "set_position": state = nav.setPosition(a.lat, a.lon, num(a.heading)); break;
      case "set_target": state = nav.setTarget(a.lat, a.lon); break;
      case "reset": state = nav.reset(); break;
      default: throw new Error("unknown call " + step.call);
    }
    compare("sessions[" + i + "] '" + s.name + "' step " + k + " (" + step.call + ")", step.expect, state);
  });
});

failures.slice(0, 50).forEach((f) => console.log("FAIL " + f));
if (failures.length > 50) console.log("… and " + (failures.length - 50) + " more");
console.log((failures.length ? "FAILED" : "OK") + ": " + checked + " cases, " + failures.length + " mismatches");
process.exit(failures.length ? 1 : 0);
