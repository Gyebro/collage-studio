# Collage Studio

A local, lightweight collage editor with an interactive browser interface and a Python/Pillow high-resolution JPEG renderer. No account, CDN, or cloud upload required.

## Initial prompt

> Create a simple collage editing tool. 
> Main requirements:
> For new projects, the layout would be configured with a main grid (default 10x10), an aspect ratio (default width:height 4:5) and a center "showcase" image, which takes up N middle cells of the grid (default 6, resulting in a 6x6 area of the grid being covered by the showcase image).
> Once a project is set up, the tool should have the following functionality.
> 1. clicking on a grid element allows the user to browse for an image, then, that image will be placed in the corresponding grid cell.
> 2. this can also be achieved by drag-and-drop too (not essential, but nice-to-have feature)
> 3. the user can replace assigned images the same way
> 4. at any point, the tool should display the number of filled/empty images (in the default layout, 64 small + 1 showcase images are needed).
> 5. there is a fine-tune mode, which allows the user to adjust the position or cropping of the image with respect to the grid cell. The image should always fill the grid cell, but scaling or movement adjustment should be possible.
> 6. the user can export the layout into a suitable configuration file (e.g. json or yaml)
> 7. finally, the user should be able to actually render the collage, write a single jpeg image after prompting for actual image dimensions and required DPI values.
> 
> The tool should preferably use Python and or Javascript with a web based frontend. Although, if there are suitable python UI-s, it can use those as well (the tool should not necessarily be a web based tool).
> Please ask questions or extra requirements where unclear, then implement an initial version.

## Quick start

**Requirements:** Python 3.10+ and a modern browser (Chrome, Firefox, Edge, Safari).

```bash
cd collage-studio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

The tool opens at **http://127.0.0.1:8000** in your browser. If no browser opens automatically, visit that URL manually. To stop the local server, press `Ctrl+C` in the terminal.

On macOS/Linux, `python3` can be used instead of `python`. On Windows, you may need `py -m pip install -r requirements.txt` and `py run.py`.

The optional `COLLAGE_PORT` environment variable changes the port (default: `8000`). `COLLAGE_NO_BROWSER=1` disables automatic browser opening. `COLLAGE_DATA_DIR` can point to another storage folder.

## Features and how to use them

1. **Create a project.** Default layout: **10 columns × 10 rows**, **4:5 width-to-height aspect ratio**, and **6 × 6 center showcase**. That layout requires **64 small pictures + 1 showcase picture = 65 images**. Click **New project** for other grid dimensions, showcase sizes, or aspect ratios.
2. **Add and replace photos.** In **Add photos** mode, click any visible small cell or the center showcase, then choose a file. To replace an image, click the same cell again. You can also drag a file directly onto a cell. Accepts common formats supported by Pillow: JPEG, PNG, WebP, TIFF, BMP, GIF (first frame). The preview is optimized, but the renderer always uses the original uploaded images.
3. **Track progress.** The side panel continuously reports filled, remaining, and total photo slots. Covered cells under the showcase are not counted separately.
4. **Fine-tune framing.** Select **Fine-tune** and click a filled cell. Use the zoom (100–400%) and left/right/top/bottom focus controls, or drag directly on the selected photo to reposition it. The photo always fills its cell (no letterboxing). Use **Reset crop**, **Replace**, or **Remove image from cell** as needed. In Fine-tune, double-click a cell to replace its image.
5. **Save the editable project.** Use **Save editable project (.zip)**. This ZIP includes `project.json` and **all original uploaded images**, so it is fully portable. Later, choose **Open project** to restore a ZIP. The **Export config JSON** option downloads layout, file references, and transforms **only**; it does not embed image bytes, so keep the ZIP when you need a portable backup.
6. **Render your JPEG.** Click **Render JPEG**, choose output width, units (centimeters / millimeters / inches / pixels), desired DPI and JPEG quality. Height is automatically calculated from the project's aspect ratio. The output is a single full-resolution JPEG; DPI is stored in the JPEG metadata. The tool supports renders up to **80 megapixels** or **30,000 pixels per side**, whichever is lower.

**Example:** Default 4:5 layout, width **20 cm**, and **300 DPI** yields a **2,362 × 2,953-pixel** JPEG. If you select `px`, the entered width is the pixel width and DPI is stored as metadata rather than affecting the pixel dimensions.

## Behavior and design decisions

- There are **no physical gutters or strokes in exported JPEGs**. Toggleable grid lines are only editing guides and never appear in the render.
- **Empty cells** are rendered in a neutral light gray (`#e8e7e2`). Export is allowed before all photos are filled, and the render dialog warns how many are missing.
- **Cropping is always cover-fit**. Photos can be moved only within the spare cropped area so no part of a cell becomes blank. Zoom creates additional room for panning.
- For grid/showcase dimensions with odd leftover cells, the showcase is centered as closely as integer cells allow, with the extra row/column on the **bottom/right**.
- Output height is computed from the aspect ratio, rather than entered independently, so rendered JPEGs preserve the exact chosen project proportions (within integer pixel rounding).
- JPEGs are saved in RGB color. Transparent pixels are composited onto white; embedded color profiles are not currently color-managed. DPI metadata does not improve the source photos' intrinsic resolution.
- Local projects are saved under `data/<project-id>/` while the server runs and survive restarts. The browser remembers the most recently opened project on the same browser/device. For reliable backups or transfer between computers, **export a ZIP**.

## Limits and privacy

The local server listens only on `127.0.0.1` and stores photos on your machine under `data/` (or `COLLAGE_DATA_DIR`). It does not send photos to an external service. There is no login or encryption; use this as a personal desktop tool and **do not expose its port to the internet**.

Maximum image upload: **75 MB per image**. Maximum import ZIP: **1 GB**. Input images must be Pillow-readable. Large input images or complex projects can require significant CPU/RAM during rendering.

## Tests

Install `pytest` if needed, then run:

```bash
python -m pytest -q
```

The suite covers the default layout/count, image upload/replace/remove, crop settings, rejected covered cells, ZIP save/load, and JPEG colors/dimensions/DPI.

## Project format

The portable ZIP has this structure:

```text
collage-project.zip
├── project.json
└── images/
    ├── <random-image-id>.jpg
    └── ...
```

`project.json` stores `version`, `columns`, `rows`, `showcase`, `ratio_width`, `ratio_height`, and a `tiles` dictionary. Each key is either `"showcase"` or `"r{row}c{column}"` (zero-indexed); each value contains its `asset`, `original_filename`, `zoom`, `pan_x`, and `pan_y`. Zoom is in `[1.0, 4.0]`; both pan values are in `[-1.0, 1.0]`. Image references inside `project.json` use the image filename; exported standalone JSON adds the portable `path` field for convenience.

## Possible next iterations

Optional gutters/borders/backgrounds, rotation, bulk import with automatic photo placement, a re-orderable photo library, color-profile-aware print export, and an explicit project rename field.
