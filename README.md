# Lupus Cortex

Lupus Cortex is an urban-intelligence frontend plus an evidence-collection API designed to feed a trained analysis model.

## Runtime flow

1. A user selects a location or requests a scenario analysis.
2. The frontend asks for a NASA Earthdata Login user token for that request.
3. The token is sent to the Lupus Cortex API in the `X-Earthdata-Token` header.
4. The API uses the token only for NASA Earthdata access. It is not written to disk, browser storage, logs, or the model payload.
5. The API automatically gathers free contextual data from OpenStreetMap/Nominatim, Overpass and Open-Meteo.
6. NASA CMR is queried for relevant Earth-observation granules and provenance. If `EARTHDATA_WORKER_URL` is configured, the API also asks that worker for numeric science-variable extraction.
7. The combined evidence package is sent to the configured trained model at `MODEL_API_URL`.
8. The model must return Lupus Cortex report schema `1.0`; the API validates it and sends it back to the frontend.

No browser-side fallback invents environmental values.

## NASA broker coverage

The API performs authenticated CMR discovery for representative products covering MERRA-2 aerosols, MERRA-2 surface meteorology, GPM IMERG precipitation, MODIS LST/NDVI/AOD, SMAP soil moisture, GRACE/GRACE-FO water storage, VIIRS night lights and NASADEM.

CMR discovery supplies granule metadata and provenance. Product-specific numeric extraction is deliberately delegated to `EARTHDATA_WORKER_URL`, because HDF/NetCDF products differ in variable names, QA flags, scale factors, cadence and spatial grids. This prevents the application from treating catalog metadata as physical measurements.

## Free sources collected automatically

- OpenStreetMap Nominatim: place/context metadata
- OpenStreetMap Overpass: roads, public transport, hospitals/clinics, green space and surface-water features near the selected point
- Open-Meteo: current meteorology and air-quality context
- Open-Meteo Elevation: elevation context

External providers may rate-limit requests or become temporarily unavailable. Source errors are recorded in the evidence package instead of being silently replaced.

## Model contract

Set:

```env
MODEL_API_URL=https://your-model-service.example/analyze
MODEL_API_KEY=
```

The backend sends JSON shaped like:

```json
{
  "schema_version": "1.0",
  "collection_id": "...",
  "request": {},
  "evidence": {
    "nasa": {},
    "free_sources": {}
  },
  "scenario": null
}
```

The model must return an object containing at least:

`schema_version`, `analysis_id`, `model_version`, `location`, `time`, `data_quality`, `components`, `indices`, `hwi`, `analysis`, `history`, `forecasts`, `warnings`, `recommendations`, and `provenance`.

Until `MODEL_API_URL` is configured, `/api/analyze` intentionally returns HTTP 503 after evidence collection rather than fabricating an analysis.

## Run locally

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

Open:

`http://localhost:8000`

Useful endpoints:

- `GET /health`
- `GET /api/status`
- `POST /api/collect` for evidence-only testing
- `POST /api/analyze`
- `POST /api/scenario`

The frontend is served by the same FastAPI container, so local deployment does not require CORS configuration.

## Security

Never commit Earthdata user tokens or model API keys. `.env` is ignored. Earthdata tokens are requested by the frontend for each analysis and held only long enough to complete that request.
