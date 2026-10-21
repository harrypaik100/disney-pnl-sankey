# Disney P&L Sankey — filing-to-site pipeline
#   make            extract → assemble → verify → build → check the built page (no network needed)
#   make xbrl       cross-check the consolidated figures against the SEC's XBRL facts (needs network)
#   make publish    copy the verified build to the repo root (what GitHub Pages serves in "branch" mode)
PY := python3 -I

.PHONY: all extract assemble verify build check xbrl site publish clean

all: site

extract:
	$(PY) tools/extract.py --all

assemble: extract
	$(PY) tools/assemble.py

verify: assemble
	$(PY) tools/verify.py

build: verify
	$(PY) tools/build.py

check: build
	node tools/verify_site.js

site: check

xbrl:
	$(PY) tools/xbrl.py --require

publish: check
	rm -rf pages && cp -r site/pages pages && cp site/index.html index.html && cp site/.nojekyll .nojekyll
	@echo "root index.html and pages/ now match the verified build"

clean:
	rm -rf site extracted data/values.json data/sources.json data/consistency.json data/verify-report.md
