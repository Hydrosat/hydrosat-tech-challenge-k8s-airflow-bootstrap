#!/usr/bin/env python3
"""
Simulate Sentinel-2 satellite data for the configured AOI and upload to S3.

Generates a two-band GeoTIFF (B04 Red, B08 NIR) whose reflectance values
evolve with crop phenology so that NDVI increases realistically after each
field's planting date.

Usage (inside a KubernetesPodOperator pod):
    python download_satellite.py --date 2024-04-15 \
                                 --config /opt/airflow/dags/config/pipeline_config.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime

import boto3
import numpy as np
import rasterio
from botocore.exceptions import ClientError
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
import yaml


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def s3_client(cfg: dict):
    s3 = cfg["s3"]
    return boto3.client(
        "s3",
        endpoint_url=s3["endpoint_url"],
        aws_access_key_id=s3["access_key"],
        aws_secret_access_key=s3["secret_key"],
        region_name="us-east-1",
    )


# ── Simulation ───────────────────────────────────────────────────────────────

def _aoi_bounds(aoi: dict) -> tuple[float, float, float, float]:
    coords = aoi["coordinates"][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return min(lons), min(lats), max(lons), max(lats)


def simulate_sentinel2(
    aoi: dict,
    fields: list[dict],
    date_str: str,
    resolution: float,
    max_pixels: int,
) -> tuple[np.ndarray, dict]:
    """Return (data[2,H,W], rasterio_profile) with simulated B04 / B08."""
    min_lon, min_lat, max_lon, max_lat = _aoi_bounds(aoi)

    width = min(int((max_lon - min_lon) / resolution), max_pixels)
    height = min(int((max_lat - min_lat) / resolution), max_pixels)
    transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)

    # Reproducible pseudo-random values keyed to date
    seed = int(hashlib.md5(date_str.encode()).hexdigest()[:8], 16)
    rng = np.random.RandomState(seed)
    exec_date = datetime.strptime(date_str, "%Y-%m-%d")

    # Start with bare-soil reflectance
    red = 0.15 + rng.uniform(-0.02, 0.02, (height, width)).astype(np.float32)
    nir = 0.20 + rng.uniform(-0.02, 0.02, (height, width)).astype(np.float32)

    # Overlay crop phenology per field
    for field in fields:
        planting = datetime.strptime(field["planting_date"], "%Y-%m-%d")
        days = (exec_date - planting).days
        if days < 0:
            continue

        # Logistic growth: NDVI rises from ~0.1 (bare) to ~0.8 (mature) in 120 d
        g = min(1.0, days / 120.0)
        target_red = 0.12 * (1.0 - g) + 0.04 * g
        target_nir = 0.22 * (1.0 - g) + 0.60 * g

        # Rasterise the rectangular field polygon to pixel indices
        fc = field["geometry"]["coordinates"][0]
        flons = [c[0] for c in fc]
        flats = [c[1] for c in fc]
        c0 = max(0, int((min(flons) - min_lon) / (max_lon - min_lon) * width))
        c1 = min(width, int((max(flons) - min_lon) / (max_lon - min_lon) * width))
        r0 = max(0, int((max_lat - max(flats)) / (max_lat - min_lat) * height))
        r1 = min(height, int((max_lat - min(flats)) / (max_lat - min_lat) * height))

        noise_r = rng.uniform(-0.02, 0.02, (r1 - r0, c1 - c0)).astype(np.float32)
        noise_n = rng.uniform(-0.02, 0.02, (r1 - r0, c1 - c0)).astype(np.float32)
        red[r0:r1, c0:c1] = target_red + noise_r
        nir[r0:r1, c0:c1] = target_nir + noise_n

    red = np.clip(red, 0.0, 1.0)
    nir = np.clip(nir, 0.0, 1.0)

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": width,
        "height": height,
        "count": 2,
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
    }
    return np.stack([red, nir]), profile


# ── Upload ───────────────────────────────────────────────────────────────────

def upload_geotiff(client, bucket: str, key: str, data: np.ndarray, profile: dict):
    with MemoryFile() as memfile:
        with memfile.open(**profile) as dst:
            dst.write(data)
            dst.set_band_description(1, "B04_Red_665nm")
            dst.set_band_description(2, "B08_NIR_842nm")
        client.put_object(Bucket=bucket, Key=key, Body=memfile.read())


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Simulate & upload Sentinel-2 data")
    ap.add_argument("--date", required=True, help="Execution date YYYY-MM-DD")
    ap.add_argument("--config", required=True, help="Pipeline config YAML path")
    args = ap.parse_args()

    print(f"=== Sentinel-2 Download / Simulation ===")
    print(f"    Date  : {args.date}")
    print(f"    Config: {args.config}")

    cfg = load_config(args.config)
    client = s3_client(cfg)
    bucket = cfg["s3"]["bucket"]
    key = f"raw/{args.date}/sentinel2_bands.tif"

    # Idempotency: skip if the object already exists
    try:
        client.head_object(Bucket=bucket, Key=key)
        print(f"    ↳ Already exists at s3://{bucket}/{key} – skipping.")
        return
    except ClientError:
        pass

    sim = cfg["simulation"]
    data, profile = simulate_sentinel2(
        aoi=cfg["aoi"],
        fields=cfg["fields"],
        date_str=args.date,
        resolution=sim["resolution"],
        max_pixels=sim["max_pixels"],
    )
    print(f"    Raster shape: {data.shape}  (bands × height × width)")

    print(f"    Uploading → s3://{bucket}/{key}")
    upload_geotiff(client, bucket, key, data, profile)

    # Sidecar metadata
    meta = {
        "date": args.date,
        "source": "simulated_sentinel2",
        "bands": ["B04_Red_665nm", "B08_NIR_842nm"],
        "crs": str(profile["crs"]),
        "width": profile["width"],
        "height": profile["height"],
        "aoi": cfg["aoi"],
    }
    client.put_object(
        Bucket=bucket,
        Key=f"raw/{args.date}/metadata.json",
        Body=json.dumps(meta, indent=2),
        ContentType="application/json",
    )
    print("    Done ✓")


if __name__ == "__main__":
    main()

