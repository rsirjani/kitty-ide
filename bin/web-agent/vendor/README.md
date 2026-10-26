# vendor

`stealth.min.js` — the puppeteer-extra-plugin-stealth evasions (MIT) bundled
as one injectable script by
[extract-stealth-evasions](https://github.com/berstend/puppeteer-extra/tree/master/packages/extract-stealth-evasions).
`stealth.mjs` injects it into every page and patches a few of its defaults
(WebGL vendor, core count) to match a Linux desktop.

Regenerate with `npx extract-stealth-evasions` (in an empty directory) and copy
the resulting `stealth.min.js` here.
