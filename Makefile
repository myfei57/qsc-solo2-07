test:
	PYTHONPATH=. python -m pytest -q

run:
	PYTHONPATH=. python -m fgd --config config/fgd.yaml serve --host 127.0.0.1 --port 8080

demo:
	PYTHONPATH=. python -m fgd --config config/fgd.yaml demo --fast --cycles 12

deadcode:
	PYTHONPATH=. python tools/deadcode.py fgd
