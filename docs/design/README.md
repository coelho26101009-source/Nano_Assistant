# Design references

## `nano-ui-reference.png`

The approved visual reference for the Nano desktop shell: warm near-black
surfaces, dark-glass panels, the flame red of the mark, a top bar of sections,
a conversation rail on the left and the chat on the right.

It lives here and **not** in `frontend/public/`, where it was originally
dropped. Anything under `public/` is copied verbatim into `frontend/out/` by the
static export and then packaged into the installer by electron-builder, so a
1.2 MB mockup of the interface was being shipped inside the interface. It is a
reference, not a runtime asset.

The runtime brand assets stay where they belong, in
`frontend/public/branding/`:

| File | Role |
| --- | --- |
| `nano-mark.png` | the master artwork for the flame mark (opaque, no alpha) |
| `nano-wordmark.png` | the master artwork for the "NANO" lettering (opaque) |
| `nano-mark-alpha.png` | derived: the mark with a real alpha channel, cropped |
| `nano-wordmark-alpha.png` | derived: the wordmark with a real alpha channel, cropped |

The two `-alpha` files are what the UI actually loads. Regenerate them with:

```
python scripts/derive_brand_assets.py
```

and rebuild the desktop and tray icons from the same artwork with:

```
powershell -ExecutionPolicy Bypass -File scripts/build_app_icon.ps1
```
