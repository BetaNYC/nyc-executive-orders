#!/usr/bin/env bash
# Watches the odwyer-impellitteri OCR run for its final page's output JSON,
# then kills the run_volume_ocr.py driver (pid 3549).
set -euo pipefail

PIDS=(3549 3550)
FINAL_PAGE_JSON="vlm-ocr-runs/1946-01-07_1950-10-04_ODwyer-Impellitteri_Memoranda/json/page_0204.json"
POLL_INTERVAL=30

echo "watching for ${FINAL_PAGE_JSON} (will kill pids ${PIDS[*]} once it appears)"

any_pid_alive() {
    for pid in "${PIDS[@]}"; do
        kill -0 "${pid}" 2>/dev/null && return 0
    done
    return 1
}

while [[ ! -f "${FINAL_PAGE_JSON}" ]]; do
    if ! any_pid_alive; then
        echo "none of pids ${PIDS[*]} are running and ${FINAL_PAGE_JSON} was never produced -- exiting without killing anything"
        exit 1
    fi
    sleep "${POLL_INTERVAL}"
done

echo "found ${FINAL_PAGE_JSON}"

for pid in "${PIDS[@]}"; do
    if kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}"
        echo "killed pid ${pid}"
    else
        echo "pid ${pid} already gone"
    fi
done
