.PHONY: setup test lint olist-download cfpb-download cfpb-sample load synth index rag-eval api dq
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
synth:          ## policies, synthetic disputes, golden set (needs `make load`)
	$(PY) -m disputedesk.synthetic.generate
index:          ## build the Chroma policy index from policies/
	$(PY) -m disputedesk.rag.index
rag-eval:       ## retrieval hit@k / recall@k on the golden set (no LLM)
	$(PY) -m disputedesk.rag.eval
api:            ## run the tool service on :8000 (needs `make load synth index`)
	PYTHONPATH=src .venv/bin/uvicorn --factory disputedesk.tools.app:create_app --port 8000
dq:
	$(PY) -m disputedesk.data.quality
