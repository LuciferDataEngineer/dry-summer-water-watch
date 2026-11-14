PYTHON ?= python3
export PYTHONPATH := src

.PHONY: refresh pipeline test lint quality forecast benchmark

refresh:
	$(PYTHON) -m waterwatch.snapshot --live

pipeline:
	WATERWATCH_SKIP_CHRONOS=$${WATERWATCH_SKIP_CHRONOS:-1} $(PYTHON) -m waterwatch.pipeline

forecast:
	WATERWATCH_SAVE_CHRONOS=1 $(PYTHON) -m waterwatch.pipeline

test:
	$(PYTHON) -m pytest -o addopts= -m "not integration"

lint:
	$(PYTHON) -m ruff check src tests k8s/scale_worker.py k8s/start_load.py

quality:
	$(PYTHON) -m waterwatch.quality

benchmark:
	$(PYTHON) -m waterwatch.risingwave_job
