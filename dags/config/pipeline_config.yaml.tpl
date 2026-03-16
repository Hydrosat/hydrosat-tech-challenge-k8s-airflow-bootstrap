# =============================================================================
# Hydrosat Sentinel-2 Pipeline Configuration
# =============================================================================
#
# This file defines the Area of Interest (AOI), the agricultural fields to
# monitor, and the S3-compatible storage settings.  It is read by the Airflow
# DAGs (for orchestration decisions) and by the processing scripts running
# inside KubernetesPodOperator pods.
#
# Location: agricultural area near Ames, Iowa (US Corn Belt).
# =============================================================================

# ── Area of Interest ─────────────────────────────────────────────────────────
# A rectangular bounding box that defines the satellite data acquisition area.
aoi:
  type: Polygon
  coordinates:
    - - [-93.28, 41.96]
      - [-93.22, 41.96]
      - [-93.22, 42.01]
      - [-93.28, 42.01]
      - [-93.28, 41.96]

# ── Agricultural Fields ──────────────────────────────────────────────────────
# Each field has a unique id, a planting date, a crop type, and a GeoJSON-like
# polygon geometry that intersects the AOI.
fields:
  - id: field_1
    name: "North-West Corn Field"
    crop: corn
    planting_date: "2024-03-15"
    geometry:
      type: Polygon
      coordinates:
        - - [-93.28, 41.97]
          - [-93.26, 41.97]
          - [-93.26, 41.99]
          - [-93.28, 41.99]
          - [-93.28, 41.97]

  - id: field_2
    name: "Central Soybean Field"
    crop: soybean
    planting_date: "2024-04-01"
    geometry:
      type: Polygon
      coordinates:
        - - [-93.26, 41.97]
          - [-93.24, 41.97]
          - [-93.24, 42.00]
          - [-93.26, 42.00]
          - [-93.26, 41.97]

  - id: field_3
    name: "Eastern Wheat Field"
    crop: wheat
    planting_date: "2024-04-15"
    geometry:
      type: Polygon
      coordinates:
        - - [-93.24, 41.96]
          - [-93.22, 41.96]
          - [-93.22, 41.99]
          - [-93.24, 41.99]
          - [-93.24, 41.96]

# ── S3 / MinIO Storage ──────────────────────────────────────────────────────
# The port must match the MinIO Service port (MINIO_API_PORT in the Makefile).
s3:
  endpoint_url: "http://minio.airflow.svc.cluster.local:${MINIO_API_PORT}"
  access_key: "minioadmin"
  secret_key: "minioadmin"
  bucket: "hydrosat-pipeline-copilot"

# ── Simulation Settings ─────────────────────────────────────────────────────
# Used when generating synthetic Sentinel-2 raster data.
simulation:
  # Approximate ground resolution in degrees (~10 m at 42°N)
  resolution: 0.0001
  crs: "EPSG:4326"
  # Cap raster dimensions for performance in the local demo
  max_pixels: 200

