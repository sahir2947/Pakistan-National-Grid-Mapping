// Navigate mode for the map viewer: guides a rider from their position to a pinned point.
//
// This file is only the screen. All the guidance (GPS smoothing, choosing a heading, distance,
// direction, turn, arrival) comes from PakGrid.NavigationSession, the same engine as Python's
// pakgrid.NavigationSession; apps build their own screens on that engine in the same way.
// The rider's position comes from live GPS, or from a marker dragged on the map ("Simulate")
// for testing without moving.
(function (root) {
  "use strict";

  var HEADING_SOURCES = {
    gps: "direction of travel (GPS)", compass: "phone compass",
    movement: "direction dragged (simulation)", manual: "heading given"
  };

  function Navigator(opts) {
    this.map = opts.map;
    this.grid = opts.grid;
    this.panel = opts.panel;
    this.onExit = opts.onExit;
    this.flash = opts.flash;
    this.layer = L.layerGroup().addTo(this.map);
    this.units = "m";
    this.target = null;
    this.session = null;         // PakGrid.NavigationSession
    this.state = null;           // its latest state
    this.mode = null;            // "live" | "sim" | null
    this._watch = null;
    this._onOrientation = this._orientation.bind(this);
    this.map.on("zoomend", this._drawArrow, this);
  }

  // -- Opening and closing -------------------------------------------------------

  Navigator.prototype.open = function (target) {
    this.close(true);
    this.target = target;
    this.session = new PakGrid.NavigationSession(target.lat, target.lon);
    this.state = this.session.state();
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
    this.target = this.session = this.state = null;
    this._line = this._arrow = this._riderMarker = this._accuracy = null;
    if (!keepPanel && this.onExit) this.onExit();
  };

  Navigator.prototype.stop = function () {
    if (this._watch != null) navigator.geolocation.clearWatch(this._watch);
    this._watch = null;
    window.removeEventListener("deviceorientationabsolute", this._onOrientation);
    window.removeEventListener("deviceorientation", this._onOrientation);
    this.mode = null;
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
    var c = pos.coords;
    this.state = this.session.addFix(c.latitude, c.longitude, { accuracy: c.accuracy, speed: c.speed, heading: c.heading });
    this._update();
    if (!this._fitted) { this._fitted = true; this.recenter(); }
  };

  Navigator.prototype._orientation = function (e) {
    var deg = null;
    if (typeof e.webkitCompassHeading === "number") deg = e.webkitCompassHeading;   // iPhone
    else if (e.absolute && typeof e.alpha === "number") deg = (360 - e.alpha) % 360;  // Android
    if (deg == null) return;
    var orient = (screen.orientation && screen.orientation.angle) || window.orientation || 0;
    if (this.mode !== "live" || !this.session) return;
    var before = this.state;
    this.state = this.session.addCompass(deg + orient);
    if (this.state.heading_deg !== before.heading_deg) this._update();
  };

  // -- Simulation ------------------------------------------------------------------

  Navigator.prototype.startSim = function () {
    var self = this;
    this.stop();
    this._clearRider();
    this.mode = "sim";
    // Start the rider about 400 m south-west of the target, inside the current view if possible.
    var m = PakGrid.metresPerDegree(this.target.lat);
    this.state = this.session.setPosition(this.target.lat - 280 / m.lat, this.target.lon - 280 / m.lon);
    var marker = L.marker([this.state.lat, this.state.lon], { draggable: true, zIndexOffset: 900,
      icon: this._riderIcon(), autoPan: true }).addTo(this.layer);
    this._riderMarker = marker;
    // In simulation the engine takes the heading from the direction the marker is dragged.
    function moved() {
      var p = marker.getLatLng();
      self.state = self.session.setPosition(p.lat, p.lng);
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
    if (this.session) this.state = this.session.reset();
  };

  Navigator.prototype._update = function () {
    var s = this.state;
    if (!s || s.status === "waiting") { this.render(); return; }
    var r = [s.lat, s.lon], t = [this.target.lat, this.target.lon];
    if (this.mode === "live") {
      if (!this._riderMarker) {
        this._riderMarker = L.marker(r, { interactive: false, zIndexOffset: 900, icon: this._riderIcon() }).addTo(this.layer);
      } else {
        this._riderMarker.setLatLng(r);
      }
      if (s.accuracy_m) {
        if (!this._accuracy) {
          this._accuracy = L.circle(r, { radius: s.accuracy_m, color: "#2563eb", weight: 1,
            fillOpacity: 0.08, interactive: false }).addTo(this.layer);
        } else {
          this._accuracy.setLatLng(r).setRadius(s.accuracy_m);
        }
      }
    }
    if (!this._line) {
      this._line = L.polyline([r, t], { color: "#dc2626", weight: 3, dashArray: "8 6", interactive: false })
        .addTo(this.layer);
    } else {
      this._line.setLatLngs([r, t]);
    }
    this._drawArrow();
    this.render();
  };

  // Arrowhead on the map line, halfway along, pointing from the rider to the target.
  Navigator.prototype._drawArrow = function () {
    if (this._arrow) { this.layer.removeLayer(this._arrow); this._arrow = null; }
    var s = this.state;
    if (!s || s.status === "waiting" || s.status === "arrived") return;
    var a = this.map.latLngToContainerPoint([s.lat, s.lon]);
    var b = this.map.latLngToContainerPoint([this.target.lat, this.target.lon]);
    var size = Math.min(24, Math.round(a.distanceTo(b) * 0.35));
    if (size < 8) return;
    this._arrow = L.marker([(s.lat + this.target.lat) / 2, (s.lon + this.target.lon) / 2], {
      interactive: false, icon: L.divIcon({ className: "arrow-head", iconSize: [size, size], iconAnchor: [size / 2, size / 2],
        html: arrowSvg(s.bearing_deg, "#dc2626") }) }).addTo(this.layer);
  };

  Navigator.prototype.recenter = function () {
    if (!this.target) return;
    var pts = [[this.target.lat, this.target.lon]];
    if (this.state && this.state.lat != null) pts.push([this.state.lat, this.state.lon]);
    if (pts.length === 1) this.map.setView(pts[0], 17);
    else this.map.fitBounds(pts, L.extend({ maxZoom: 18 }, this.padding ? this.padding() : {}));
  };

  function arrowSvg(rotateDeg, colour) {
    return "<svg viewBox='0 0 22 22' style='transform:rotate(" + rotateDeg.toFixed(1) + "deg)'>" +
           "<path d='M11 2 L19 19 L11 14 L3 19 Z' fill='" + colour + "' stroke='#fff' stroke-width='1.5'/></svg>";
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }

  // -- Panel ---------------------------------------------------------------------

  Navigator.prototype.render = function () {
    if (!this.target) return;
    var self = this, d = this.state, t = this.target, html = "";
    html += "<div class='nav-head'><div><h2>Navigate to</h2><div class='code'>" + esc(t.label) + "</div>" +
            "<div class='sub'>" + t.lat.toFixed(6) + ", " + t.lon.toFixed(6) + "</div></div>" +
            "<button type='button' id='nav-exit'>Exit</button></div>";

    if (!d || d.status === "waiting") {
      html += "<p class='nav-hint'>" + (this.mode === "live" ? "Waiting for your GPS position…" :
              "Choose <b>Live GPS</b> to navigate from where you are, or <b>Simulate</b> to drag a rider " +
              "marker around the map.") + "</p>";
    } else if (d.status === "arrived") {
      html += "<div class='nav-main arrived'><div class='nav-big'>✓</div><div><div class='nav-dist'>You've arrived</div>" +
              "<div class='sub'>Within " + this.session.arrived_m + " m of the pin</div></div></div>";
    } else {
      // With a heading, the arrow turns with the rider: up means straight ahead. Without one it is north-up.
      var rel = d.arrow_mode === "relative";
      html += "<div class='nav-main'><div class='nav-big'>" + arrowSvg(d.arrow_deg, "#dc2626") + "</div><div>" +
              "<div class='nav-dist'>" + PakGrid.formatDistance(d.distance_m, this.units) + "</div>" +
              "<div class='nav-dir'>" + d.compass_short + " · " + Math.round(d.bearing_deg) + "° (" + d.compass + ")</div>" +
              (rel ? "<div class='nav-turn'>" + d.turn + "</div>" : "") +
              (d.status === "almost" ? "<div class='nav-turn'>Almost there</div>" : "") + "</div></div>" +
              "<div class='sub'>" + (rel ? "Arrow: up = the way you're heading (" + esc(HEADING_SOURCES[d.heading_source]) + ")."
                                          : "Arrow: north-up (your heading isn't known yet; start moving, or allow the compass).") +
              " Bearings are clockwise from north. Straight-line distance; the road may be longer.</div>";
    }

    if (d && d.status !== "waiting") {
      var addr = this.grid.addressFromPoint(d.lat, d.lon);
      var rows = [["You", d.lat.toFixed(6) + ", " + d.lon.toFixed(6)]];
      if (addr) rows.push(["Your grid address", esc(addr.text)]);
      if (d.accuracy_m) rows.push(["GPS accuracy", "±" + Math.round(d.accuracy_m) + " m"]);
      rows.push(["Exact distance", d.distance_m.toFixed(1) + " m (" + Math.round(d.distance_ft) + " ft)"]);
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

  var api = { Navigator: Navigator };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PakNav = api;
})(this);
