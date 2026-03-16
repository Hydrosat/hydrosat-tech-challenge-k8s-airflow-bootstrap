#!/usr/bin/env python3
"""
Process satellite data for a single agricultural field.

For one (field, date) pair this script:
  1. Reads the Sentinel-2 GeoTIFF from S3 (produced by DAG 1).
  2. Clips to the field extent.
  3. Computes NDVI = (B08 − B04) / (B08 + B04).
  4. Loads yesterday's cumulative result (if any) to accumulate metrics.
  5. Writes the daily result JSON back to S3.

Usage (inside a KubernetesPodOperator pod):
    python process_field.py --date 2024-04-15 \
                            --field-id field_1 \
                            --config /opt/airflow/dags/config/pipeline_config.yaml
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

import boto3
import numpy as np
import rasterio
from botocore.exceptions import ClientError
from rasterio.io import MemoryFile
from rasterio.windows import from_bounds as window_from_bounds
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


def field_by_id(cfg: dict, field_id: str) -> dict:
    for f in cfg["fields"]:
        if f["id"] == field_id:
            return f
    raise ValueError(f"Field '{field_id}' not found in config")


# ── NDVI ─────────────────────────────────────────────────────────────────────

def compute_ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    denom = (nir + red).astype(np.float64)
    return np.where(denom > 0, (nir - red) / denom, 0.0).astype(np.float32)


# ── S3 I/O ───────────────────────────────────────────────────────────────────

def read_raster(client, bucket: str, key: str):
    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return MemoryFile(body)


def read_json(client, bucket: str, key: str) -> dict | None:
    try:
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except ClientError:
        return None


def write_json(client, bucket: str, key: str, obj: dict):
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(obj, indent=2),
        ContentType="application/json",
    )


# ── Processing ───────────────────────────────────────────────────────────────

def process(client, bucket: str, date_str: str, field: dict) -> dict:
    field_id = field["id"]
    exec_date = datetime.strptime(date_str, "%Y-%m-%d")
    planting = datetime.strptime(field["planting_date"], "%Y-%m-%d")
    days = (exec_date - planting).days

    print(f"    Field       : {field_id} – {field['name']}")
    print(f"    Crop        : {field['crop']}")
    print(f"    Planting    : {field['planting_date']}")
    print(f"    Days growing: {days}")

    # ── Read & clip raster ───────────────────────────────────────────────
    raster_key = f"raw/{date_str}/sentinel2_bands.tif"
    print(f"    Reading raster s3://{bucket}/{raster_key}")

    fc = field["geometry"]["coordinates"][0]
    flons = [c[0] for c in fc]
    flats = [c[1] for c in fc]

    memfile = read_raster(client, bucket, raster_key)
    with memfile.open() as src:
        win = window_from_bounds(
            min(flons), min(flats), max(flons), max(flats), src.transform,
        )
        red = src.read(1, window=win)
        nir = src.read(2, window=win)

    print(f"    Clipped window: {red.shape}")

    # ── NDVI statistics ──────────────────────────────────────────────────
    ndvi = compute_ndvi(red, nir)
    mean_ndvi = float(np.mean(ndvi))
    min_ndvi = float(np.min(ndvi))
    max_ndvi = float(np.max(ndvi))
    std_ndvi = float(np.std(ndvi))
    print(f"    NDVI  mean={mean_ndvi:.4f}  min={min_ndvi:.4f}  "
          f"max={max_ndvi:.4f}  std={std_ndvi:.4f}")

    # ── Accumulate from yesterday ────────────────────────────────────────
    yesterday = (exec_date - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_key = f"processed/{field_id}/{yesterday}/result.json"
    prev = read_json(client, bucket, prev_key)

    if prev is not None:
        cum_ndvi = prev["cumulative_ndvi"] + mean_ndvi
        cum_days = prev["cumulative_days_processed"] + 1
        print(f"    Previous cumulative NDVI: {prev['cumulative_ndvi']:.4f}")
    else:
        cum_ndvi = mean_ndvi
        cum_days = 1
        print("    No previous output – starting fresh accumulation.")

    return {
        "field_id": field_id,
        "field_name": field["name"],
        "crop": field["crop"],
        "date": date_str,
        "planting_date": field["planting_date"],
        "days_since_planting": days,
        "mean_ndvi": round(mean_ndvi, 6),
        "min_ndvi": round(min_ndvi, 6),
        "max_ndvi": round(max_ndvi, 6),
        "std_ndvi": round(std_ndvi, 6),
        "cumulative_ndvi": round(cum_ndvi, 6),
        "cumulative_days_processed": cum_days,
        "average_ndvi_over_season": round(cum_ndvi / cum_days, 6),
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Process one field's satellite data")
    ap.add_argument("--date", required=True, help="Execution date YYYY-MM-DD")
    ap.add_argument("--field-id", required=True, help="Field identifier")
    ap.add_argument("--config", required=True, help="Pipeline config YAML path")
    args = ap.parse_args()

    print(f"=== Field Processing ===")
    print(f"    Date    : {args.date}")
    print(f"    Field ID: {args.field_id}")

    cfg = load_config(args.config)
    field = field_by_id(cfg, args.field_id)
    client = s3_client(cfg)
    bucket = cfg["s3"]["bucket"]

    # Skip if before planting
    exec_date = datetime.strptime(args.date, "%Y-%m-%d")
    planting = datetime.strptime(field["planting_date"], "%Y-%m-%d")
    if exec_date < planting:
        print(f"    Date {args.date} < planting {field['planting_date']} → skip.")
        return

    # Idempotency
    out_key = f"processed/{args.field_id}/{args.date}/result.json"
    try:
        client.head_object(Bucket=bucket, Key=out_key)
        print(f"    ↳ Already exists at s3://{bucket}/{out_key} – skipping.")
        return
    except ClientError:
        pass

    result = process(client, bucket, args.date, field)

    print(f"    Uploading → s3://{bucket}/{out_key}")
    write_json(client, bucket, out_key, result)

    print(f"    Result:\n{json.dumps(result, indent=4)}")
    print("    Done ✓")


if __name__ == "__main__":
    main()

