// The background map under the grid. Edit this file to change it; nothing else in the viewer
// needs to change. pakgrid itself never fetches tiles: only the browser showing this page does.
//
//   url          a Leaflet tile URL template ({z}/{x}/{y}, optional {s} and {r}), or "" for no
//                background map at all (the grid, border and places still work: fully offline)
//   attribution  shown in the map's corner; most tile providers require it
//   maxZoom      the deepest zoom the provider serves
//   subdomains   only if the url has {s}
//
// The default, tile.openstreetmap.org, allows light use only (see its tile usage policy:
// https://operations.osmfoundation.org/policies/tiles/). An app showing this viewer to its own
// users must switch to a provider, or its own tile server, that allows that use.
var PAKGRID_TILES = {
  url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19
};
