// Checks the map viewer in a real browser (Chromium through Playwright), offline:
//   1. the map and grid appear while places.js is still on its way (held back 4 s);
//   2. a search made while places are loading waits, then shows the place (Lahore);
//   3. a link to a place (#Taxila) opens it once places arrive;
//   4. with the CPU slowed 6x (about a low-end phone): when the map is painted, and when
//      places.js has arrived;
//   5. with tiles.js set to no background map: the grid still draws, and the data sources are
//      still credited.
// Every run fails if the page asks any host but its own for anything other than map tiles (so
// Leaflet must come from viewer/vendor/), and map tiles are not fetched.
//
//   node scripts/check_viewer_in_browser.js viewer index.html
//
// Needs Playwright with Chromium (npm install playwright && npx playwright install chromium).
const { chromium } = require("playwright");
const http = require("http"), fs = require("fs"), path = require("path");
const [viewer, page_name] = process.argv.slice(2);
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".html": "text/html" };
let placesDelay = 0, tilesFile = null;
const server = http.createServer((req, res) => {
  const name = decodeURIComponent(req.url.split("?")[0].split("#")[0]).replace(/^\//, "") || page_name;
  const file = path.join(viewer, name);
  if (!fs.existsSync(file)) { res.writeHead(404); return res.end(); }
  const send = () => { res.writeHead(200, { "Content-Type": TYPES[path.extname(name)] || "application/octet-stream" });
                       if (name === "tiles.js" && tilesFile !== null) return res.end(tilesFile);
                       fs.createReadStream(file).pipe(res); };
  if (name === "places.js" && placesDelay) setTimeout(send, placesDelay); else send();
}).listen(0);
const base = `http://127.0.0.1:${server.address().port}/`;

async function open(browser, { delay = 0, cpu = 1, hash = "", tiles = null } = {}) {
  placesDelay = delay; tilesFile = tiles;
  const ctx = await browser.newContext({ viewport: { width: 412, height: 860 } });
  const page = await ctx.newPage();
  const tileHosts = ["tile.openstreetmap.org"];
  const foreign = [];
  await page.route((url) => url.host !== new URL(base).host, (r) => {
    const host = new URL(r.request().url()).host;
    if (!tileHosts.includes(host)) foreign.push(r.request().url());
    r.abort();
  });
  // when the first frame showing the grid is painted, measured inside the page
  await page.addInitScript(() => {
    new MutationObserver(function (_, obs) {
      if (document.querySelector(".quad-label")) {
        obs.disconnect();
        requestAnimationFrame(() => requestAnimationFrame(() => { window.__mapPainted = performance.now(); }));
      }
    }).observe(document, { childList: true, subtree: true });
  });
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: cpu });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (/integrity/i.test(m.text())) errors.push(m.text()); });
  const t0 = Date.now();
  await page.goto(base + page_name + hash, { waitUntil: "commit" });
  await page.waitForSelector(".quad-label", { timeout: 120000 });             // the grid is drawn
  const mapAt = Date.now() - t0;
  return { ctx, page, t0, mapAt, errors, foreign };
}

(async () => {
  const browser = await chromium.launch();
  const out = {};
  // 1-2: map before places; a tap while loading waits and then shows the place
  {
    const { ctx, page, mapAt, errors, foreign } = await open(browser, { delay: 4000 });
    out.mapWhilePlacesHeld = mapAt;
    out.placesLoadedAtMap = await page.evaluate(() => typeof PAKGRID_PLACES !== "undefined");
    out.summaryWhileLoading = await page.textContent("#panel");
    // tap central Lahore (fly the map there, then click its centre)
    await page.evaluate(() => document.getElementById("q").value = "31.5204, 74.3587");
    await page.evaluate(() => document.querySelector("form").requestSubmit());
    out.statusWhileLoading = await page.textContent("#status");
    await page.waitForFunction(() => /Lahore/.test(document.getElementById("panel").textContent), null, { timeout: 30000 });
    out.panelAfter = (await page.textContent("#panel")).slice(0, 160);
    out.errors1 = errors; out.foreign1 = foreign;
    await ctx.close();
  }
  // 3: a link to a place opens it once places arrive
  {
    const { ctx, page, errors, foreign } = await open(browser, { delay: 2000, hash: "#Taxila" });
    await page.waitForFunction(() => /Taxila/.test(document.getElementById("panel").textContent), null, { timeout: 30000 });
    out.hashPanel = (await page.textContent("#panel")).slice(0, 100);
    out.errors2 = errors; out.foreign2 = foreign;
    await ctx.close();
  }
  // 4: time to a usable map on a CPU slowed 6x (a low-end phone), no network delay
  {
    const { ctx, page, mapAt, errors, foreign } = await open(browser, { cpu: 6 });
    await page.waitForFunction(() => typeof PAKGRID_PLACES !== "undefined" && window.__mapPainted, null, { timeout: 120000 });
    out.cpu6 = await page.evaluate(() => ({ mapPaintedMs: Math.round(window.__mapPainted),
      placesReadyMs: Math.round(performance.getEntriesByName(location.origin + "/places.js")[0].responseEnd) }));
    out.errors3 = errors; out.foreign3 = foreign;
    await ctx.close();
  }
  // 5: no background map (tiles.js with url ""): the grid draws and the data is still credited
  {
    const { ctx, page, errors, foreign } = await open(browser, {
      tiles: 'var PAKGRID_TILES = { url: "", attribution: "", maxZoom: 19 };' });
    out.noTilesImages = await page.evaluate(() => document.querySelectorAll(".leaflet-tile").length);
    out.noTilesCredit = await page.textContent(".leaflet-control-attribution");
    out.errors5 = errors; out.foreign5 = foreign;
    await ctx.close();
  }
  await browser.close(); server.close();
  console.log(JSON.stringify(out, null, 1));
  const problems = [];
  for (const k of Object.keys(out)) if (/^(errors|foreign)/.test(k) && out[k].length) problems.push(`${k}: ${out[k].join("; ")}`);
  if (out.noTilesImages !== 0) problems.push("tiles were drawn with tiles.js url set to none");
  if (!/geoBoundaries/.test(out.noTilesCredit)) problems.push("the data sources are not credited without tiles");
  if (problems.length) { console.error("FAILED:\n" + problems.join("\n")); process.exit(1); }
})().catch((e) => { console.error(e); process.exit(1); });
