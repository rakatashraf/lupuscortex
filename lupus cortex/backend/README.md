# Lupus Cortex Model API

Self-contained FastAPI service serving the trained per-component Random Forest models.

## Quick Start

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Download the model file:
   - From Google Drive: `Lupus_Cortex_Data/07_Model_Artifacts/lupus_rf_per_component.pkl`
   - Place it at: `../../models/lupus_rf_per_component.pkl` (relative to this file)
   - Or set `LUPUS_BASE` env var to the directory containing `models/`

3. Run the API:
   ```bash
   python model_api.py
   ```
   The API starts at `http://127.0.0.1:8100`

4. Open `../analysis.html` in your browser and set the API URL to `http://127.0.0.1:8100`

## Endpoints

- `GET /health` — health check, lists available components
- `POST /predict` — predict one component value
  ```json
  {"latitude": 23.8, "longitude": 90.4, "date": "2025-11-15", "component": "1", "recent_values": [45.2, 48.1]}
  ```
- `POST /api/analyze` — full air-quality analysis report
  ```json
  {"lat": 23.8, "lon": 90.4, "name": "Dhaka", "date": "2025-11-15"}
  ```

## Model Info

- 24 per-component Random Forest models (200 trees each)
- Leakage-free training: shifted lags, chronological splits, train-only scaling
- Training data: 84,431 rows, central Bangladesh, 2025
- See `model_index.json` in Drive for per-component test metrics
