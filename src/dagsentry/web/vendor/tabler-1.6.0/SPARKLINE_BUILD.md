# Tabler Sparkline subset

`tabler-sparkline.min.js` is compiled from unmodified Tabler Core **1.6.0**
`js/src/sparkline.ts` and its eight Bootstrap-derived internal modules. It is the
actual Tabler component, not a separate chart implementation. Axes, date labels
and accessible day inspection are DagSentry application code.

Source: https://registry.npmjs.org/@tabler/core/-/core-1.6.0.tgz
Source commit: `18023036af414cc3c9820dd516937ee486281c48`.
Verify the artifact SHA-512 recorded in `THIRD_PARTY_NOTICES.md` before extraction.

Compiled with esbuild **0.25.12**. esbuild is development-only; its MIT license
is retained as `LICENSE.esbuild` for generated helper code. Source licenses are
`LICENSE.tabler` and `LICENSE.bootstrap`. No ApexCharts, Popper, full Tabler bundle,
other Bootstrap components, CDN assets or runtime Node dependency are included.
The esbuild metafile confirms the nine source inputs listed below and no external
imports in the output. The only application glue is the global export below.

## Rebuild

In a temporary build directory, extract `package/js/src` from the verified npm
artifact. Install the pinned esbuild into a separate `build-tools` directory:

```sh
npm install --prefix build-tools --no-save --ignore-scripts esbuild@0.25.12
```

Create `entry.js` containing exactly:

```js
import Sparkline from './package/js/src/sparkline.ts';
window.TablerSparkline = Sparkline;
```

Run from that temporary directory:

```sh
build-tools/node_modules/.bin/esbuild entry.js --bundle --minify --format=iife --target=es2022 --legal-comments=inline --metafile=bundle-meta.json '--banner:js=/*! Tabler Sparkline 1.6.0 | MIT | See LICENSE.tabler, LICENSE.bootstrap and LICENSE.esbuild */' --outfile=tabler-sparkline.min.js
```

Output SHA-256:
`2a669f7e37de3f400d6d9aafcf0933466bf498a0103488d38a409ed0f4eac539`

The component uses SVG attributes and textContent for rendered values; its two
innerHTML assignments only clear the container with a constant empty string.
No server strings are interpreted as HTML. No inline scripts or style attributes
are required, and the existing CSP remains unchanged.

## Source SHA-256 inventory

| Source within npm artifact | SHA-256 |
| --- | --- |
| `js/src/bootstrap/dom/data.ts` | `258a4aeea26efe302af18efdc54d27bf905b1313f2d36604f859f08174edddc3` |
| `js/src/bootstrap/util/index.ts` | `ffb9845e8893a5b87a2c0f86b727981ce01d533eb82e20894e2853ddc1b59669` |
| `js/src/bootstrap/dom/event-handler.ts` | `1a8c72f172bb5214431370e4c8280ca28babb4b76e15d32bb86e99389922fa32` |
| `js/src/bootstrap/dom/manipulator.ts` | `13efe112427a18a757dd2610764124e080796a228c42551c93bdf3c3c01cd391` |
| `js/src/bootstrap/util/config.ts` | `2a860d89c434575446a2947bdeeabc19ee9affdbda814d23da480b2f1bf07a90` |
| `js/src/bootstrap/base-component.ts` | `28e959d70bb8d6b70fcc6767d4aee10cc1e895462ceced57baa1f53a554cdf0f` |
| `js/src/bootstrap/dom/selector-engine.ts` | `02bb15a5af4a7c0e04229fe5350b40e96d4d052ebeb0caba67feba91cb8f09b5` |
| `js/src/bootstrap/util/component-functions.ts` | `c0ed69269c8d0f335dae7ea24733c0b00fc739a064d3e8a9fe327c1f7aedca59` |
| `js/src/sparkline.ts` | `1d45562dc9f597b1a3b5701a6369441462b3bc096270852966f37673037ef2c1` |
