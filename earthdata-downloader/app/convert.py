from __future__ import annotations

import gzip
import json
import math
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


LAT_NAMES = {"lat", "latitude", "y_lat", "nav_lat"}
LON_NAMES = {"lon", "longitude", "long", "x_lon", "nav_lon"}
TIME_NAMES = {"time", "datetime", "date", "timestamp", "observation_time"}


def _match_column(columns: Iterable[str], candidates: set[str]) -> str | None:
    lookup = {str(c).lower(): str(c) for c in columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    for c in columns:
        low = str(c).lower()
        if any(candidate in low for candidate in candidates):
            return str(c)
    return None


def _filter_bbox(df: pd.DataFrame, bbox: dict[str, float]) -> pd.DataFrame:
    lat = _match_column(df.columns, LAT_NAMES)
    lon = _match_column(df.columns, LON_NAMES)
    if not lat or not lon:
        return df
    latv = pd.to_numeric(df[lat], errors="coerce")
    lonv = pd.to_numeric(df[lon], errors="coerce")
    mask = (
        latv.between(bbox["south"], bbox["north"])
        & lonv.between(bbox["west"], bbox["east"])
    )
    out = df.loc[mask].copy()
    rename: dict[str, str] = {}
    if lat != "latitude":
        rename[lat] = "latitude"
    if lon != "longitude":
        rename[lon] = "longitude"
    if rename:
        out.rename(columns=rename, inplace=True)
    return out


def _normalize_time(df: pd.DataFrame) -> pd.DataFrame:
    time_col = _match_column(df.columns, TIME_NAMES)
    if time_col and time_col != "observation_time":
        df = df.rename(columns={time_col: "observation_time"})
    return df


def _apply_meta(df: pd.DataFrame, meta: dict[str, Any]) -> pd.DataFrame:
    if df.empty:
        return df
    for key, value in reversed(list(meta.items())):
        if key not in df.columns:
            df.insert(0, key, value if value is not None else "")
    return df


def _limit(df: pd.DataFrame, max_rows: int) -> pd.DataFrame:
    if max_rows and max_rows > 0 and len(df) > max_rows:
        return df.iloc[:max_rows].copy()
    return df


def _wanted(name: str, filters: list[str]) -> bool:
    if not filters:
        return True
    low = name.lower()
    return any(f.lower() in low for f in filters if f.strip())


def _xarray_file(
    path: Path,
    meta: dict[str, Any],
    filters: list[str],
    bbox: dict[str, float],
    max_rows: int,
) -> list[pd.DataFrame]:
    import xarray as xr

    frames: list[pd.DataFrame] = []
    ds = xr.open_dataset(path, decode_times=True, mask_and_scale=True)
    try:
        for name, arr in ds.data_vars.items():
            if not _wanted(name, filters):
                continue
            if not np.issubdtype(arr.dtype, np.number):
                continue
            try:
                df = arr.to_dataframe(name="value").reset_index()
            except Exception:
                values = np.asarray(arr.values).reshape(-1)
                df = pd.DataFrame({"value": values})
            df.insert(0, "variable", name)
            unit = str(arr.attrs.get("units") or arr.attrs.get("Units") or "")
            df.insert(1, "unit", unit)
            df = _normalize_time(df)
            df = _filter_bbox(df, bbox)
            df = _limit(df, max_rows)
            if not df.empty:
                frames.append(_apply_meta(df, meta))
    finally:
        ds.close()
    return frames


def _h5_datasets(handle: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []

    def visitor(name: str, obj: Any) -> None:
        try:
            import h5py
            if isinstance(obj, h5py.Dataset):
                out.append((name, obj))
        except Exception:
            pass

    handle.visititems(visitor)
    return out


def _hdf5_file(
    path: Path,
    meta: dict[str, Any],
    filters: list[str],
    bbox: dict[str, float],
    max_rows: int,
) -> list[pd.DataFrame]:
    import h5py

    frames: list[pd.DataFrame] = []
    with h5py.File(path, "r") as h:
        datasets = _h5_datasets(h)
        lat_ds = next((obj for name, obj in datasets if name.split("/")[-1].lower() in LAT_NAMES), None)
        lon_ds = next((obj for name, obj in datasets if name.split("/")[-1].lower() in LON_NAMES), None)
        lat_arr = np.asarray(lat_ds) if lat_ds is not None else None
        lon_arr = np.asarray(lon_ds) if lon_ds is not None else None

        for name, ds in datasets:
            base = name.split("/")[-1]
            if base.lower() in LAT_NAMES | LON_NAMES | TIME_NAMES:
                continue
            if not _wanted(name, filters):
                continue
            if not np.issubdtype(ds.dtype, np.number):
                continue
            try:
                arr = np.asarray(ds)
            except Exception:
                continue
            if arr.size == 0:
                continue

            values = arr.reshape(-1)
            df = pd.DataFrame({"variable": name, "value": values})
            unit_raw = ds.attrs.get("units", "")
            if isinstance(unit_raw, bytes):
                unit_raw = unit_raw.decode(errors="ignore")
            df.insert(1, "unit", str(unit_raw))

            if lat_arr is not None and lon_arr is not None and lat_arr.shape == arr.shape and lon_arr.shape == arr.shape:
                df["latitude"] = lat_arr.reshape(-1)
                df["longitude"] = lon_arr.reshape(-1)
                df = _filter_bbox(df, bbox)
            else:
                if arr.ndim:
                    inds = np.unravel_index(np.arange(arr.size), arr.shape)
                    for i, ind in enumerate(inds):
                        df[f"index_{i}"] = ind
            df = _limit(df, max_rows)
            if not df.empty:
                frames.append(_apply_meta(df, meta))
    return frames


def _hdf4_file(
    path: Path,
    meta: dict[str, Any],
    filters: list[str],
    bbox: dict[str, float],
    max_rows: int,
) -> list[pd.DataFrame]:
    from pyhdf.SD import SD, SDC

    h = SD(str(path), SDC.READ)
    try:
        names = list(h.datasets().keys())
        lat_name = next((n for n in names if n.lower() in LAT_NAMES), None)
        lon_name = next((n for n in names if n.lower() in LON_NAMES), None)
        lat_arr = np.asarray(h.select(lat_name).get()) if lat_name else None
        lon_arr = np.asarray(h.select(lon_name).get()) if lon_name else None
        frames: list[pd.DataFrame] = []
        for name in names:
            if name in (lat_name, lon_name) or not _wanted(name, filters):
                continue
            ds = h.select(name)
            arr = np.asarray(ds.get())
            if not np.issubdtype(arr.dtype, np.number) or arr.size == 0:
                continue
            df = pd.DataFrame({"variable": name, "value": arr.reshape(-1)})
            attrs = ds.attributes()
            df.insert(1, "unit", str(attrs.get("units") or attrs.get("Units") or ""))
            if lat_arr is not None and lon_arr is not None and lat_arr.shape == arr.shape and lon_arr.shape == arr.shape:
                df["latitude"] = lat_arr.reshape(-1)
                df["longitude"] = lon_arr.reshape(-1)
                df = _filter_bbox(df, bbox)
            else:
                inds = np.unravel_index(np.arange(arr.size), arr.shape)
                for i, ind in enumerate(inds):
                    df[f"index_{i}"] = ind
            df = _limit(df, max_rows)
            if not df.empty:
                frames.append(_apply_meta(df, meta))
        return frames
    finally:
        h.end()


def _geotiff_file(
    path: Path,
    meta: dict[str, Any],
    filters: list[str],
    bbox: dict[str, float],
    max_rows: int,
) -> list[pd.DataFrame]:
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.warp import transform_bounds, transform

    frames: list[pd.DataFrame] = []
    with rasterio.open(path) as src:
        if src.crs:
            left, bottom, right, top = transform_bounds(
                "EPSG:4326",
                src.crs,
                bbox["west"],
                bbox["south"],
                bbox["east"],
                bbox["north"],
                densify_pts=21,
            )
            window = from_bounds(left, bottom, right, top, src.transform)
            window = window.round_offsets().round_lengths()
            full = rasterio.windows.Window(0, 0, src.width, src.height)
            window = window.intersection(full)
        else:
            window = rasterio.windows.Window(0, 0, src.width, src.height)

        transform_window = src.window_transform(window)
        data = src.read(window=window, masked=True)
        for band in range(data.shape[0]):
            name = (src.descriptions or [None] * src.count)[band] or f"band_{band + 1}"
            if not _wanted(name, filters):
                continue
            arr = data[band]
            rows, cols = np.where(~np.ma.getmaskarray(arr))
            if len(rows) == 0:
                continue
            values = np.asarray(arr[rows, cols], dtype=float)
            xs, ys = rasterio.transform.xy(transform_window, rows, cols, offset="center")
            xs = np.asarray(xs, dtype=float)
            ys = np.asarray(ys, dtype=float)
            if src.crs and str(src.crs).upper() not in ("EPSG:4326", "OGC:CRS84"):
                lons, lats = transform(src.crs, "EPSG:4326", xs.tolist(), ys.tolist())
            else:
                lons, lats = xs.tolist(), ys.tolist()

            df = pd.DataFrame(
                {
                    "variable": name,
                    "unit": "",
                    "latitude": lats,
                    "longitude": lons,
                    "value": values,
                }
            )
            df = _filter_bbox(df, bbox)
            df = _limit(df, max_rows)
            if not df.empty:
                frames.append(_apply_meta(df, meta))
    return frames


def _table_file(
    path: Path,
    meta: dict[str, Any],
    bbox: dict[str, float],
    max_rows: int,
) -> list[pd.DataFrame]:
    sep = "\t" if path.suffix.lower() in (".tsv", ".tab") else None
    if sep:
        df = pd.read_csv(path, sep=sep)
    else:
        try:
            df = pd.read_csv(path)
        except Exception:
            df = pd.read_csv(path, sep=None, engine="python")
    df = _normalize_time(_filter_bbox(df, bbox))
    df = _limit(df, max_rows)
    return [_apply_meta(df, meta)] if not df.empty else []


def _json_file(
    path: Path,
    meta: dict[str, Any],
    bbox: dict[str, float],
    max_rows: int,
) -> list[pd.DataFrame]:
    data = json.loads(path.read_text(errors="ignore"))
    if isinstance(data, dict) and isinstance(data.get("features"), list):
        rows: list[dict[str, Any]] = []
        for feat in data["features"]:
            props = dict(feat.get("properties") or {})
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates")
            if geom.get("type") == "Point" and isinstance(coords, list) and len(coords) >= 2:
                props["longitude"] = coords[0]
                props["latitude"] = coords[1]
            rows.append(props)
        df = pd.DataFrame(rows)
    elif isinstance(data, list):
        df = pd.json_normalize(data)
    elif isinstance(data, dict):
        try:
            df = pd.json_normalize(data)
        except Exception:
            df = pd.DataFrame([data])
    else:
        return []
    df = _normalize_time(_filter_bbox(df, bbox))
    df = _limit(df, max_rows)
    return [_apply_meta(df, meta)] if not df.empty else []


def convert_file(
    path: Path,
    meta: dict[str, Any],
    filters: list[str],
    bbox: dict[str, float],
    max_rows: int,
) -> list[pd.DataFrame]:
    suffix = path.suffix.lower()

    if suffix == ".zip":
        frames: list[pd.DataFrame] = []
        with tempfile.TemporaryDirectory(prefix="earthdata_zip_") as td:
            with zipfile.ZipFile(path) as z:
                z.extractall(td)
            for child in Path(td).rglob("*"):
                if child.is_file():
                    try:
                        frames.extend(convert_file(child, meta, filters, bbox, max_rows))
                    except Exception:
                        continue
        return frames

    if suffix == ".gz":
        target = Path(tempfile.mktemp(prefix="earthdata_gz_", suffix=Path(path.stem).suffix or ".bin"))
        try:
            with gzip.open(path, "rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            return convert_file(target, meta, filters, bbox, max_rows)
        finally:
            target.unlink(missing_ok=True)

    if suffix in (".nc", ".nc4", ".cdf"):
        return _xarray_file(path, meta, filters, bbox, max_rows)
    if suffix in (".h5", ".hdf5", ".he5"):
        try:
            return _xarray_file(path, meta, filters, bbox, max_rows)
        except Exception:
            return _hdf5_file(path, meta, filters, bbox, max_rows)
    if suffix in (".hdf", ".h4"):
        try:
            return _hdf4_file(path, meta, filters, bbox, max_rows)
        except Exception:
            return _hdf5_file(path, meta, filters, bbox, max_rows)
    if suffix in (".tif", ".tiff"):
        return _geotiff_file(path, meta, filters, bbox, max_rows)
    if suffix in (".csv", ".tsv", ".tab", ".txt"):
        return _table_file(path, meta, bbox, max_rows)
    if suffix in (".json", ".geojson"):
        return _json_file(path, meta, bbox, max_rows)

    # Some providers omit useful extensions. Try the scientific readers in order.
    for reader in (_xarray_file, _hdf5_file):
        try:
            return reader(path, meta, filters, bbox, max_rows)
        except Exception:
            pass
    raise ValueError(f"Unsupported or unreadable Earthdata format: {path.name}")


def combine_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True, sort=False)
    preferred = [
        "source",
        "component_query",
        "collection_id",
        "collection_title",
        "granule_id",
        "granule_ur",
        "satellite_platform",
        "instrument",
        "granule_begin",
        "granule_end",
        "observation_time",
        "latitude",
        "longitude",
        "variable",
        "value",
        "unit",
        "original_file",
        "download_url",
    ]
    cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    return df[cols]
