#!/usr/bin/env python3
"""SUPERVISED GPP INTEGRATION (Phase D) — DO NOT RUN THE MERGE AUTOMATICALLY.

============================================================================
  !!  THE MERGE MUTATES THE COMMITTED CORPUS (corpus/, pdfs/, sources/)  !!
============================================================================

Folds the DORIS Government Publications Portal (GPP) harvest into the corpus:
mints records for the net-new + known-missing orders, attaches PDFs to the 53
no-pdf gap-closer records, parks dual-provenance copies + pre-1974 volumes under
``sources/gpp/``, and writes the provenance sidecar + integration report. The
overlap math is re-derived from the committed inventory + corpus (recon report:
BetaNYC workspace ``team/research/mayoral-executive-orders/2026-07-17-gpp-city-
record-recon.md``).

FULLY LOCAL — no network, no cloud (engineering-standards §7). It reads the staged
PDFs (``gpp-<fileset_id>.pdf``, already downloaded by the browser harvest) and the
committed corpus, then copies files + writes JSON/Markdown. The gate is NOT about
network; it makes the corpus mutation a deliberate, authorized action, matching
the project's runner posture (run_parse / run_deblasio_harvest_live).

Idempotent + resumable: the prior provenance sidecar pins already-integrated
orders, so re-running over the same (or a more-complete) staging dir never
duplicates or corrupts. Run the dry-run now (partial staging OK); run the merge
after the harvest finishes.

Preview (read-only, NO gate) — validate staging + print the plan, write nothing:
    python scripts/run_gpp_integration.py --dry-run

Merge (writes the corpus). Two equally-strong authorization gates:
  * --i-am-a-human-running-this-supervised : a human, interactively.
  * --operator-authorized : an agent, under explicit in-session operator (noel)
    authorization (logs a truthful agent-executed provenance line).
Refuses to run the merge (exit 2) if NEITHER gate flag is present.

    # born-digital only (fast, no OCR deps) — scanned orders get an ocr-skipped stub:
    python scripts/run_gpp_integration.py --no-ocr --operator-authorized
    # full parse incl. LOCAL OCR (needs the [ocr] extra: ocrmypdf + Tesseract):
    python scripts/run_gpp_integration.py --operator-authorized

Exit codes: 0 clean; 1 completed but staging had corrupt files; 2 no auth gate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
from pathlib import Path

# Make the src/ package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import config, gpp  # noqa: E402
from nyc_executive_orders.ocr import OcrConfig  # noqa: E402

logger = logging.getLogger("nyc_executive_orders.run_gpp_integration")

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "corpus"
EO_JSON = CORPUS_DIR / "eo.json"
SOURCES_DIR = REPO_ROOT / "sources" / "gpp"
REPORT_MD = REPO_ROOT / "gpp_integration_report.md"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--inventory", type=Path, default=config.DEFAULT_GPP_INVENTORY,
                   help=f"GPP inventory JSON (default: {config.DEFAULT_GPP_INVENTORY}).")
    p.add_argument("--staging-dir", type=Path, default=config.DEFAULT_GPP_STAGING_DIR,
                   help=f"Harvested PDFs dir (default: {config.DEFAULT_GPP_STAGING_DIR}).")
    p.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    p.add_argument("--sources-dir", type=Path, default=SOURCES_DIR)
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT,
                   help="Repo root under which pdfs/ are placed (default: this repo). "
                        "Override only for isolated tests.")
    p.add_argument("--report-path", type=Path, default=REPORT_MD)
    p.add_argument("--dry-run", action="store_true",
                   help="Validate staging + print the plan; write nothing. No gate.")
    p.add_argument("--no-ocr", action="store_true",
                   help="Born-digital only; scanned PDFs emit an ocr-skipped stub "
                        "(no [ocr] extra needed). Re-run without it later to OCR.")
    p.add_argument("--language", default="eng", help="Tesseract language (OCR path).")
    p.add_argument("--i-am-a-human-running-this-supervised", action="store_true",
                   help="Human authorization for the corpus-mutating merge.")
    p.add_argument("--operator-authorized", action="store_true",
                   help="Agent authorization for the merge (explicit in-session "
                        "operator go-ahead).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    eo_json = args.corpus_dir / "eo.json"
    if not args.inventory.exists():
        raise SystemExit(f"inventory not found at {args.inventory}")
    if not eo_json.exists():
        raise SystemExit(f"corpus not found at {eo_json}")

    inventory = gpp.load_inventory(args.inventory)
    corpus = json.loads(eo_json.read_text(encoding="utf-8"))
    prior_ledger = gpp.provenance_ledger(args.corpus_dir)

    plan = gpp.classify(inventory, corpus, prior_ledger=prior_ledger)
    staging = gpp.validate_staging(gpp.expected_filesets(plan), args.staging_dir)

    counts = plan.counts()
    minted = counts.get(gpp.NET_NEW, 0) + counts.get(gpp.GAP_CLOSER_MINT, 0)
    print(f"\nGPP integration plan (corpus {len(corpus)} → {len(corpus) + minted}):")
    for k in (gpp.NET_NEW, gpp.GAP_CLOSER_MINT, gpp.GAP_CLOSER_EXISTING, gpp.DUAL,
              "volume", "excluded"):
        print(f"  {k}: {counts.get(k, 0)}")
    print(f"Staging: {staging.summary()}")
    if prior_ledger:
        print(f"Prior sidecar: {len(prior_ledger)} orders already integrated (resuming).")

    if args.dry_run:
        print("\nDRY-RUN — nothing written.\n")
        print(gpp.render_report(plan, staging, corpus_before=len(corpus)))
        return 0

    # --- Gate (the merge mutates the committed corpus) ----------------------- #
    human = args.i_am_a_human_running_this_supervised
    operator = args.operator_authorized
    if not (human or operator):
        print(
            "REFUSING TO RUN: the merge mutates the committed corpus "
            "(corpus/, pdfs/, sources/).\n"
            "Re-run with --operator-authorized (agent, explicit in-session operator "
            "authorization) OR --i-am-a-human-running-this-supervised (a human). "
            "To preview WITHOUT writing, pass --dry-run (no gate).",
            file=sys.stderr,
        )
        return 2
    if operator and not human:
        logger.info(
            "GPP merge under explicit operator authorization (operator=noel, "
            "in-session); agent-executed. LOCAL ONLY — no network, no cloud "
            "(engineering-standards §7)."
        )

    do_ocr = not args.no_ocr
    if do_ocr and importlib.util.find_spec("ocrmypdf") is None:
        print(
            "REFUSING TO RUN: OCR path requested but the [ocr] extra is not "
            "installed (ocrmypdf + Tesseract/Ghostscript).\n"
            "Install it (`uv pip install 'nyc-executive-orders[ocr]'`) OR pass "
            "--no-ocr to fold in born-digital text now and OCR the scanned PDFs on "
            "a later re-run (idempotent).",
            file=sys.stderr,
        )
        return 2

    result = gpp.integrate(
        inventory, corpus,
        staging_dir=args.staging_dir, repo_root=args.repo_root,
        corpus_dir=args.corpus_dir, sources_dir=args.sources_dir,
        prior_ledger=prior_ledger, do_ocr=do_ocr,
        ocr_config=OcrConfig(language=args.language), do_write=True,
    )
    prov_path = gpp.write_provenance(result, args.corpus_dir)
    vols_path = gpp.write_volumes_manifest(result, args.sources_dir)
    args.report_path.write_text(
        gpp.render_report(plan, staging, corpus_before=result.corpus_before,
                          result=result),
        encoding="utf-8",
    )

    print("\nWROTE:")
    print(f"  {eo_json} ({result.corpus_before} → {result.corpus_after} records)")
    print(f"  {prov_path} ({len(result.provenance)} orders)")
    print(f"  {vols_path} ({len(result.volumes_manifest)} volumes)")
    print(f"  {args.report_path}")
    print(f"\nminted={result.minted} gap_closed={result.gap_closed} "
          f"dual_files={result.dual_files} volume_files={result.volume_files} "
          f"files_written={result.files_written} deferred={len(result.deferred)}")
    if result.deferred:
        print(f"  DEFERRED (file not staged — re-run after harvest completes): "
              f"{len(result.deferred)}")
    if staging.corrupt:
        print(f"\nCompleted, but {len(staging.corrupt)} staged file(s) were corrupt "
              "— re-download and re-run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
