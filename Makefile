PYTHON ?= python3
CONFIG ?= config/local.json
HOST ?= pi

.PHONY: help check check-display preview snapshot image build-display release

help:
	@printf '%s\n' \
	  'make check                         Local validation and offline tests' \
	  'make check-display                 ASan/UBSan C probes (requires a C compiler)' \
	  'make preview CONFIG=path.json       Preview future schedule, no hardware access' \
	  'make snapshot HOST=pi              Read-only device backup (ignored by Git)' \
	  'make image                         Build Debian 13 ARM64 compiler image' \
	  'make build-display                 Compile epd into build/display/' \
	  'make release CONFIG=path.json       Prepare local release; does not upload or install'

check:
	$(PYTHON) scripts/check-project.py

check-display:
	$(PYTHON) scripts/check-display.py

preview:
	$(PYTHON) scripts/preview-schedule.py --config "$(CONFIG)"

snapshot:
	$(PYTHON) scripts/snapshot-device.py --host "$(HOST)"

image:
	docker compose build display-build

build-display:
	mkdir -p build/display
	docker compose run --rm --no-deps display-build

release:
	$(PYTHON) scripts/prepare-release.py --config "$(CONFIG)" --epd build/display/epd
