.PHONY: build test pytest check check-all execute test-gpu check-gpu shell clean

# Build the CPU Colab test image (always re-clones upstream notebooks).
build:
	docker compose build --build-arg CACHEBUST=$$(date +%s) test

# Run all CPU tests: pytest + notebook import checks (skips GPU notebooks).
test:
	docker compose build --build-arg CACHEBUST=$$(date +%s) test
	docker compose run --rm test

# Run only pytest unit tests.
pytest:
	docker compose build --build-arg CACHEBUST=$$(date +%s) pytest
	docker compose run --rm pytest

# Run notebook syntax and import checks for CPU notebooks only.
check:
	docker compose build --build-arg CACHEBUST=$$(date +%s) check
	docker compose run --rm check

# Run notebook syntax and import checks for ALL notebooks (including GPU).
check-all:
	docker compose build --build-arg CACHEBUST=$$(date +%s) test
	docker compose run --rm test bash -c " \
		python scripts/generate_manifest.py && \
		python scripts/check_notebook.py --all \
	"

# Execute all CPU notebooks end-to-end with solutions injected. Results in ./results/.
execute:
	docker compose build --build-arg CACHEBUST=$$(date +%s) execute
	docker compose run --rm execute

# Run all GPU tests on a GCE T4 instance: pytest + import checks + notebook execution.
# Spins up an ephemeral instance, runs everything, copies results back, then deletes it.
test-gpu:
	./scripts/gce_gpu_test.sh --execute

# Run GPU import checks only on a GCE T4 instance (no pytest, no notebook execution).
check-gpu:
	./scripts/gce_gpu_test.sh --check-only

# Drop into a shell in the CPU image for debugging.
shell:
	docker compose build --build-arg CACHEBUST=$$(date +%s) shell
	docker compose run --rm shell

# Remove results and docker artifacts.
clean:
	rm -rf results/
	docker compose down --rmi local --remove-orphans
