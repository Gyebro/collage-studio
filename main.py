"""Collage Studio: local image collage editor and print-quality renderer."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("COLLAGE_DATA_DIR", str(BASE_DIR / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Collage Studio", version="0.2.0")

MAX_UPLOAD = 75 * 1024 * 1024
MAX_ZIP = 1024 * 1024 * 1024
MAX_RENDER_PIXELS = 80_000_000
MAX_IMAGE_SIDE = 30_000
SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
SAFE_CELL = re.compile(r"^r(\d+)c(\d+)$")
FORMAT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "TIFF": ".tiff", "BMP": ".bmp", "GIF": ".gif"}


class LayoutRequest(BaseModel):
    columns: int = Field(default=10, ge=2, le=30)
    rows: int = Field(default=10, ge=2, le=30)
    showcase: int = Field(default=6, ge=1, le=30)
    ratio_width: float = Field(default=4.0, gt=0, le=100)
    ratio_height: float = Field(default=5.0, gt=0, le=100)
    border_percent: float = Field(default=0.0, ge=0, le=30)
    border_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")


class AppearanceRequest(BaseModel):
    border_percent: float = Field(ge=0, le=30)
    border_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class TransformRequest(BaseModel):
    zoom: float = Field(ge=1.0, le=4.0)
    pan_x: float = Field(ge=-1.0, le=1.0)
    pan_y: float = Field(ge=-1.0, le=1.0)


class RenderRequest(BaseModel):
    width: float = Field(gt=0, le=100000)
    unit: str = "cm"
    dpi: int = Field(default=300, ge=36, le=1200)
    quality: int = Field(default=94, ge=60, le=100)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_folder(project_id: str) -> Path:
    if not SAFE_ID.fullmatch(project_id):
        raise HTTPException(404, "Project not found")
    path = DATA_DIR / project_id
    if not path.is_dir():
        raise HTTPException(404, "Project not found")
    return path


def load_project(project_id: str) -> dict[str, Any]:
    path = project_folder(project_id) / "project.json"
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
        # Bundles produced by v0.1 did not have border settings.
        project.setdefault("border_percent", 0.0)
        project.setdefault("border_color", "#ffffff")
        return project
    except (OSError, json.JSONDecodeError):
        raise HTTPException(500, "Cannot read project configuration")


def save_project(project_id: str, project: dict[str, Any]) -> None:
    folder = project_folder(project_id)
    project["updated_at"] = utcnow()
    target = folder / "project.json"
    tmp = folder / "project.json.tmp"
    tmp.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def showcase_bounds(p: dict) -> tuple[int, int, int, int]:
    """Return showcase extent (left, top, right exclusive, bottom exclusive)."""
    n = p["showcase"]
    x0 = (p["columns"] - n) // 2
    y0 = (p["rows"] - n) // 2
    return x0, y0, x0 + n, y0 + n


def expected_keys(p: dict) -> list[str]:
    x0, y0, x1, y1 = showcase_bounds(p)
    keys = ["showcase"]
    for r in range(p["rows"]):
        for c in range(p["columns"]):
            if not (x0 <= c < x1 and y0 <= r < y1):
                keys.append(f"r{r}c{c}")
    return keys


def validate_cell(p: dict, cell: str) -> None:
    if cell not in expected_keys(p):
        raise HTTPException(400, "Invalid or covered grid cell")


def create_project(layout: dict[str, Any]) -> dict:
    p = LayoutRequest(**layout)
    if p.showcase > min(p.columns, p.rows):
        raise HTTPException(422, "Showcase must fit within the grid")
    project_id = uuid.uuid4().hex
    folder = DATA_DIR / project_id
    (folder / "images").mkdir(parents=True)
    (folder / "previews").mkdir()
    (folder / "renders").mkdir()
    project = {
        "version": 1,
        "id": project_id,
        "title": "Untitled collage",
        **p.model_dump(),
        "tiles": {},
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    save_project(project_id, project)
    return project


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_fingerprints(p: dict) -> None:
    """Backfill hashes for projects created before duplicate detection existed."""
    changed = False
    folder = project_folder(p["id"])
    for tile in p["tiles"].values():
        if not re.fullmatch(r"[0-9a-f]{64}", str(tile.get("sha256", ""))):
            tile["sha256"] = sha256_file(image_path(folder, tile))
            changed = True
    if changed:
        save_project(p["id"], p)


def duplicate_diagnostics(p: dict) -> dict:
    groups: dict[str, list[str]] = {}
    for key, tile in p["tiles"].items():
        groups.setdefault(tile["sha256"], []).append(key)
    repeated = [
        {"cells": sorted(keys), "filenames": [p["tiles"][key]["original_filename"] for key in sorted(keys)]}
        for keys in groups.values() if len(keys) > 1
    ]
    repeated.sort(key=lambda entry: entry["cells"][0])
    return {"duplicate_groups": repeated, "duplicate_repeats": sum(len(g["cells"]) - 1 for g in repeated)}


def api_project(p: dict) -> dict:
    ensure_fingerprints(p)
    result = dict(p)
    result["expected_cells"] = len(expected_keys(p))
    result["filled_cells"] = len(p["tiles"])
    result["showcase_bounds"] = list(showcase_bounds(p))
    result.update(duplicate_diagnostics(p))
    return result


def image_path(folder: Path, image_info: dict) -> Path:
    # Image name is never taken from an arbitrary user path.
    filename = image_info.get("asset", "")
    if not re.fullmatch(r"[a-f0-9]{32}\.(jpg|png|webp|tiff|bmp|gif)", filename):
        raise HTTPException(400, "Unsafe image asset reference")
    path = folder / "images" / filename
    if not path.is_file():
        raise HTTPException(404, "Project image is missing")
    return path


def make_preview(source: Path, destination: Path) -> None:
    with Image.open(source) as src:
        im = ImageOps.exif_transpose(src)
        im.thumbnail((1300, 1300), Image.Resampling.LANCZOS)
        if im.mode != "RGB":
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, "white")
            bg.paste(rgba, mask=rgba.getchannel("A"))
            im = bg
        im.save(destination, format="JPEG", quality=88, optimize=True)


def decode_image(raw: bytes) -> str:
    try:
        with Image.open(io.BytesIO(raw)) as image:
            fmt = image.format
            if fmt not in FORMAT_EXT:
                raise ValueError("Unsupported image type")
            if image.width < 1 or image.height < 1:
                raise ValueError("Invalid image size")
            image.verify()
            return FORMAT_EXT[fmt]
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, f"Image cannot be read: {exc}")


def pixels_for_request(p: dict, req: RenderRequest) -> tuple[int, int]:
    if req.unit not in {"px", "in", "cm", "mm"}:
        raise HTTPException(422, "Units must be px, in, cm, or mm")
    multiplier = {"px": 1, "in": req.dpi, "cm": req.dpi / 2.54, "mm": req.dpi / 25.4}[req.unit]
    width = round(req.width * multiplier)
    height = round(width * p["ratio_height"] / p["ratio_width"])
    if width < 1 or height < 1:
        raise HTTPException(422, "Output dimensions are too small")
    if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE or width * height > MAX_RENDER_PIXELS:
        raise HTTPException(422, "Requested image exceeds 80 megapixels or 30,000 pixels on a side")
    return width, height


def paint_tile(base: Image.Image, src_path: Path, rect: tuple[int, int, int, int], tile: dict) -> None:
    x0, y0, x1, y1 = rect
    cw, ch = x1 - x0, y1 - y0
    if cw <= 0 or ch <= 0:
        return
    with Image.open(src_path) as loaded:
        img = ImageOps.exif_transpose(loaded)
        if img.mode == "RGBA" or "transparency" in img.info:
            rgba = img.convert("RGBA")
            solid = Image.new("RGB", rgba.size, "white")
            solid.paste(rgba, mask=rgba.getchannel("A"))
            img = solid
        else:
            img = img.convert("RGB")
        zoom = float(tile.get("zoom", 1.0))
        pan_x = float(tile.get("pan_x", 0.0))
        pan_y = float(tile.get("pan_y", 0.0))
        scale = max(cw / img.width, ch / img.height) * zoom
        sw = max(cw, math.ceil(img.width * scale))
        sh = max(ch, math.ceil(img.height * scale))
        resized = img.resize((sw, sh), resample=Image.Resampling.LANCZOS)
        overflow_x = sw - cw
        overflow_y = sh - ch
        offset_x = round(overflow_x * (pan_x + 1) / 2)
        offset_y = round(overflow_y * (pan_y + 1) / 2)
        crop = resized.crop((offset_x, offset_y, offset_x + cw, offset_y + ch))
        base.paste(crop, (x0, y0))


def content_rect(
    x_edges: list[int], y_edges: list[int],
    extent: tuple[int, int, int, int], gap: int,
) -> tuple[int, int, int, int]:
    """Inset only shared edges; keep the exterior edge of the collage border-free."""
    c0, r0, c1, r1 = extent
    left = x_edges[c0] + (gap // 2 if c0 else 0)
    right = x_edges[c1] - (gap - gap // 2 if c1 < len(x_edges) - 1 else 0)
    top = y_edges[r0] + (gap // 2 if r0 else 0)
    bottom = y_edges[r1] - (gap - gap // 2 if r1 < len(y_edges) - 1 else 0)
    return left, top, right, bottom


def render_collage(p: dict, folder: Path, output: Path, size: tuple[int, int], dpi: int, quality: int) -> None:
    width, height = size
    # Width is a percentage of the shortest *small* grid-cell edge; the gap
    # therefore stays proportional across output pixel sizes and print DPI.
    gap = round(min(width / p["columns"], height / p["rows"]) * p.get("border_percent", 0) / 100)
    canvas = Image.new("RGB", (width, height), p.get("border_color", "#ffffff"))
    x_edges = [round(c * width / p["columns"]) for c in range(p["columns"] + 1)]
    y_edges = [round(r * height / p["rows"]) for r in range(p["rows"] + 1)]
    x0, y0, x1, y1 = showcase_bounds(p)
    for r in range(p["rows"]):
        for c in range(p["columns"]):
            if x0 <= c < x1 and y0 <= r < y1:
                continue
            rect = content_rect(x_edges, y_edges, (c, r, c + 1, r + 1), gap)
            canvas.paste("#e8e7e2", rect)
            tile = p["tiles"].get(f"r{r}c{c}")
            if tile:
                paint_tile(canvas, image_path(folder, tile), rect, tile)
    hero_rect = content_rect(x_edges, y_edges, (x0, y0, x1, y1), gap)
    canvas.paste("#e8e7e2", hero_rect)
    if "showcase" in p["tiles"]:
        paint_tile(canvas, image_path(folder, p["tiles"]["showcase"]), hero_rect, p["tiles"]["showcase"])
    canvas.save(output, format="JPEG", quality=quality, subsampling=0, dpi=(dpi, dpi), optimize=True)


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/static/{filename}")
def static_file(filename: str):
    if filename not in {"styles.css", "app.js", "favicon.svg"}:
        raise HTTPException(404)
    return FileResponse(BASE_DIR / "static" / filename)


@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.2.0"}


@app.post("/api/projects")
def new_project(req: LayoutRequest):
    return api_project(create_project(req.model_dump()))


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    return api_project(load_project(project_id))


@app.post("/api/projects/{project_id}/images")
async def upload_image(project_id: str, cell: str = Form(...), file: UploadFile = File(...)):
    p = load_project(project_id)
    validate_cell(p, cell)
    raw = await file.read(MAX_UPLOAD + 1)
    if not raw:
        raise HTTPException(400, "Uploaded image is empty")
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Image exceeds the 75 MB limit")
    extension = decode_image(raw)
    folder = project_folder(project_id)
    image_id = uuid.uuid4().hex
    asset = image_id + extension
    source = folder / "images" / asset
    preview = folder / "previews" / (image_id + ".jpg")
    source.write_bytes(raw)
    try:
        make_preview(source, preview)
    except (OSError, Image.DecompressionBombError) as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(400, f"Image preview failed: {exc}")
    old = p["tiles"].get(cell)
    p["tiles"][cell] = {
        "asset": asset,
        "original_filename": (file.filename or "image")[:250],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "zoom": 1.0,
        "pan_x": 0.0,
        "pan_y": 0.0,
    }
    save_project(project_id, p)
    if old:
        old_image = folder / "images" / old["asset"]
        old_image.unlink(missing_ok=True)
        (folder / "previews" / (Path(old["asset"]).stem + ".jpg")).unlink(missing_ok=True)
    return api_project(p)


@app.patch("/api/projects/{project_id}/appearance")
def update_appearance(project_id: str, req: AppearanceRequest):
    p = load_project(project_id)
    p.update(req.model_dump())
    save_project(project_id, p)
    return api_project(p)


@app.patch("/api/projects/{project_id}/tiles/{cell}")
def transform_image(project_id: str, cell: str, req: TransformRequest):
    p = load_project(project_id)
    validate_cell(p, cell)
    if cell not in p["tiles"]:
        raise HTTPException(404, "Cell is empty")
    p["tiles"][cell].update(req.model_dump())
    save_project(project_id, p)
    return p["tiles"][cell]


@app.delete("/api/projects/{project_id}/tiles/{cell}")
def delete_image(project_id: str, cell: str):
    p = load_project(project_id)
    validate_cell(p, cell)
    old = p["tiles"].pop(cell, None)
    if old:
        folder = project_folder(project_id)
        image_path(folder, old).unlink(missing_ok=True)
        (folder / "previews" / (Path(old["asset"]).stem + ".jpg")).unlink(missing_ok=True)
        save_project(project_id, p)
    return api_project(p)


@app.get("/api/projects/{project_id}/previews/{image_id}")
def get_preview(project_id: str, image_id: str):
    if not SAFE_ID.fullmatch(image_id):
        raise HTTPException(404)
    p = load_project(project_id)
    if not any(Path(tile["asset"]).stem == image_id for tile in p["tiles"].values()):
        raise HTTPException(404)
    path = project_folder(project_id) / "previews" / f"{image_id}.jpg"
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/projects/{project_id}/config.json")
def download_config(project_id: str):
    p = load_project(project_id)
    ensure_fingerprints(p)
    payload = json.loads(json.dumps(p))
    # configuration references portable asset paths, available in the full ZIP
    for tile in payload["tiles"].values():
        tile["path"] = "images/" + tile["asset"]
    return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="collage-project.json"'})


@app.get("/api/projects/{project_id}/bundle.zip")
def download_bundle(project_id: str):
    p = load_project(project_id)
    ensure_fingerprints(p)
    folder = project_folder(project_id)
    output = folder / "collage-project.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=4) as archive:
        archive.writestr("project.json", json.dumps(p, indent=2, ensure_ascii=False))
        for tile in p["tiles"].values():
            source = image_path(folder, tile)
            archive.write(source, arcname="images/" + tile["asset"])
    return FileResponse(output, media_type="application/zip", filename="collage-project.zip")


@app.post("/api/import-zip")
async def import_bundle(file: UploadFile = File(...)):
    # Stream to disk rather than loading a potentially large photo archive in RAM.
    with tempfile.TemporaryFile() as upload_copy:
        bytes_received = 0
        while chunk := await file.read(4 * 1024 * 1024):
            bytes_received += len(chunk)
            if bytes_received > MAX_ZIP:
                raise HTTPException(413, "Project ZIP exceeds the 1 GB limit")
            upload_copy.write(chunk)
        upload_copy.seek(0)
        try:
            archive = zipfile.ZipFile(upload_copy)
        except (zipfile.BadZipFile, OSError):
            raise HTTPException(400, "Invalid project ZIP")
        with archive:
            try:
                info = archive.getinfo("project.json")
                if info.file_size > 2_000_000:
                    raise ValueError("Configuration exceeds size limit")
                saved = json.loads(archive.read(info))
                layout = LayoutRequest(**{
                    **{k: saved[k] for k in ("columns", "rows", "showcase", "ratio_width", "ratio_height")},
                    "border_percent": saved.get("border_percent", 0),
                    "border_color": saved.get("border_color", "#ffffff"),
                })
                if layout.showcase > min(layout.columns, layout.rows):
                    raise ValueError("Showcase does not fit in grid")
                raw_tiles = saved.get("tiles", {})
                valid_keys = set(expected_keys(layout.model_dump()))
                if not isinstance(raw_tiles, dict) or any(k not in valid_keys for k in raw_tiles):
                    raise ValueError("ZIP refers to invalid cells")
                if len(raw_tiles) > 900:
                    raise ValueError("Too many cells")
                total = 0
                verified: dict[str, tuple[str, dict]] = {}
                seen_assets: set[str] = set()
                for key, tile in raw_tiles.items():
                    asset = tile["asset"]
                    if not re.fullmatch(r"[a-f0-9]{32}\.(jpg|png|webp|tiff|bmp|gif)", asset):
                        raise ValueError("Unsafe asset filename")
                    if asset in seen_assets:
                        raise ValueError("Every image cell must have its own asset")
                    seen_assets.add(asset)
                    zipinfo = archive.getinfo("images/" + asset)
                    total += zipinfo.file_size
                    if zipinfo.file_size > MAX_UPLOAD or total > MAX_ZIP:
                        raise ValueError("Project media exceeds size limit")
                    # Decompress and validate one asset at a time; don't retain originals in RAM.
                    with archive.open(zipinfo) as source:
                        body = source.read(MAX_UPLOAD + 1)
                    if len(body) != zipinfo.file_size:
                        raise ValueError("ZIP contains truncated or oversized image data")
                    ext = decode_image(body)
                    if ext != Path(asset).suffix:
                        raise ValueError("Image format does not match its extension")
                    transform = TransformRequest(
                        zoom=float(tile.get("zoom", 1)),
                        pan_x=float(tile.get("pan_x", 0)),
                        pan_y=float(tile.get("pan_y", 0)),
                    )
                    verified[key] = (asset, {
                        "original_filename": str(tile.get("original_filename", "image"))[:250],
                        "sha256": hashlib.sha256(body).hexdigest(),
                        **transform.model_dump(),
                    })
                p = create_project(layout.model_dump())
                p["title"] = str(saved.get("title", "Imported collage"))[:120]
                folder = project_folder(p["id"])
                try:
                    for key, (asset, fields) in verified.items():
                        with archive.open("images/" + asset) as src, (folder / "images" / asset).open("wb") as dst:
                            shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
                        make_preview(folder / "images" / asset, folder / "previews" / (Path(asset).stem + ".jpg"))
                        p["tiles"][key] = {"asset": asset, **fields}
                    save_project(p["id"], p)
                except Exception:
                    shutil.rmtree(folder, ignore_errors=True)
                    raise
                return api_project(p)
            except (KeyError, ValueError, TypeError, IndexError, json.JSONDecodeError, OSError, ValidationError, zipfile.BadZipFile) as exc:
                raise HTTPException(400, f"Cannot import ZIP: {exc}")


@app.post("/api/projects/{project_id}/render")
def render(project_id: str, req: RenderRequest):
    p = load_project(project_id)
    size = pixels_for_request(p, req)
    folder = project_folder(project_id)
    output = folder / "renders" / ("collage-" + uuid.uuid4().hex + ".jpg")
    try:
        render_collage(p, folder, output, size, req.dpi, req.quality)
    except (OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(500, f"Rendering failed: {exc}")
    return FileResponse(
        output,
        media_type="image/jpeg",
        filename=f"collage-{size[0]}x{size[1]}-{req.dpi}dpi.jpg",
        headers={"X-Collage-Width": str(size[0]), "X-Collage-Height": str(size[1])},
    )
