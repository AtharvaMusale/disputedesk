.PHONY: setup test lint olist-download cfpb-download cfpb-sample load dq
setup:        ## create venv and install pinned deps
	python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt
test:
	.venv/bin/pytest
lint:
	.venv/bin/ruff check src tests

PY = PYTHONPATH=src .venv/bin/python
olist-download:  ## needs a Kaggle token in ~/.kaggle/access_token
	.venv/bin/kaggle datasets download olistbr/brazilian-ecommerce -p data/raw/olist --unzip
cfpb-download:  ## download CFPB mirror (~925 MB parquet), filter to card + transfer, 2022+
	$(PY) -m disputedesk.data.download_cfpb
cfpb-sample:    ## draw 10k per group -> data/processed/cfpb_sample.parquet
	$(PY) -m disputedesk.data.sample_cfpb
load:           ## load real data + build derived tables
	$(PY) -m disputedesk.data.load
dq:
	$(PY) -m disputedesk.data.quality
