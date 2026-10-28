// Navigate mode for the map viewer: guides a rider from their position to a pinned point.
//
// Every update works out the straight-line distance and compass bearing from the rider to the
// target (PakGrid.direction, the same maths as pakgrid.navigate.direction) and, when the rider's
// heading is known, how far to turn. The rider's position comes from live GPS, or from a marker
// dragged on the map ("Simulate") for testing without moving.
//
// Heading, in order of preference:
//   1. GPS direction of travel, while moving faster than walking pace (a phone compass is
//      unreliable on a motorbike);
//   2. the phone's compass, when stopped;
//   3. none: the arrow is then drawn north-up.
(function (root) {
  "use strict";

  var ARRIVED_M = 15, ALMOST_M = 50, MOVING_MPS = 2, COMPASS_FRESH_MS = 3000;

  function Navigator(opts) {
    this.map = opts.map;
    this.grid = opts.grid;
    this.panel = opts.panel;
    this.onExit = opts.onExit;
    this.flash = opts.flash;
    this.layer = L.layerGroup().addTo(this.map);
    this.units = "m";
    this.target = null;
    this.rider = null;           // {lat, lon, accuracy}
    this.heading = null;         // {deg, source}
    this.mode = null;            // "live" | "sim" | null
    this.fixes = [];
    this.compass = null;         // {deg, at}
    this._watch = null;
    this._onOrientation = this._orientation.bind(this);
    this.map.on("zoomend", this._drawArrow, this);
  }

  // -- Opening and closing -------------------------------------------------------

  Navigator.prototype.open = function (target) {
    this.close(true);
    this.target = target;
    L.marker([target.lat, target.lon], { interactive: false, zIndexOffset: 1000, icon: L.divIcon({
      className: "nav-target", iconSize: [30, 30], iconAnchor: [15, 28],
      html: "<svg viewBox='0 0 30 30'><path d='M15 29 C15 29 4 17 4 11 A11 11 0 0 1 26 11 C26 17 15 29 15 29 Z' " +
            "fill='#dc2626' stroke='#fff' stroke-width='2'/><circle cx='15' cy='11' r='4' fill='#fff'/></svg>" })
    }).addTo(this.layer);
    this.render();
  };

  Navigator.prototype.close = function (keepPanel) {
    this.stop();
    this.layer.clearLayers();
    this.target = this.rider = this.heading = null;
    this._line = this._arrow = this._riderMarker = this._accuracy = null;
    if (!keepPanel && this.onExit) this.onExit();
  };

  Navigator.prototype.stop = function () {
    if (this._watch != null) navigator.geolocation.clearWatch(this._watch);
    this._watch = null;
    window.removeEventListener("deviceorientationabsolute", this._onOrientation);
    window.removeEventListener("deviceorientation", this._onOrientation);
    this.mode = null;
    this.fixes = [];
  };

  // -- Live GPS ------------------------------------------------------------------

  Navigator.prototype.startLive = function () {
    var self = this;
    if (!navigator.geolocation) { this.flash("This browser cannot share its location"); return; }
    this.stop();
    this._clearRider();
    this.mode = "live";
    this._fitted = false;
    // The compass needs permission on iPhones, and the request must come from this tap.
    if (window.DeviceOrientationEvent && typeof DeviceOrientationEvent.requestPermission === "function") {
      DeviceOrientationEvent.requestPermission().then(function (state) {
        if (state === "granted") window.addEventListener("deviceorientation", self._onOrientation);
      }).catch(function () {});
    } else {
      window.addEventListener("deviceorientationabsolute", this._onOrientation);
      window.addEventListener("deviceorientation", this._onOrientation);
    }
    this._watch = navigator.geolocation.watchPosition(function (pos) { self._fix(pos); },
      function (err) { self.flash("Location unavailable: " + err.message); },
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 20000 });
    this.render();
  };

  Navigator.prototype._fix = function (pos) {
    var c = pos.coords, now = Date.now(), acc = Math.max(c.accuracy || 20, 1);
    // Steady the arrow by averaging the last few readings (weighted by accuracy), but only while
    // they agree: a reading further from the current estimate than its own accuracy is real
    // movement, so start afresh from it rather than lag behind the rider.
    if (this.rider) {
      var jump = PakGrid.direction(this.rider.lat, this.rider.lon, c.latitude, c.longitude, null).distance_m;
      if (jump > Math.max(acc, 5)) this.fixes = [];
    }
    this.fixes.push({ lat: c.latitude, lon: c.longitude, acc: acc, at: now });
    this.fixes = this.fixes.filter(function (f) { return now - f.at < 10000; }).slice(-3);
    var sw = 0, slat = 0, slon = 0;
    this.fixes.forEach(function (f) { var w = 1 / (f.acc * f.acc); sw += w; slat += f.lat * w; slon += f.lon * w; });
    this.rider = { lat: slat / sw, lon: slon / sw, accuracy: c.accuracy };

    if (c.speed != null && c.speed >= MOVING_MPS && c.heading != null && isFinite(c.heading)) {
      this.heading = { deg: c.heading, source: "direction of travel (GPS)" };
    } else if (this.compass && now - this.compass.at < COMPASS_FRESH_MS) {
      this.heading = { deg: this.compass.deg, source: "phone compass" };
    } else {
      this.heading = null;
    }
    this._update();
    if (!this._fitted) { this._fitted = true; this.recenter(); }
  };

  Navigator.prototype._orientation = function (e) {
    var deg = null;
    if (typeof e.webkitCompassHeading === "number") deg = e.webkitCompassHeading;   // iPhone
    else if (e.absolute && typeof e.alpha === "number") deg = (360 - e.alpha) % 360;  // Android
    if (deg == null) return;
    var orient = (screen.orientation && screen.orientation.angle) || window.orientation || 0;
    this.compass = { deg: ((deg + orient) % 360 + 360) % 360, at: Date.now() };
    if (this.mode === "live" && this.rider && !(this.heading && this.heading.source.indexOf("GPS") >= 0)) {
      this.heading = { deg: this.compass.deg, source: "phone compass" };
      this._update();
    }
  };

  // -- Simulation ------------------------------------------------------------------

  Navigator.prototype.startSim = function () {
    var self = this;
    this.stop();
    this._clearRider();
    this.mode = "sim";
    // Start the rider about 400 m south-west of the target, inside the current view if possible.
    var m = PakGrid.metresPerDegree(this.target.lat);
    this.rider = { lat: this.target.lat - 280 / m.lat, lon: this.target.lon - 280 / m.lon, accuracy: null };
    this.heading = null;
    var marker = L.marker([this.rider.lat, this.rider.lon], { draggable: true, zIndexOffset: 900,
      icon: this._riderIcon(), autoPan: true }).addTo(this.layer);
    this._riderMarker = marker;
    var last = L.latLng(this.rider.lat, this.rider.lon);
    function moved() {
      var p = marker.getLatLng();
      // In simulation, the heading is the direction the marker was last dragged.
      var d = PakGrid.direction(last.lat, last.lng, p.lat, p.lng, null);
      if (d.distance_m >= 2) { self.heading = { deg: d.bearing_deg, source: "direction dragged (simulation)" }; last = p; }
      self.rider = { lat: p.lat, lon: p.lng, accuracy: null };
      self._update();
    }
    marker.on("drag", moved);
    marker.on("dragend", moved);
    this._update();
    this.recenter();
  };

  // -- Drawing -------------------------------------------------------------------

  Navigator.prototype._riderIcon = function () {
    return L.divIcon({ className: "nav-rider", iconSize: [26, 26], iconAnchor: [13, 13],
      html: "<svg viewBox='0 0 26 26'><circle cx='13' cy='13' r='9' fill='#2563eb' stroke='#fff' stroke-width='3'/>" +
            "<circle cx='13' cy='13' r='3' fill='#fff'/></svg>" });
  };

  Navigator.prototype._clearRider = function () {
    var self = this;
    ["_line", "_arrow", "_riderMarker", "_accuracy"].forEach(function (k) {
      if (self[k]) self.layer.removeLayer(self[k]);
      self[k] = null;
    });
    this.rider = this.heading = null;
  };

  Navigator.prototype._update = function () {
    if (!this.rider || !this.target) { this.render(); return; }
    var r = [this.rider.lat, this.rider.lon], t = [this.target.lat, this.target.lon];
    if (this.mode === "live") {
      if (!this._riderMarker) {
        this._riderMarker = L.marker(r, { interactive: false, zIndexOffset: 900, icon: this._riderIcon() }).addTo(this.layer);
      } else {
        this._riderMarker.setLatLng(r);
      }
      if (this.rider.accuracy) {
        if (!this._accuracy) {
          this._accuracy = L.circle(r, { radius: this.rider.accuracy, color: "#2563eb", weight: 1,
            fillOpacity: 0.08, interactive: false }).addTo(this.layer);
        } else {
          this._accuracy.setLatLng(r).setRadius(this.rider.accuracy);
        }
      }
    }
    if (!this._line) {
      this._line = L.polyline([r, t], { color: "#dc2626", weight: 3, dashArray: "8 6", interactive: false })
        .addTo(this.layer);
    } else {
      this._line.setLatLngs([r, t]);
    }
    this.dir = PakGrid.direction(r[0], r[1], t[0], t[1], this.heading ? this.heading.deg : null);
    this._drawArrow();
    this.render();
  };

  // Arrowhead on the map line, halfway along, pointing from the rider to the target.
  Navigator.prototype._drawArrow = function () {
    if (this._arrow) { this.layer.removeLayer(this._arrow); this._arrow = null; }
    if (!this.rider || !this.target || !this.dir || this.dir.distance_m < ARRIVED_M) return;
    var a = this.map.latLngToContainerPoint([this.rider.lat, this.rider.lon]);
    var b = this.map.latLngToContainerPoint([this.target.lat, this.target.lon]);
    var size = Math.min(24, Math.round(a.distanceTo(b) * 0.35));
    if (size < 8) return;
    this._arrow = L.marker([(this.rider.lat + this.target.lat) / 2, (this.rider.lon + this.target.lon) / 2], {
      interactive: false, icon: L.divIcon({ className: "arrow-head", iconSize: [size, size], iconAnchor: [size / 2, size / 2],
        html: arrowSvg(this.dir.bearing_deg, "#dc2626") }) }).addTo(this.layer);
  };

  Navigator.prototype.recenter = function () {
    if (!this.target) return;
    var pts = [[this.target.lat, this.target.lon]];
    if (this.rider) pts.push([this.rider.lat, this.rider.lon]);
    if (pts.length === 1) this.map.setView(pts[0], 17);
    else this.map.fitBounds(pts, L.extend({ maxZoom: 18 }, this.padding ? this.padding() : {}));
  };

  function arrowSvg(rotateDeg, colour) {
    return "<svg viewBox='0 0 22 22' style='transform:rotate(" + rotateDeg.toFixed(1) + "deg)'>" +
           "<path d='M11 2 L19 19 L11 14 L3 19 Z' fill='" + colour + "' stroke='#fff' stroke-width='1.5'/></svg>";
  }

  // Distances rounded so they don't flicker: 5 m steps under 100 m, 10 m steps under 1 km, then km.
  function formatDistance(m, units) {
    if (units === "ft") {
      var ft = m * 3.28084;
      if (ft < 300) return Math.round(ft / 5) * 5 + " ft";
      if (ft < 5280) return Math.round(ft / 10) * 10 + " ft";
      return (ft / 5280).toFixed(1) + " mi";
    }
    if (m < 100) return Math.round(m / 5) * 5 + " m";
    if (m < 1000) return Math.round(m / 10) * 10 + " m";
    return (m / 1000).toFixed(1) + " km";
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }

  // -- Panel ---------------------------------------------------------------------

  Navigator.prototype.render = function () {
    if (!this.target) return;
    var self = this, d = this.dir, t = this.target, html = "";
    html += "<div class='nav-head'><div><h2>Navigate to</h2><div class='code'>" + esc(t.label) + "</div>" +
            "<div class='sub'>" + t.lat.toFixed(6) + ", " + t.lon.toFixed(6) + "</div></div>" +
            "<button type='button' id='nav-exit'>Exit</button></div>";

    if (!this.rider || !d) {
      html += "<p class='nav-hint'>" + (this.mode === "live" ? "Waiting for your GPS position…" :
              "Choose <b>Live GPS</b> to navigate from where you are, or <b>Simulate</b> to drag a rider " +
              "marker around the map.") + "</p>";
    } else if (d.distance_m < ARRIVED_M) {
      html += "<div class='nav-main arrived'><div class='nav-big'>✓</div><div><div class='nav-dist'>You've arrived</div>" +
              "<div class='sub'>Within " + ARRIVED_M + " m of the pin</div></div></div>";
    } else {
      // With a heading, the arrow turns with the rider: up means straight ahead. Without one it is north-up.
      var rel = this.heading && d.turn_deg != null;
      var rot = rel ? d.turn_deg : d.bearing_deg;
      html += "<div class='nav-main'><div class='nav-big'>" + arrowSvg(rot, "#dc2626") + "</div><div>" +
              "<div class='nav-dist'>" + formatDistance(d.distance_m, this.units) + "</div>" +
              "<div class='nav-dir'>" + d.compass_short + " · " + Math.round(d.bearing_deg) + "° (" + d.compass + ")</div>" +
              (rel ? "<div class='nav-turn'>" + d.turn + "</div>" : "") +
              (d.distance_m < ALMOST_M ? "<div class='nav-turn'>Almost there</div>" : "") + "</div></div>" +
              "<div class='sub'>" + (rel ? "Arrow: up = the way you're heading (" + esc(this.heading.source) + ")."
                                          : "Arrow: north-up (your heading isn't known yet; start moving, or allow the compass).") +
              " Bearings are clockwise from north. Straight-line distance; the road may be longer.</div>";
    }

    if (this.rider) {
      var here = this.grid.locate(this.rider.lat, this.rider.lon);
      var rows = [["You", this.rider.lat.toFixed(6) + ", " + this.rider.lon.toFixed(6)]];
      if (here) rows.push(["Your block", PakGrid.subCellCode(here.q, here.row, here.col, here.subRow * 5 + here.subCol)]);
      if (this.rider.accuracy) rows.push(["GPS accuracy", "±" + Math.round(this.rider.accuracy) + " m"]);
      if (d) rows.push(["Exact distance", d.distance_m.toFixed(1) + " m (" + Math.round(d.distance_ft) + " ft)"]);
      html += "<table>" + rows.map(function (r) {
        return "<tr><td>" + r[0] + "</td><td class='num'>" + r[1] + "</td></tr>"; }).join("") + "</table>";
    }

    html += "<div class='nav-buttons'>" +
            "<button type='button' id='nav-live'" + (this.mode === "live" ? " class='on'" : "") + ">Live GPS</button>" +
            "<button type='button' id='nav-sim'" + (this.mode === "sim" ? " class='on'" : "") + ">Simulate</button>" +
            "<button type='button' id='nav-centre'>Recentre</button>" +
            "<button type='button' id='nav-units'>" + (this.units === "m" ? "Show feet" : "Show metres") + "</button></div>";
    this.panel.innerHTML = html;
    document.getElementById("nav-exit").onclick = function () { self.close(); };
    document.getElementById("nav-live").onclick = function () { self.startLive(); };
    document.getElementById("nav-sim").onclick = function () { self.startSim(); };
    document.getElementById("nav-centre").onclick = function () { self.recenter(); };
    document.getElementById("nav-units").onclick = function () { self.units = self.units === "m" ? "ft" : "m"; self.render(); };
  };

  var api = { Navigator: Navigator, formatDistance: formatDistance };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PakNav = api;
})(this);
