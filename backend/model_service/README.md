# Lupus Cortex Model API

Self-contained FastAPI service serving the trained Random Forest model.

## Run locally

```bash
pip install fastapi uvicorn joblib scikit-learn numpy
python backend/model_service/model_api.py
# serves on http://127.0.0.1:8100
```

## Required files (place next to this service or set paths in model_api.py)

- `models/lupus_rf.pkl` — trained Random Forest
- `models/lupus_scaler.pkl` — fitted StandardScaler
- `reference_dataset/lupus_cortex_fixed_reference_dataset.csv` — WHO guidelines

Download them from the Google Drive `Lupus_Cortex_Data` folder:
- 07_Model_Artifacts/lupus_rf.pkl
- 07_Model_Artifacts/lupus_scaler.pkl
- 04_Fixed_Reference_Dataset/lupus_cortex_fixed_reference_dataset.csv

## Endpoints

- `GET /health` — service + model status
- `POST /predict` — single component prediction
- `POST /api/analyze` — full analysis report (HWI, components, warnings)

The hosted frontend (`analysis.html` on GitHub Pages) connects to this API.
Set the API URL in the page, default `http://127.0.0.1:8100`.
