/**
 * Render Mermaid diagram to SVG using mermaid v9 + jsdom.
 * No browser / no internet required.
 * Post-processes viewBox to ensure full diagram is visible.
 */
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { pretendToBeVisual: true });

// All SVGElements return a reasonable fixed size for layout purposes.
// Mermaid uses getBBox() for Dagre layout; fixed 120x36 gives decent node spacing.
dom.window.SVGElement.prototype.getBBox = function () {
  return { x: 0, y: 0, width: 120, height: 36 };
};
dom.window.SVGElement.prototype.getComputedTextLength = function () {
  return Math.min((this.textContent || '').length * 7, 140);
};

global.window    = dom.window;
global.document  = dom.window.document;
global.navigator = dom.window.navigator;
global.SVGElement = dom.window.SVGElement;
global.HTMLElement = dom.window.HTMLElement;
global.Element   = dom.window.Element;
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);
global.cancelAnimationFrame  = clearTimeout;
global.MutationObserver = dom.window.MutationObserver;

const mermaid = require('mermaid');

/**
 * Fix SVG viewBox by scanning all translate() transforms and rects to find
 * the true bounding box of the diagram content.
 */
function fixViewBox(svg) {
  // Extract all translate(x, y) values
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const transRe = /translate\(([-\d.]+),\s*([-\d.]+)\)/g;
  let m;
  while ((m = transRe.exec(svg)) !== null) {
    const x = parseFloat(m[1]), y = parseFloat(m[2]);
    minX = Math.min(minX, x - 80);
    minY = Math.min(minY, y - 30);
    maxX = Math.max(maxX, x + 200);
    maxY = Math.max(maxY, y + 60);
  }
  if (!isFinite(minX)) return svg;  // nothing to fix

  const pad = 20;
  const vbX = minX - pad, vbY = minY - pad;
  const vbW = maxX - minX + pad * 2;
  const vbH = maxY - minY + pad * 2;

  // Cap very large dimensions (> 4000px) at reasonable max
  const finalW = Math.min(vbW, 3000);
  const finalH = Math.min(vbH, 2000);
  const vb = `${vbX.toFixed(0)} ${vbY.toFixed(0)} ${finalW.toFixed(0)} ${finalH.toFixed(0)}`;

  return svg
    .replace(/viewBox="[^"]*"/, `viewBox="${vb}"`)
    .replace(/width="[^"]*"/, `width="${finalW}"`)
    .replace(/height="[^"]*"/, `height="${finalH}"`);
}

async function render(code) {
  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose',
    flowchart: { htmlLabels: false, curve: 'linear', nodeSpacing: 60, rankSpacing: 80 },
  });

  const id = 'g' + Date.now();
  const el = document.createElement('div');
  el.id = id;
  document.body.appendChild(el);

  const result = await mermaid.render(id, code);
  let svgStr = typeof result === 'string' ? result : result?.svg;

  if (svgStr && svgStr.includes('<svg')) {
    svgStr = fixViewBox(svgStr);
    process.stdout.write(svgStr);
    process.exit(0);
  }
  process.stderr.write('No SVG\n');
  process.exit(1);
}

const code = process.argv.slice(2).join('\n') || process.env.MERMAID_CODE;
if (!code) { process.stderr.write('No code\n'); process.exit(1); }
render(code);
