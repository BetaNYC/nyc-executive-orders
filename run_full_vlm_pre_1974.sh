#!/usr/bin/env bash
# Phase E end-to-end on an NVIDIA GPU: OCR the pre-1974 bound volumes with
# dots.ocr at 4-bit, then build the corpus from the resulting page JSON.
#
#   stage 0  scripts/run_volume_ocr.py --classify-blank-only   (opt-in, --classify)
#   stage 1  scripts/run_volume_ocr.py --device cuda --quantization 4bit
#   stage 2  scripts/run_pre1974_build.py
#
# 4bit is the right setting on a small card and not just a fallback: measured
# ~3x faster than the 8bit CLI default for equivalent text, at ~4.3 GB peak.
# Stage 1 is the slow one -- budget ~2 hours per volume,
# ~45-50 hours for all 14 -- so default to one volume at a time via --volume.
#
#   ./run_full_vlm_pre_1974.sh --volume Wagner_Orders
#   ./run_full_vlm_pre_1974.sh --volume 1962-01-23 --start-page 88   # resume
#   ./run_full_vlm_pre_1974.sh --clean --volume 1968-01-10           # redo one
#   ./run_full_vlm_pre_1974.sh --dry-run                             # print cmds
#
# Stage 2 ALWAYS builds every volume, even under --volume. corpus/eo_pre1974.json,
# manifest_pre1974.csv and pre1974_provenance.json are each written whole from the
# volumes that stage sees, so building a subset would truncate all three to that
# subset's records; ids are minted against a build-wide collision counter too
# (build_pre1974._mint_ids), so a subset can mint an id the full build suffixes.
# It reads committed JSON and takes seconds, so building all 14 costs nothing.
#
# Stage 0 is off by default. Run it (--classify) on a volume you have not OCR'd
# before: the blank/bleed-through thresholds were calibrated on one volume's
# scans, and a false skip silently drops a real order. It is minutes, no model.
#
# Resuming: every stage checks for its own output first and is safe to re-run.
# A volume whose pages are all OCR'd is skipped outright; a volume that was
# killed part-way resumes at its first missing page (page numbering is absolute,
# so the records directory extends rather than restarting); stage 0 is
# skipped where classify_report.json is already there; stage 2 is skipped
# when no volume was OCR'd this run and the corpus outputs already exist. So the
# way to grind through all 14 is to just keep re-running the script. --force
# redoes everything anyway; an explicit --start-page overrides the resume point.
#
# Cleaning: --clean --volume X deletes everything a previous run left for X and
# exits, so the next run treats it as fresh. That is the only way to redo a
# volume whose OCR is wrong rather than merely incomplete -- the 1968 and 1970
# Lindsay Orders volumes, OCR'd from sideways renders before --rotate existed,
# are exactly that case. It removes, for each matched volume:
#
#   sources/gpp/volumes/ocr/<stem>/   the page records + classify_report -- the
#                                     one that matters: this is what the planner
#                                     reads, so removing it is what makes the
#                                     volume fresh again
#   vlm-ocr-runs/<stem>/              rendered pages, overlays, memory profile
#   vlm-pics/<stem>/                  Picture crops, and their manifest entries
#   vlm-logs/<stem>.log               the run transcript
#   corpus/<year>/<eo_id>.md          this volume's records, per the provenance
#                                     sidecar -- which is why it reads that file
#                                     BEFORE stage 2 overwrites it
#
# Nothing here touches git -- the deletions show up as unstaged, review them.
#
# Each volume gets its own appended logfile under vlm-logs/ (gitignored) --
# vlm-logs/<volume-stem>.log for stages 0+1, vlm-logs/pre1974_build.log for
# stage 2 -- so a 14-volume run is greppable per volume instead of one 50-hour
# scroll. The logs get every line the stages print; the terminal gets a digest
# of it (--progress-every N, default every 10 pages) so a 50-hour run reads as
# a progress meter rather than a firehose:
#
#   [Wagner_Orders] page 100 (10/126, 8%) 117s/page avg, ~3h46m left
#
# Warnings, tracebacks and OOMs are never filtered out -- they go to the
# terminal as they happen, on top of the periodic lines. A volume that fails
# does not stop the rest: its failure is announced immediately with the tail of
# its log, re-reported at the end, and the script exits nonzero.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${REPO_ROOT}/vlm-logs"
QUANTIZATION=4bit
PROGRESS_EVERY=10
VOLUME=""
START_PAGE=""
PAGES=""
ATTN_IMPL=""
CLASSIFY=0
SKIP_OCR=0
SKIP_BUILD=0
DRY_RUN=0
FORCE=0
CLEAN=0

usage() {
    # The header comment above, up to the first line of code, is the manual.
    awk 'NR > 1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
    cat <<'EOF'

Flags:
  --volume SUBSTR         Only volumes whose filename contains SUBSTR (default: all).
  --start-page N          Resume an interrupted volume at absolute page N.
  --pages N               Cap pages OCR'd per volume (sampling).
  --quantization Q        none|8bit|4bit (default: 4bit).
  --attn-implementation X e.g. sdpa, flash_attention_2 (default: let vlm_ocr pick).
  --progress-every N      Terminal status line every N pages (default: 10; 1 = every page).
  --classify              Also run stage 0, the blank-page calibration pass.
  --clean                 Delete one volume's outputs so the next run redoes it
                          from page 1, then exit. Needs --volume (see "Cleaning").
  --force                 Redo work that already has output (see "Resuming").
  --skip-ocr              Skip stages 0+1; rebuild the corpus from committed JSON.
  --skip-build            Stop after OCR; do not touch corpus/.
  --dry-run               Print what each stage would run, change nothing.
  -h, --help              This.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --volume)               VOLUME="$2"; shift 2 ;;
        --start-page)           START_PAGE="$2"; shift 2 ;;
        --pages)                PAGES="$2"; shift 2 ;;
        --quantization)         QUANTIZATION="$2"; shift 2 ;;
        --attn-implementation)  ATTN_IMPL="$2"; shift 2 ;;
        --progress-every)       PROGRESS_EVERY="$2"; shift 2 ;;
        --classify)             CLASSIFY=1; shift ;;
        --clean)                CLEAN=1; shift ;;
        --force)                FORCE=1; shift ;;
        --skip-ocr)             SKIP_OCR=1; shift ;;
        --skip-build)           SKIP_BUILD=1; shift ;;
        --dry-run)              DRY_RUN=1; shift ;;
        -h|--help)              usage; exit 0 ;;
        *)                      echo "error: unknown argument $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "${PROGRESS_EVERY}" =~ ^[1-9][0-9]*$ ]] \
    || { echo "error: --progress-every wants a positive integer, got '${PROGRESS_EVERY}'" >&2; exit 2; }

# The stages are python talking into a pipe, which block-buffers by default --
# 8 KB of page lines would arrive at once and every status line below would be
# stale by half an hour. run_volume_ocr.py hands its environment to the vlm_ocr
# subprocess, so setting this once here reaches the process doing the work.
export PYTHONUNBUFFERED=1

# Page-to-page the allocation sizes vary (KV cache and activations both scale
# with the page's vision-token count), so the caching allocator accumulates
# blocks of the wrong size and strands them: the OOMs this replaced were all
# reported with ~900 MB "reserved by PyTorch but unallocated" on a 7.66 GiB
# card. expandable_segments lets a segment grow in place instead, which hands
# most of that back. Allocator-only -- it cannot change what the model emits.
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# Prefer the repo venv so this works from cron / a bare shell, where the
# vlm-cuda extra is installed but nothing has been activated.
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
else
    PYTHON="$(command -v python3 || command -v python || true)"
fi
[[ -n "${PYTHON}" ]] || { echo "error: no python interpreter found" >&2; exit 1; }

# Resolve volume stems through the repo's own manifest loader, so this loop and
# run_volume_ocr.py always agree on what --volume selects. The stem is a unique
# substring of the filename, so it round-trips back through --volume cleanly.
plan_volumes() {
    "${PYTHON}" - "${REPO_ROOT}" "${VOLUME}" <<'PY'
"""Emit one TSV plan row per matching volume: what is already on disk, and so
what stage 1 still has to do.

    stem <TAB> status <TAB> resume_page <TAB> have <TAB> total <TAB> classified

Every page gets a page_NNNN.json -- blank/bleed-through ones too, carrying
`"skipped": "blank_or_bleedthrough"` -- so a finished volume is a dense
page_0001..page_NNNN, and "first page number missing" is an exact resume point
rather than a guess. One directory holds those records, written per page as the
run goes: sources/gpp/volumes/ocr/<stem>/ (vlm_ocr --json-dir). So what is on
disk IS the progress -- there is no second copy to reconcile it against, and no
publish step that can lag behind it.
"""
import contextlib
import re
import sys
from pathlib import Path

repo, needle = Path(sys.argv[1]), sys.argv[2].lower()
sys.path.insert(0, str(repo / "src"))

# The import chain prints a pymupdf deprecation warning on *stdout*, which would
# otherwise land in the caller's plan. Keep stdout to the TSV alone.
with contextlib.redirect_stdout(sys.stderr):
    from nyc_executive_orders.build_pre1974 import load_volumes

    try:
        import pymupdf
    except ImportError:  # older PyMuPDF only exposes the fitz name
        import fitz as pymupdf

    volumes = load_volumes(repo / "sources" / "gpp" / "volumes.json")

PAGE_RE = re.compile(r"page_(\d+)\.json$")


def page_numbers(directory: Path) -> set[int]:
    if not directory.is_dir():
        return set()
    return {int(m.group(1)) for p in directory.glob("page_*.json")
            if (m := PAGE_RE.search(p.name))}


for volume in volumes:
    if needle not in volume.filename.lower():
        continue

    pdf = repo / volume.pdf_relpath
    if not pdf.exists():
        print(f"{volume.stem}\tnopdf\t1\t0\t0\tno")
        continue
    with contextlib.redirect_stdout(sys.stderr), pymupdf.open(pdf) as doc:
        total = doc.page_count

    ocr_dir = repo / "sources" / "gpp" / "volumes" / "ocr" / volume.stem
    have = page_numbers(ocr_dir)
    classified = "yes" if (ocr_dir / "classify_report.json").is_file() else "no"
    missing = [p for p in range(1, total + 1) if p not in have]

    if not missing:
        status, resume = "complete", total + 1
    else:
        status = "resume" if min(missing) > 1 else "fresh"
        resume = min(missing)
        # Gaps above the resume point get re-OCR'd on the way past them: there
        # is no --end-page, so a resume always runs to the end of the volume.
        gaps = [p for p in missing if p > min(missing)]
        if gaps and len(gaps) != total - min(missing):
            print(f"note: {volume.stem} has {len(have)} page(s) on disk with holes above "
                  f"page {min(missing)}; resuming there re-OCRs the pages after it.",
                  file=sys.stderr)

    print(f"{volume.stem}\t{status}\t{resume}\t{len(have & set(range(1, total + 1)))}"
          f"\t{total}\t{classified}")
PY
}

# --clean, for one volume. See "Cleaning" in the header for what goes and why.
# Volume stems are resolved through plan_volumes, so --clean and a real run
# always agree on what --volume selected.
clean_volume() {
    local stem="$1"
    [[ -n "${stem}" ]] || return 0          # never let an empty stem reach rm -rf
    echo
    echo "=== clean: ${stem} ==="
    local target
    for target in "sources/gpp/volumes/ocr/${stem}" "vlm-ocr-runs/${stem}" \
                  "vlm-pics/${stem}" "vlm-logs/${stem}.log"; do
        local path="${REPO_ROOT}/${target}"
        if [[ ! -e "${path}" ]]; then
            echo "  (absent)     ${target}"
        elif (( DRY_RUN )); then
            echo "  would remove ${target}"
        else
            rm -rf -- "${path}"
            echo "  removed      ${target}"
        fi
    done

    # The corpus records this volume produced, plus its crop manifest entries.
    # Both are keyed by data inside a JSON file rather than by path, so they need
    # more than rm -rf. corpus/<year>/ is shared -- several volumes overlap in
    # year -- so this deletes by eo_id from the provenance sidecar, never a whole
    # year directory. Stage 2 rewrites the sidecar itself, so this has to happen
    # before the next build, which is exactly where --clean sits.
    "${PYTHON}" - "${REPO_ROOT}" "${stem}" "${DRY_RUN}" <<'PY'
import json
import sys
from pathlib import Path

repo, stem, dry_run = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1"
verb = "would remove" if dry_run else "removed"

sidecar = repo / "corpus" / "pre1974_provenance.json"
if sidecar.is_file():
    prov = json.loads(sidecar.read_text(encoding="utf-8"))
    paths = [repo / "corpus" / eo_id[:4] / f"{eo_id}.md"
             for eo_id, entry in sorted(prov.items())
             if entry.get("volume", {}).get("filename", "").removesuffix(".pdf") == stem]
    gone = 0
    for path in paths:
        if not path.exists():
            continue
        if not dry_run:
            path.unlink()
        gone += 1
    print(f"  {verb:12s} {gone} corpus record(s) "
          f"({len(paths) - gone} already absent) via {sidecar.name}")
else:
    print(f"  (absent)     {sidecar.relative_to(repo)} -- no corpus records to remove")

# Merge-updated per volume by run_picture_clips.py, so stale entries would
# otherwise point at crops this clean just deleted until that script re-runs.
manifest = repo / "vlm-pics" / "manifest.json"
if manifest.is_file():
    data = json.loads(manifest.read_text(encoding="utf-8"))
    kept = [p for p in data.get("pictures", []) if p.get("volume") != stem]
    dropped = len(data.get("pictures", [])) - len(kept)
    if dropped and not dry_run:
        data["pictures"] = kept
        manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    print(f"  {verb:12s} {dropped} picture manifest entr(ies)")
PY
}

# Boils a stage's output down to a terminal status line every N pages. Reads the
# stage transcript on stdin (the log already has all of it, via tee) and passes
# through only: the [1/3]-[3/3] phase banners, every PROGRESS_EVERY'th page, and
# anything that smells like trouble. The page line vlm_ocr prints is
#
#     "      page 100 (10/126): 37 elements, 117.2s"
#
# -- absolute page, index within this run, and the per-page wall time, which is
# all the ETA needs. Pages are ~2 minutes each, so a plain per-page average is a
# good enough predictor; no smoothing.
digest() {
    awk -v tag="$1" -v every="${PROGRESS_EVERY}" '
        function say(msg) { printf("  [%s] %s %s\n", strftime("%H:%M:%S"), tag, msg); fflush() }
        function eta(remaining,   secs, h, m) {
            if (timed == 0 || remaining <= 0) return ""
            secs = (elapsed / timed) * remaining
            h = int(secs / 3600); m = int((secs % 3600) / 60)
            return sprintf(", ~%s left", h > 0 ? sprintf("%dh%02dm", h, m) : sprintf("%dm", m))
        }
        # Per-page progress: "page <abs> (<n>/<total>): <k> elements, <t>s [skipped]"
        $1 == "page" && $3 ~ /^\([0-9]+\/[0-9]+\):$/ {
            split(substr($3, 2, length($3) - 3), part, "/")
            n = part[1] + 0; total = part[2] + 0
            t = $6; sub(/s$/, "", t)
            if (t + 0 > 0) { elapsed += t + 0; timed++ }
            if (n == 1 || n == total || n % every == 0)
                say(sprintf("%s (%d/%d, %d%%)%s%s", $1 " " $2, n, total, (n * 100) / total,
                            timed ? sprintf(", %ds/page avg", int(elapsed / timed)) : "",
                            eta(total - n)))
            next
        }
        # Same idea for the classify pass: "  page 0004: SKIP (blank/...) dark_fraction=..."
        # The SKIP/KEEP verdict is what tells these apart from a per-page WARNING
        # line, which starts identically and must NOT be filtered out.
        $1 == "page" && $2 ~ /^[0-9]+:$/ && $3 ~ /^(SKIP|KEEP)$/ {
            c++
            blank += ($3 == "SKIP")
            if (c % every == 0) say(sprintf("classified %d page(s) (%d blank), at page %s", c, blank, substr($2, 1, length($2) - 1)))
            next
        }
        /^\[[0-9]\/3\]|^\[classify-blank-only\]/ { say($0); next }
        /^ +model loaded in|^ +[0-9]+ page\(s\) rendered|^done\.|page\(s\) would be skipped|^ +[0-9]+ page record\(s\) in / {
            sub(/^ +/, ""); say($0); next
        }
        # Never swallowed: these are the reason to be watching the terminal.
        /WARNING|Traceback|^Error|error:|Exception|out of memory|OutOfMemory|FAILED|Killed/ {
            sub(/^ +/, ""); say($0)
        }
    '
}

# Runs one stage, echoing the command and appending a transcript to its log.
# view=digest filters the terminal copy (above); view=full mirrors the stage
# verbatim, for the short stages whose output IS the result.
# pipefail (set above) keeps the python exit status, not tee's or awk's.
run_stage() {
    local view="$1" tag="$2" label="$3" log="$4"; shift 4
    echo
    echo "=== ${label} ==="
    echo "  $*"
    if (( DRY_RUN )); then
        echo "  log -> ${log}"
        return 0
    fi
    {
        echo
        echo "=== ${label} :: $(date -Is) ==="
        echo "  $*"
    } >>"${log}"
    if [[ "${view}" == digest ]]; then
        echo "  (full output -> ${log}; terminal shows a line every ${PROGRESS_EVERY} page(s))"
        "$@" 2>&1 | tee -a "${log}" | digest "${tag}"
    else
        "$@" 2>&1 | tee -a "${log}"
    fi
}

# A stage died. Say so loudly and show the tail of its log, since the terminal
# no longer carries the output that explains why.
report_failure() {
    local what="$1" log="$2"
    echo >&2
    echo "!!! FAILED ${what} -- see ${log}" >&2
    if [[ -f "${log}" ]]; then
        echo "--- last 15 lines of ${log} ---" >&2
        tail -n 15 "${log}" >&2
        echo "--- end ---" >&2
    fi
}

if (( CLEAN )); then
    # Required, not defaulted to all 14: --clean is the one destructive thing
    # here, and a bare --clean would silently throw away ~50 GPU-hours.
    [[ -n "${VOLUME}" ]] \
        || { echo "error: --clean needs --volume (it deletes a volume's outputs)" >&2; exit 2; }

    mapfile -t plan < <(plan_volumes)
    (( ${#plan[@]} )) \
        || { echo "error: no volume in sources/gpp/volumes.json matches '${VOLUME}'" >&2; exit 2; }

    echo "cleaning ${#plan[@]} volume(s) matching '${VOLUME}'$( (( DRY_RUN )) && echo ' (dry run)' )"
    for row in "${plan[@]}"; do
        IFS=$'\t' read -r stem _ <<<"${row}"
        clean_volume "${stem}"
    done
    echo
    echo "done. next run will OCR $( (( ${#plan[@]} > 1 )) && echo 'these volumes' || echo 'this volume' ) from page 1."
    exit 0
fi

# The GPU has to be otherwise empty at 8 GB -- a desktop session plus one stray
# python process was enough to OOM a run. Show what is resident before we start.
if (( ! SKIP_OCR )) && command -v nvidia-smi >/dev/null 2>&1; then
    echo "GPU state before the run:"
    nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader || true
fi

(( DRY_RUN )) || mkdir -p "${LOG_DIR}"

failed=()
ocrd_something=0

if (( SKIP_OCR )); then
    echo "skipping stages 0+1 (--skip-ocr): building from committed OCR JSON"
else
    mapfile -t plan < <(plan_volumes)
    if (( ${#plan[@]} == 0 )); then
        echo "error: no volume in sources/gpp/volumes.json matches '${VOLUME}'" >&2
        exit 2
    fi

    # An explicit --start-page is a per-volume instruction; silently applying it
    # to all 14 would restart every one of them mid-book.
    if [[ -n "${START_PAGE}" ]] && (( ${#plan[@]} > 1 )); then
        echo "error: --start-page needs a --volume that matches exactly one volume" \
             "(matched ${#plan[@]})" >&2
        exit 2
    fi

    echo "plan (${#plan[@]} volume(s)):"
    printf '  %-56s %-8s %s\n' "volume" "status" "pages on disk"
    for row in "${plan[@]}"; do
        IFS=$'\t' read -r stem status resume have total classified <<<"${row}"
        printf '  %-56s %-8s %s/%s%s\n' "${stem}" "${status}" "${have}" "${total}" \
            "$( [[ "${status}" == resume ]] && echo " (resume at ${resume})" )"
    done

    vol_n=0
    for row in "${plan[@]}"; do
        IFS=$'\t' read -r stem status resume have total classified <<<"${row}"
        log="${LOG_DIR}/${stem}.log"
        vol_n=$(( vol_n + 1 ))
        echo
        echo "### volume ${vol_n}/${#plan[@]}: ${stem} (${status}, ${have}/${total} pages on disk)" \
             "-- $(date '+%Y-%m-%d %H:%M:%S')"

        if [[ "${status}" == nopdf ]]; then
            echo "SKIP ${stem}: PDF not on disk" >&2
            failed+=("${stem} (pdf missing)")
            continue
        fi
        if [[ "${status}" == complete ]] && (( ! FORCE )) && [[ -z "${PAGES}${START_PAGE}" ]]; then
            echo "SKIP ${stem}: all ${total} page(s) already OCR'd"
            continue
        fi

        ocr_args=(--volume "${stem}" --device cuda --quantization "${QUANTIZATION}")
        [[ -n "${ATTN_IMPL}" ]] && ocr_args+=(--attn-implementation "${ATTN_IMPL}")
        [[ -n "${PAGES}" ]] && ocr_args+=(--pages "${PAGES}")
        if [[ -n "${START_PAGE}" ]]; then
            ocr_args+=(--start-page "${START_PAGE}")          # explicit wins
        elif (( ! FORCE )) && [[ "${status}" == resume ]]; then
            ocr_args+=(--start-page "${resume}")
            echo "${stem}: resuming at page ${resume} (${have}/${total} already on disk)"
        fi

        if (( CLASSIFY )); then
            if [[ "${classified}" == yes ]] && (( ! FORCE )); then
                echo "SKIP stage 0 for ${stem}: classify_report.json already on disk"
            else
                run_stage digest "${stem}" "stage 0: blank-page calibration :: ${stem}" "${log}" \
                    "${PYTHON}" "${REPO_ROOT}/scripts/run_volume_ocr.py" \
                    --volume "${stem}" --classify-blank-only \
                    || { report_failure "stage 0 (classify): ${stem}" "${log}"
                         failed+=("${stem} (classify)"); continue; }
            fi
        fi

        run_stage digest "${stem}" "stage 1: dots.ocr on cuda @ ${QUANTIZATION} :: ${stem}" "${log}" \
            "${PYTHON}" "${REPO_ROOT}/scripts/run_volume_ocr.py" "${ocr_args[@]}" \
            || { report_failure "stage 1 (ocr): ${stem}" "${log}"
                 echo "continuing with the remaining volume(s)" >&2
                 failed+=("${stem} (ocr)"); continue; }
        (( DRY_RUN )) || echo "  [$(date '+%H:%M:%S')] ${stem}: done"
        ocrd_something=1
    done
fi

build_outputs_exist() {
    [[ -f "${REPO_ROOT}/corpus/eo_pre1974.json" && -f "${REPO_ROOT}/pre1974_report.md" ]]
}

if (( SKIP_BUILD )); then
    echo
    echo "skipping stage 2 (--skip-build); OCR JSON is under sources/gpp/volumes/ocr/"
elif (( ! FORCE && ! SKIP_OCR && ! ocrd_something )) && build_outputs_exist; then
    echo
    echo "skipping stage 2: no volume was OCR'd this run and corpus/eo_pre1974.json +"
    echo "pre1974_report.md are already built (--force, or --skip-ocr, to rebuild anyway)"
else
    # Cheap, idempotent, and reads only committed OCR JSON, so it is still worth
    # running when some volume above failed -- it just will not see that volume.
    #
    # Deliberately NOT passed --volume, even when this run OCR'd one volume: see
    # the header. Building a subset rewrites eo_pre1974.json, manifest_pre1974.csv
    # and pre1974_provenance.json with only that subset's records, and mints ids
    # against an empty collision counter. Whole-corpus every time is seconds.
    # full, not digest: stage 2 is seconds long and its stdout is the report.
    run_stage full build "stage 2: build pre-1974 corpus (all volumes)" "${LOG_DIR}/pre1974_build.log" \
        "${PYTHON}" "${REPO_ROOT}/scripts/run_pre1974_build.py" \
        || { report_failure "stage 2 (build)" "${LOG_DIR}/pre1974_build.log"
             failed+=("stage 2 build"); }
fi

echo
if (( DRY_RUN )); then
    echo "dry run: nothing was executed."
    exit 0
fi

echo "logs: ${LOG_DIR}"
echo "OCR progress:"
"${PYTHON}" "${REPO_ROOT}/scripts/run_volume_ocr.py" --status

if (( ${#failed[@]} )); then
    echo
    echo "FAILED (${#failed[@]}):" >&2
    printf '  %s\n' "${failed[@]}" >&2
    exit 1
fi
echo
echo "done."
