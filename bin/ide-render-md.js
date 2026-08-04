#!/usr/bin/env node

// Render local Markdown to a self-contained HTML shell for Carbonyl.  KaTeX is
// loaded from the user's local installation, so equations work offline and no
// document content is sent to a web service.

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const modules = path.join(
  process.env.HOME,
  ".local/share/kitty-ide-markdown/node_modules",
);
const { marked } = require(path.join(modules, "marked"));
const markedKatex = require(path.join(modules, "marked-katex-extension"));

const input = process.argv[2];
const output = process.argv[3];
if (!input || !output) {
  console.error("usage: ide-render-md.js INPUT.md OUTPUT.html");
  process.exit(2);
}

const escapeHtml = (value) => value
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

marked.use(markedKatex({
  throwOnError: false,
  // Scientific prose commonly places punctuation immediately after a closing
  // delimiter and may contain several formulas on one line.
  nonStandard: true,
  output: "htmlAndMathml",
}));
marked.setOptions({ gfm: true, breaks: false });

const source = fs.readFileSync(input, "utf8");
const body = marked.parse(source);
const title = escapeHtml(path.basename(input));
const base = pathToFileURL(`${path.dirname(path.resolve(input))}${path.sep}`).href;
const katexCss = pathToFileURL(
  path.join(modules, "katex/dist/katex.min.css"),
).href;

const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <base href="${base}">
  <title>${title}</title>
  <link rel="stylesheet" href="${katexCss}">
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      max-width: 980px;
      margin: 0 auto;
      padding: 2rem 2.4rem 5rem;
      color: #c5c9c5;
      background: #181616;
      font: 17px/1.65 system-ui, sans-serif;
    }
    h1, h2, h3, h4 { color: #e6c384; line-height: 1.25; margin-top: 1.8em; }
    h1 { color: #e46876; border-bottom: 1px solid #54546d; padding-bottom: .35em; }
    h2 { border-bottom: 1px solid #2a2929; padding-bottom: .25em; }
    a { color: #7fb4ca; }
    strong { color: #c8c093; }
    blockquote { border-left: 4px solid #c4746e; margin-left: 0; padding-left: 1em; color: #a6a69c; }
    code { color: #e6c384; background: #2a2929; padding: .12em .3em; border-radius: 4px; }
    pre { overflow: auto; background: #0d0c0c; padding: 1em; border-radius: 7px; }
    pre code { padding: 0; background: transparent; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #54546d; padding: .45em .65em; text-align: left; }
    th { background: #2a2929; }
    .katex-display { overflow-x: auto; overflow-y: hidden; padding: .6em 0; }
    .katex { color: #c8c093; font-size: 1.08em; }
  </style>
</head>
<body>${body}</body>
</html>`;

fs.writeFileSync(output, html);
