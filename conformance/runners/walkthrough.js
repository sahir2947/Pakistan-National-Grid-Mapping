// Reference runner: checks the JavaScript port in viewer/pakgrid.js against walkthrough.json.
//
//   node conformance/runners/walkthrough.js [conformance/walkthrough.json] [viewer/pakgrid.js]
//
// For each session: make a guide from its walkthrough, check the starting state, then make
// each call and compare the state with the expected one (numbers within the file's tolerance
// for that field, everything else exactly). Exits with status 1 if anything failed.
"use strict";

const fs = require("fs");
const path = require("path");

const here = __dirname;
const suitePath = process.argv[2] || path.join(here, "..", "walkthrough.json");
const libPath = process.argv[3] || path.join(here, "..", "..", "viewer", "pakgrid.js");
const suite = JSON.parse(fs.readFileSync(suitePath, "utf8"));
const P = require(path.resolve(libPath));

// The file writes NaN as the string "NaN" (JSON has no NaN).
function num(v) { return v === "NaN" ? NaN : v; }

let checked = 0;
const failures = [];

function compare(where, expected, actual) {
  checked++;
  for (const field of Object.keys(expected)) {
    const want = expected[field], got = actual[field];
    const tol = suite.field_tolerance[field];
    if (typeof want === "number" && tol) {
      if (typeof got !== "number" || !(Math.abs(got - want) <= suite.tolerance[tol])) {
        failures.push(where + ": " + field + " expected " + want + ", got " + got);
      }
    } else if (want !== got) {
      failures.push(where + ": " + field + " expected " + JSON.stringify(want) + ", got " + JSON.stringify(got));
    }
  }
}

suite.sessions.forEach((s, i) => {
  const guide = new P.WalkthroughGuide(s.walkthrough);
  compare("sessions[" + i + "] '" + s.name + "' start", s.start, guide.state);
  s.steps.forEach((step, k) => {
    const a = step.args;
    let state;
    switch (step.call) {
      case "update": state = guide.update(num(a.lat), num(a.lon), num(a.accuracy_m)); break;
      case "show": state = guide.show(a.index); break;
      case "next": state = guide.next(); break;
      case "previous": state = guide.previous(); break;
      default: throw new Error("unknown call " + step.call);
    }
    compare("sessions[" + i + "] '" + s.name + "' step " + k + " (" + step.call + ")", step.expect, state);
  });
});

failures.slice(0, 50).forEach((f) => console.log("FAIL " + f));
if (failures.length > 50) console.log("… and " + (failures.length - 50) + " more");
console.log((failures.length ? "FAILED" : "OK") + ": " + checked + " cases, " + failures.length + " mismatches");
process.exit(failures.length ? 1 : 0);
