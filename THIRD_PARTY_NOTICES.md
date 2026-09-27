# Third-party notices: packaged web UI

DagSentry's own LICENSE is unchanged. This inventory covers the third-party assets
shipped with its web UI; Python dependencies retain their respective licenses.

## Tabler Core 1.6.0 (free, MIT)

- Project: https://github.com/tabler/tabler
- Exact release: https://github.com/tabler/tabler/releases/tag/%40tabler%2Fcore%401.6.0
- Source commit: `18023036af414cc3c9820dd516937ee486281c48`
- Official npm artifact: https://registry.npmjs.org/@tabler/core/-/core-1.6.0.tgz
- Artifact SHA-512 (npm integrity, verified before extraction):
  `AdYNV9SVWQy1h3SvcvRG0WH/N7objsf4tzRFVcRdKq7EUogF5hH9EkJup5SycBMrs7g7fGZHdY9h/s18rgy/Ww==`
- Included file: `vendor/tabler-1.6.0/tabler.min.css`, copied byte-for-byte from
  `package/dist/css/tabler.min.css` in the artifact.
- CSS SHA-256: `60bc8b4432e7778016e3675b79181f441fa617ac8b5387f8372a860cbb459a82`
- Copyright (c) 2018-2026 The Tabler Authors.
- The CSS also preserves its original header credit:
  Copyright 2018-2026 codecalm.net Paweł Kuna.
- Full license: `vendor/tabler-1.6.0/LICENSE.tabler`.
- License source: https://github.com/tabler/tabler/blob/18023036af414cc3c9820dd516937ee486281c48/LICENSE

Only the core stylesheet is redistributed. Its embedded SVG control glyphs
(select/check/radio/close/navigation indicators) are part of that stylesheet and
covered by its Tabler/Bootstrap provenance. No standalone Tabler Icons package,
icon font, web font, photo, illustration, demo page, paid/Pro asset, ApexCharts,
plugin, source map, or third-party JavaScript is included. System fonts are
selected by CSS and are not redistributed. The CSS has no external imports,
font-face declarations, or network asset URLs; its image URLs are embedded data.

## Bootstrap-derived CSS (MIT)

Tabler 1.6.0 vendors and maintains Bootstrap-derived SCSS under
`core/scss/bootstrap/` and `core/scss/mixins/bootstrap/`. The shipped CSS includes
that derived code. Its exact version here is the Tabler source snapshot above;
a separate upstream Bootstrap release number is not asserted.

- Upstream: https://github.com/twbs/bootstrap
- Copyright (c) 2011-2025 The Bootstrap Authors.
- Full license: `vendor/tabler-1.6.0/LICENSE.bootstrap`.
- License copied from the same Tabler source snapshot:
  https://github.com/tabler/tabler/blob/18023036af414cc3c9820dd516937ee486281c48/core/js/src/bootstrap/LICENSE

Bootstrap JavaScript and Popper are not shipped. Existing DagSentry JavaScript
continues to handle navigation, forms and native HTML dialogs.

## Normalize.css-derived reset (MIT)

Tabler's `core/scss/bootstrap/_reboot.scss` explicitly credits Normalize.css as
the source of its normalization rules. This derived reset is included in the
stylesheet; the standalone Normalize.css stylesheet is not included.

- Upstream: https://github.com/necolas/normalize.css
- Copyright © Nicolas Gallagher and Jonathan Neal.
- Full license: `vendor/tabler-1.6.0/LICENSE.normalize`.
- License source: https://github.com/necolas/normalize.css/blob/8.0.1/LICENSE.md
- The license was taken from upstream 8.0.1; the actual derived code is pinned
  to Tabler's source snapshot, not represented as an unmodified 8.0.1 copy.

## License review and distribution

All three included license texts were read. Their MIT grants permit use,
modification, distribution, sublicensing and selling copies, including commercial
use and source redistribution, subject to retaining the copyright and permission
notices. Their warranty/liability disclaimers are preserved in the full texts.
There is no requirement in these MIT licenses to publish DagSentry's own source.

These notices and all three license files are included in both the source archive
and Python wheel. In an installed package this document is located at
`dagsentry/web/THIRD_PARTY_NOTICES.md`; asset/license paths above are relative to
that directory. In the source tree they are under `src/dagsentry/web/`.

## Reproducibility

Download the exact npm artifact above over HTTPS, compare its SHA-512 with the
pinned integrity, extract only `package/dist/css/tabler.min.css`, and verify the
CSS SHA-256 above. Obtain license texts from the pinned source links. Do not copy
the npm package's demo/plugins or run its build/install scripts. A frontend build,
Node runtime, CDN connection or package manager is not required in production.
