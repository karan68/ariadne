# Article diagram images

These PNGs are the rendered versions of the Mermaid diagrams embedded in
[`../medium-article.md`](../medium-article.md). Medium (and most blogs) can't render Mermaid
directly, so upload these images and use each figure's italic caption line.

| File | Figure |
| --- | --- |
| `figure1-lifecycle.png` | Fig 1 — the `remember → recall → improve → forget` memory lifecycle |
| `figure2-two-doors.png` | Fig 2 — two doors (clinician / patient) into one memory |
| `figure3-before-after.png` | Fig 3 — the same patient before/after a connected memory |
| `figure4-abdm-product.png` | Fig 4 — the product on India's ABDM/ABHA rails |

## Regenerating

The Mermaid source lives inline in `../medium-article.md`. To re-render after an edit, use
[`@mermaid-js/mermaid-cli`](https://github.com/mermaid-js/mermaid-cli) pointed at a local
Chromium-based browser (no Chromium download needed):

```powershell
# from a scratch dir
$env:PUPPETEER_SKIP_DOWNLOAD="true"
npm install @mermaid-js/mermaid-cli

# puppeteer.json -> { "executablePath": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" }
# save each diagram's fenced body to a .mmd file, then:
mmdc -i figureN.mmd -o figureN.png -p puppeteer.json -b white -s 3
```

`-s 3` renders at 3x for crisp text; `-b white` gives a solid background for Medium.
