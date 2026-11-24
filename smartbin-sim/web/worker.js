// Runs the Smart Bin simulation (real Python core) in the browser.
// Pyodide = CPython compiled to WebAssembly. The Python sources come from
// py.zip, built by tools/build_web.py. The page talks to us with
// {id, op, args} messages; see backend() in sim/viewer.html.
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.27.2/full/';
importScripts(PYODIDE + 'pyodide.js');

let app = null;
let booting = null;

function progress(text) { self.postMessage({progress: text}); }

async function boot(config) {
  progress('Python wird geladen (einmalig ca. 10 MB) …');
  const py = await loadPyodide({indexURL: PYODIDE});
  progress('Pakete werden geladen …');
  await py.loadPackage(['pyyaml', 'sqlite3']);
  progress('Smart-Bin-Software wird geladen …');
  const zip = await (await fetch('py.zip', {cache: 'no-cache'})).arrayBuffer();
  py.unpackArchive(zip, 'zip', {extractDir: '/app'});
  py.runPython("import os, sys\nsys.path.insert(0, '/app')\nos.chdir('/app')");
  progress('Station startet …');
  const WebApp = py.pyimport('sim.web').WebApp;
  app = WebApp(config || '');
  let last = performance.now();
  setInterval(() => {
    const now = performance.now();
    try { app.advance((now - last) / 1000); } catch (e) { console.error(e); }
    last = now;
  }, 20);
  return JSON.parse(app.info_json());
}

self.onmessage = async (e) => {
  const {id, op, args} = e.data;
  try {
    let result;
    if (op === 'init') {
      booting = booting || boot(args && args.config);
      result = await booting;
    } else {
      if (!app) await booting;
      result = JSON.parse(app.request(op, JSON.stringify(args || {})));
    }
    self.postMessage({id, result});
  } catch (err) {
    self.postMessage({id, error: String(err && err.message || err)});
  }
};
