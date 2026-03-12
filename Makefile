.PHONY: install uninstall test lint run scan status setup

install:
	./install.sh

uninstall:
	./uninstall.sh

test:
	python3 -m pytest tests/ -v

lint:
	python3 -m ruff check blueward/

run:
	blueward --verbose --no-tray

scan:
	blueward scan

status:
	blueward status

setup:
	blueward setup
