#!/usr/bin/env bash
# Run the post-1974 VLM OCR on a rented DigitalOcean GPU droplet.
#
# The box does ONE job: the GPU step. It runs run_post1974_ocr.py and sends the
# page records home. Everything after that is a local step, because it is cheap,
# it needs no GPU, and the report needs the git history that the box (an rsync'd
# file subset, not a clone) does not have.
#
#   [1/7] preflight   doctl auth, local tools, the files the run needs
#   [2/7] provision   create the droplet, wait for it, wait for sshd + nvidia-smi
#   [3/7] upload      rsync the repo subset + the sibling path dependency
#   [4/7] install     uv, the vlm-cuda extra, one weight prefetch
#   [5/7] ocr         run_post1974_ocr.py (the long one)
#   [6/7] download    rsync sources/ocr and the logs back
#   [7/7] teardown    destroy the droplet
#
# Then, at home:
#
#   uv run python scripts/rebuild_index_from_corpus.py
#   uv run python scripts/run_parse.py --ocr-engine vlm
#   uv run python scripts/report_post1974_ocr.py --stage diff --samples 20
#
# WARNING: this script rents a GPU by the hour. It destroys the droplet on every
# exit path, including a failure and a Ctrl-C, unless you pass --keep-box. If the
# destroy itself fails the script prints the manual `doctl compute droplet delete`
# command and exits non-zero. Read that line. An RTX 6000 Ada left running is
# about USD 1.57 every hour.
#
#   ./execute-post-1974-ocr.sh --smoke        # 1 document, cheapest GPU card
#   ./execute-post-1974-ocr.sh --dry-run      # print the plan, rent nothing
#   ./execute-post-1974-ocr.sh                # the full 1,086-document run
#   ./execute-post-1974-ocr.sh --destroy-only # kill an orphan from a past run
#
# The run writes to sources/ocr/ in this checkout and nothing else. corpus/ stays
# untouched until you run run_parse.py at home, so a part-finished run can never
# mark the documents it did not reach `ocr-skipped`.
#
# Resuming. Page records are the switch, and they come home in sources/ocr/. A
# second run uploads them again and run_post1974_ocr.py skips every document it
# already has, so a run that died half way is just re-run.

set -euo pipefail

# --------------------------------------------------------------------------- #
# Defaults                                                                      #
# --------------------------------------------------------------------------- #

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIBLING_DIR="$(cd "${REPO_ROOT}/.." && pwd)/ny-gov-web-archiver"

REMOTE_USER=root
REMOTE_REPO=/root/nyc-executive-orders
REMOTE_SIBLING=/root/ny-gov-web-archiver
REMOTE_HF_HOME=/root/hf-cache

# Card for a real run. RTX 6000 Ada, 48 GB -- the card the instructions estimate
# against. Resolved against the live size list so a slug rename cannot strand us.
SIZE_MATCH_FULL='6000ada'
DROPLET_IMAGE="${DO_GPU_IMAGE:-gpu-h100x1-base}"
# GPU droplets live in a handful of regions only. Order is a preference,
# not a promise: capacity decides, so provision walks the list.
REGION_PREFERENCE=(tor1 nyc2 atl1 mem1 ams3)
REGION_CANDIDATES=()

TAG=post1974-ocr
DROPLET_NAME=""
SIZE=""
REGION=""
SSH_KEY_FILE="${HOME}/.ssh/id_ed25519"

WORKERS=auto
QUANTIZATION=none
PROGRESS_EVERY=25
LIMIT=""
YEARS=()
EO_IDS=()
MAX_HOURS=10

SMOKE=false
DRY_RUN=false
KEEP_BOX=false
DESTROY_ONLY=false
SWEEP=false
REUSE=""
FETCH_RENDERS=true

STATE_DIR="${REPO_ROOT}/.post1974-run"
DROPLET_ID=""
DROPLET_IP=""
SSH_KEY_ID=""
OCR_STARTED=false
DOWNLOAD_DONE=false

# --------------------------------------------------------------------------- #
# Output                                                                        #
# --------------------------------------------------------------------------- #

if [[ -t 1 ]]; then
    B=$'\033[1m'; R=$'\033[0m'; RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'
else
    B=""; R=""; RED=""; GRN=""; YEL=""; DIM=""
fi

log()  { printf '%s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s!! %s%s\n' "$YEL" "$*" "$R" >&2; }
err()  { printf '%sXX %s%s\n' "$RED" "$*" "$R" >&2; }
die()  { err "$*"; exit 1; }

hms() {
    local s=${1:-0}
    printf '%dh%02dm%02ds' $((s / 3600)) $((s % 3600 / 60)) $((s % 60))
}

# --------------------------------------------------------------------------- #
# Progress ledger                                                               #
# --------------------------------------------------------------------------- #
#
# Ten named phases. Each one prints a banner when it starts and its own duration
# when it ends, and appends a row to ${STATE_DIR}/ledger.tsv. The final summary
# reprints the whole ledger, so a run that fails at phase 6 still says what
# phases 1-5 cost.

PHASE_NAMES=(preflight provision upload install ocr download teardown)
PHASE_TOTAL=${#PHASE_NAMES[@]}
PHASE_INDEX=0
PHASE_CURRENT=""
PHASE_START=0
RUN_START=$(date +%s)
LEDGER=""

ledger_row() {  # name status seconds note
    [[ -n "$LEDGER" ]] || return 0
    printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4:-}" >> "$LEDGER"
}

phase_begin() {
    PHASE_CURRENT="$1"
    PHASE_INDEX=$((PHASE_INDEX + 1))
    PHASE_START=$(date +%s)
    printf '\n%s[%d/%d] %s%s %s(total elapsed %s)%s\n' \
        "$B" "$PHASE_INDEX" "$PHASE_TOTAL" "$PHASE_CURRENT" "$R" \
        "$DIM" "$(hms $(($(date +%s) - RUN_START)))" "$R"
}

phase_end() {  # [note]
    local took=$(($(date +%s) - PHASE_START))
    printf '%s    ok%s %s (%s)\n' "$GRN" "$R" "${1:-}" "$(hms "$took")"
    ledger_row "$PHASE_CURRENT" ok "$took" "${1:-}"
    PHASE_CURRENT=""
}

phase_skip() {  # reason
    printf '%s    skipped%s %s\n' "$DIM" "$R" "$1"
    ledger_row "$PHASE_CURRENT" skipped 0 "$1"
    PHASE_CURRENT=""
}

summary() {
    printf '\n%s=== run summary ===%s\n' "$B" "$R"
    if [[ -n "$LEDGER" && -s "$LEDGER" ]]; then
        printf '%-12s %-8s %10s  %s\n' phase status elapsed note
        while IFS=$'\t' read -r name status secs note; do
            printf '%-12s %-8s %10s  %s\n' "$name" "$status" "$(hms "$secs")" "$note"
        done < "$LEDGER"
    fi
    printf 'total %s\n' "$(hms $(($(date +%s) - RUN_START)))"
}

# --------------------------------------------------------------------------- #
# Teardown -- the money guard                                                   #
# --------------------------------------------------------------------------- #

destroy_droplet() {
    [[ -n "$DROPLET_ID" ]] || return 0
    log "    destroying droplet ${DROPLET_NAME} (${DROPLET_ID}) ..."
    if ! doctl compute droplet delete "$DROPLET_ID" --force >/dev/null 2>&1; then
        warn "delete call failed; retrying once"
        sleep 5
        doctl compute droplet delete "$DROPLET_ID" --force >/dev/null 2>&1 || true
    fi
    local i
    for i in $(seq 1 40); do
        if ! doctl compute droplet get "$DROPLET_ID" >/dev/null 2>&1; then
            info "droplet ${DROPLET_ID} is gone"
            rm -f "${STATE_DIR}/droplet.env"
            DROPLET_ID=""
            return 0
        fi
        sleep 5
    done
    err "DROPLET ${DROPLET_ID} (${DROPLET_NAME}, ${DROPLET_IP}) IS STILL BILLING."
    err "Destroy it yourself NOW:  doctl compute droplet delete ${DROPLET_ID} --force"
    return 1
}

# Best-effort grab of whatever the box produced, used when the run dies part way.
# Losing an OCR hour to a failed download is worse than a slow exit.
rescue_outputs() {
    $OCR_STARTED || return 0
    $DOWNLOAD_DONE && return 0
    [[ -n "$DROPLET_IP" ]] || return 0
    warn "run did not finish -- rescuing whatever page records exist before teardown"
    download_outputs || warn "rescue download failed; the records stay on the box only"
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    # A phase that died never reached phase_end, so the ledger had a hole
    # exactly where the failure was. Name it.
    if [[ -n "$PHASE_CURRENT" ]]; then
        ledger_row "$PHASE_CURRENT" FAILED $(($(date +%s) - PHASE_START)) "exit ${rc}"
        PHASE_CURRENT=""
    fi
    rescue_outputs || true
    if [[ -n "$DROPLET_ID" ]]; then
        if $KEEP_BOX; then
            PHASE_INDEX=$((PHASE_TOTAL - 1))
            phase_begin teardown
            warn "--keep-box: droplet ${DROPLET_NAME} (${DROPLET_IP}) IS STILL RUNNING AND BILLING."
            warn "ssh root@${DROPLET_IP}   /   destroy: doctl compute droplet delete ${DROPLET_ID} --force"
            ledger_row teardown kept 0 "${DROPLET_NAME} ${DROPLET_IP}"
            PHASE_CURRENT=""
        else
            PHASE_INDEX=$((PHASE_TOTAL - 1))
            phase_begin teardown
            if destroy_droplet; then
                phase_end "droplet destroyed"
            else
                ledger_row teardown FAILED 0 "MANUAL DELETE REQUIRED: ${DROPLET_ID}"
                rc=$((rc == 0 ? 1 : rc))
            fi
        fi
    fi
    summary
    exit "$rc"
}

# --------------------------------------------------------------------------- #
# Argument parsing                                                              #
# --------------------------------------------------------------------------- #

usage() {
    sed -n '2,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'USAGE'

Options:
  --smoke                 Test the whole pipeline on ONE document, on the
                          cheapest available GPU card. Implies --limit 1,
                          --workers 1, --progress-every 1, a scratch corpus dir
                          and no cutover gate.
  --limit N               Cap the number of selected documents.
  --year YYYY             Restrict to one signing year (repeatable).
  --eo-id ID              Run one document (repeatable).
  --workers N             Model replicas on the card (default 'auto', which
                          sizes from free VRAM at ~10 GB each: 4 on an RTX
                          6000 Ada, 8 on an H100. Smoke forces 1).
  --quantization none|8bit|4bit
                          Default none, per the instructions' big-card advice.
  --progress-every N      Progress line every N documents (default 25).
  --size SLUG             Droplet size. Default: the RTX 6000 Ada slug, resolved
                          from the live size list; smoke picks the cheapest GPU.
  --region SLUG           Droplet region. Default: first preferred region the
                          chosen size is actually sold in.
  --image SLUG            Droplet image (default gpu-h100x1-base, DO's AI/ML
                          ready image; also $DO_GPU_IMAGE).
  --ssh-key PATH          Private key to use (default ~/.ssh/id_ed25519). Its
                          .pub is imported into the account if it is not there.
  --max-hours H           Wall-clock cap on the OCR step (default 10). The box
                          stops the run at the cap; page records already written
                          still come home.
  --no-fetch-renders      Do not bring back the flagged page PNGs.
  --keep-box              Do NOT destroy the droplet. It keeps billing.
  --reuse ID_OR_NAME      Attach to an existing droplet instead of creating one.
  --destroy-only [ID]     Destroy the recorded (or named) droplet and exit.
  --sweep                 With --destroy-only: destroy EVERY droplet tagged
                          post1974-ocr. The orphan cleaner.
  --dry-run               Print the plan and exit. Creates nothing.
  -h, --help              This text.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --smoke)            SMOKE=true; shift ;;
        --limit)            LIMIT="$2"; shift 2 ;;
        --year)             YEARS+=("$2"); shift 2 ;;
        --eo-id)            EO_IDS+=("$2"); shift 2 ;;
        --workers)          WORKERS="$2"; shift 2 ;;
        --quantization)     QUANTIZATION="$2"; shift 2 ;;
        --progress-every)   PROGRESS_EVERY="$2"; shift 2 ;;
        --size)             SIZE="$2"; shift 2 ;;
        --region)           REGION="$2"; shift 2 ;;
        --image)            DROPLET_IMAGE="$2"; shift 2 ;;
        --ssh-key)          SSH_KEY_FILE="$2"; shift 2 ;;
        --max-hours)        MAX_HOURS="$2"; shift 2 ;;
        --no-fetch-renders) FETCH_RENDERS=false; shift ;;
        --keep-box)         KEEP_BOX=true; shift ;;
        --reuse)            REUSE="$2"; shift 2 ;;
        --destroy-only)
            DESTROY_ONLY=true
            if [[ ${2:-} && ${2:-} != --* ]]; then REUSE="$2"; shift; fi
            shift ;;
        --sweep)            SWEEP=true; shift ;;
        --dry-run)          DRY_RUN=true; shift ;;
        -h|--help)          usage; exit 0 ;;
        *)                  die "unknown option: $1 (try --help)" ;;
    esac
done

if $SMOKE; then
    [[ -n "$LIMIT" || ${#EO_IDS[@]} -gt 0 ]] || LIMIT=1
    WORKERS=1
    PROGRESS_EVERY=1
    MAX_HOURS=$(( MAX_HOURS > 2 ? 2 : MAX_HOURS ))
fi

DROPLET_NAME="${DROPLET_NAME:-post1974-ocr-$(date +%Y%m%d-%H%M%S)}"

mkdir -p "$STATE_DIR"
LEDGER="${STATE_DIR}/ledger.tsv"
: > "$LEDGER"

SSH_OPTS=(
    -i "$SSH_KEY_FILE"
    -o StrictHostKeyChecking=accept-new
    -o UserKnownHostsFile="${STATE_DIR}/known_hosts"
    -o ConnectTimeout=15
    -o ServerAliveInterval=20
    -o ServerAliveCountMax=6
    -o LogLevel=ERROR
)
SSH_CMD="ssh ${SSH_OPTS[*]}"

remote_sh() {   # run the script on stdin (or in $1) on the box
    if [[ $# -gt 0 ]]; then
        printf '%s' "$1" | ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${DROPLET_IP}" bash -s
    else
        ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${DROPLET_IP}" bash -s
    fi
}

remote_put() {  # write stdin to remote path $1, make it executable
    ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${DROPLET_IP}" "cat > '$1' && chmod +x '$1'"
}

# --------------------------------------------------------------------------- #
# Phase 1 -- preflight                                                          #
# --------------------------------------------------------------------------- #

resolve_size() {
    local sizes
    sizes=$(doctl compute size list -o json) || die "doctl compute size list failed"

    # NVIDIA only. The OCR path is vlm_ocr's cuda backend (torch + bitsandbytes),
    # so an AMD MI300/MI325/MI350 card would rent fine and then run nothing.
    # No -spot either: a reclaimed instance mid-run is a lost OCR hour.
    #
    # `regions` non-empty is the availability signal that matters. A size can be
    # in the size list, marked available:true, and be sold in NO region on this
    # account -- the RTX 4000/6000 Ada slugs are exactly that here. Creating one
    # returns "Size is not available in this region" in every region there is.
    local nvidia='[.[] | select(.slug|startswith("gpu-"))
                       | select(.available==true)
                       | select((.regions|length)>0)
                       | select(.slug|test("mi[0-9]+x")|not)
                       | select(.slug|test("-spot$")|not)]'

    if [[ -z "$SIZE" ]]; then
        if $SMOKE; then
            SIZE=$(jq -r "${nvidia} | sort_by(.price_hourly) | .[0].slug // empty" <<<"$sizes")
            [[ -n "$SIZE" ]] || die "no NVIDIA GPU size is available on this account"
        else
            SIZE=$(jq -r --arg m "$SIZE_MATCH_FULL" \
                       "${nvidia} | map(select(.slug|test(\$m))) | sort_by(.price_hourly) | .[0].slug // empty" \
                       <<<"$sizes")
            if [[ -z "$SIZE" ]]; then
                # Fall back on VRAM, which is in the slug ("...x1-48gb"). The
                # size list's `memory` field is system RAM, not VRAM, so it is
                # the wrong number to size a model against.
                warn "no '${SIZE_MATCH_FULL}' size available; falling back to the cheapest NVIDIA card with >= 40 GB of VRAM"
                SIZE=$(jq -r "${nvidia}
                              | map(select((.slug|capture(\"x[0-9]+-(?<v>[0-9]+)gb\").v|tonumber) >= 40))
                              | sort_by(.price_hourly) | .[0].slug // empty" <<<"$sizes")
                [[ -n "$SIZE" ]] || die "no suitable NVIDIA GPU size is available on this account"
            fi
        fi
    fi

    SIZE_PRICE=$(jq -r --arg s "$SIZE" '.[] | select(.slug==$s) | .price_hourly // empty' <<<"$sizes")
    SIZE_DESC=$(jq -r --arg s "$SIZE" '.[] | select(.slug==$s) | .description // empty' <<<"$sizes")
    [[ -n "$SIZE_PRICE" ]] || die "size '${SIZE}' is not in this account's size list"

    # The size list's `regions` array is EMPTY for most GPU sizes -- it does not
    # report where a GPU droplet can actually be created. So it is a hint when
    # present, and otherwise we try the known GPU regions in order at create
    # time (see phase_provision) and keep the first that takes the request.
    # Cross-check against the REGION list too: it carries a per-region `sizes`
    # array, which is the same fact from the other side and catches a size whose
    # own regions field lies.
    local advertised
    advertised=$(doctl compute region list -o json \
        | jq -r --arg s "$SIZE" '.[] | select(.available==true) | select(any(.sizes[]?; . == $s)) | .slug')
    [[ -n "$advertised" ]] || advertised=$(jq -r --arg s "$SIZE" '.[] | select(.slug==$s) | .regions[]?' <<<"$sizes")
    if [[ -n "$REGION" ]]; then
        REGION_CANDIDATES=("$REGION")
    elif [[ -n "$advertised" ]]; then
        mapfile -t REGION_CANDIDATES <<<"$advertised"
    else
        REGION_CANDIDATES=("${REGION_PREFERENCE[@]}")
    fi
    REGION="${REGION_CANDIDATES[0]}"
}

resolve_image() {
    local images
    images=$(doctl compute image list --public -o json 2>/dev/null) || images='[]'
    if jq -e --arg s "$DROPLET_IMAGE" 'any(.[]; .slug==$s)' <<<"$images" >/dev/null 2>&1; then
        return 0
    fi
    local guess
    guess=$(jq -r '[.[] | select(.slug != null)
                       | select(.slug|test("^(gpu|ml|ai)-"))] | .[0].slug // empty' <<<"$images")
    if [[ -n "$guess" ]]; then
        warn "image '${DROPLET_IMAGE}' not found; using '${guess}'"
        DROPLET_IMAGE="$guess"
    else
        warn "cannot confirm image '${DROPLET_IMAGE}' from the public image list; trying it anyway"
    fi
}

resolve_ssh_key() {
    [[ -f "${SSH_KEY_FILE}.pub" ]] || die "no public key at ${SSH_KEY_FILE}.pub (pass --ssh-key)"
    local fp
    fp=$(ssh-keygen -E md5 -lf "${SSH_KEY_FILE}.pub" | awk '{print $2}' | sed 's/^MD5://')
    [[ -n "$fp" ]] || die "cannot fingerprint ${SSH_KEY_FILE}.pub"
    if doctl compute ssh-key list -o json | jq -e --arg f "$fp" 'any(.[]; .fingerprint==$f)' >/dev/null; then
        info "ssh key already in the account (${fp})"
    else
        info "importing ${SSH_KEY_FILE}.pub into the account"
        $DRY_RUN || doctl compute ssh-key import "post1974-ocr-$(hostname -s)" \
            --public-key-file "${SSH_KEY_FILE}.pub" >/dev/null
    fi
    SSH_KEY_ID="$fp"
}

phase_preflight() {
    phase_begin preflight
    local t
    for t in doctl jq rsync ssh ssh-keygen; do
        command -v "$t" >/dev/null || die "missing local tool: ${t}"
    done
    doctl account get >/dev/null 2>&1 \
        || die "doctl is not authenticated. Run:  doctl auth init"
    info "doctl account ok"

    [[ -f "${REPO_ROOT}/corpus/eo.json" ]] || die "missing corpus/eo.json -- the worklist"
    [[ -d "${REPO_ROOT}/pdfs" ]]           || die "missing pdfs/ -- there is nothing to OCR"
    [[ -d "$SIBLING_DIR" ]] \
        || die "missing sibling path dependency ${SIBLING_DIR} (pyproject depends on it)"
    info "local inputs ok"

    # A pdfs/ tree of git-lfs pointer files would upload fine and OCR nothing.
    local probe
    probe=$(find "${REPO_ROOT}/pdfs" -name '*.pdf' -print -quit)
    if [[ -n "$probe" ]] && head -c 40 "$probe" | grep -q 'git-lfs'; then
        die "pdfs/ holds git-lfs pointers, not PDFs. Run: git lfs pull"
    fi
    info "pdfs are real files, not lfs pointers"

    local orphans
    orphans=$(list_orphans)
    if [[ -n "$orphans" ]]; then
        err "a droplet tagged ${TAG} is already running and billing:"
        printf '%s\n' "$orphans" | sed 's/^/      /' >&2
        die "destroy it first:  ./execute-post-1974-ocr.sh --destroy-only --sweep"
    fi
    info "no stale ${TAG} droplets"

    resolve_ssh_key
    resolve_size
    resolve_image

    log ""
    info "size    ${SIZE}  (${SIZE_DESC:-?}) \$${SIZE_PRICE}/hour"
    info "region  ${REGION_CANDIDATES[*]} (tried in this order until one has capacity)"
    info "image   ${DROPLET_IMAGE}"
    info "name    ${DROPLET_NAME}"
    info "mode    $($SMOKE && echo 'SMOKE (test, not a full run)' || echo 'FULL RUN')"
    info "ocr     workers=${WORKERS} quantization=${QUANTIZATION} max-hours=${MAX_HOURS}"
    info "scope   $(ocr_scope_text)"
    phase_end
}

ocr_scope_text() {
    local bits=()
    [[ -n "$LIMIT" ]] && bits+=("limit=${LIMIT}")
    [[ ${#YEARS[@]} -gt 0 ]] && bits+=("years=${YEARS[*]}")
    [[ ${#EO_IDS[@]} -gt 0 ]] && bits+=("eo-ids=${EO_IDS[*]}")
    [[ ${#bits[@]} -eq 0 ]] && echo "every scanned post-1974 document (1,086 docs / 1,799 pages)" \
        || echo "${bits[*]}"
}

# --------------------------------------------------------------------------- #
# Phase 2 -- provision                                                          #
# --------------------------------------------------------------------------- #

droplet_ip() {
    doctl compute droplet get "$1" -o json 2>/dev/null \
        | jq -r '.[0].networks.v4[]? | select(.type=="public") | .ip_address' | head -1
}

wait_for_active() {
    local deadline=$(( $(date +%s) + 900 )) status
    while (( $(date +%s) < deadline )); do
        status=$(doctl compute droplet get "$DROPLET_ID" -o json 2>/dev/null | jq -r '.[0].status // empty')
        [[ "$status" == active ]] && return 0
        printf '.'
        sleep 6
    done
    printf '\n'
    return 1
}

# Anything tagged post1974-ocr is this script's own. A hard kill (SIGKILL, a lost
# terminal) skips the trap, so a stale one can outlive the run that made it --
# and keep billing. Preflight refuses to start while one is up.
list_orphans() {
    doctl compute droplet list --tag-name "$TAG" -o json 2>/dev/null \
        | jq -r '.[] | "\(.id)\t\(.name)\t\(.status)\t\(.region.slug)"'
}

sweep_orphans() {
    local id name status region found=0
    while IFS=$'\t' read -r id name status region; do
        [[ -n "$id" ]] || continue
        found=1
        warn "destroying stale ${TAG} droplet ${id} (${name}, ${status}, ${region})"
        doctl compute droplet delete "$id" --force >/dev/null 2>&1 || true
    done < <(list_orphans)
    [[ "$found" == 1 ]] && sleep 10
    return 0
}

wait_for_ssh() {
    local deadline=$(( $(date +%s) + 600 ))
    while (( $(date +%s) < deadline )); do
        if ssh "${SSH_OPTS[@]}" -o ConnectTimeout=8 -o BatchMode=yes \
               "${REMOTE_USER}@${DROPLET_IP}" true 2>/dev/null; then
            return 0
        fi
        printf '.'
        sleep 8
    done
    printf '\n'
    return 1
}

phase_provision() {
    phase_begin provision

    if [[ -n "$REUSE" ]]; then
        local row
        row=$(doctl compute droplet list -o json \
              | jq -r --arg r "$REUSE" '.[] | select((.id|tostring)==$r or .name==$r)
                       | "\(.id)\t\(.name)\t\(.networks.v4[]? | select(.type=="public") | .ip_address)"' \
              | head -1)
        [[ -n "$row" ]] || die "no droplet matches '${REUSE}'"
        IFS=$'\t' read -r DROPLET_ID DROPLET_NAME DROPLET_IP <<<"$row"
        info "reusing droplet ${DROPLET_NAME} (${DROPLET_ID}) at ${DROPLET_IP}"
    else
        local created="" region_try last_err=""
        for region_try in "${REGION_CANDIDATES[@]}"; do
            log "    creating ${DROPLET_NAME} (${SIZE} in ${region_try}) ..."
            set +e
            # NO --wait here, on purpose. --wait blocks for a minute or more with
            # the droplet already created and already billing, and this script
            # cannot record an id it has not been given yet. A kill inside that
            # window leaves an orphan nothing knows about. Create, record, THEN
            # wait -- see wait_for_active below.
            #
            # doctl -o json writes its API error to STDOUT, not stderr, so both
            # streams have to be captured to learn WHY a create was refused.
            created=$(doctl compute droplet create "$DROPLET_NAME" \
                --size "$SIZE" --image "$DROPLET_IMAGE" --region "$region_try" \
                --ssh-keys "$SSH_KEY_ID" --tag-name "$TAG" \
                -o json 2>&1)
            local rc=$?
            set -e
            if [[ $rc -eq 0 ]]; then REGION="$region_try"; break; fi
            printf '%s\n' "$created" > "${STATE_DIR}/create-${region_try}.err"
            last_err=$(jq -r '.errors[0].detail // empty' <<<"$created" 2>/dev/null)
            [[ -n "$last_err" ]] || last_err=$(tr '\n' ' ' <<<"$created")
            warn "${region_try}: ${last_err:-create failed}"
            created=""
        done
        [[ -n "$created" ]] || die "droplet create failed in every region tried (${REGION_CANDIDATES[*]}): ${last_err}"
        DROPLET_ID=$(jq -r '.[0].id' <<<"$created")
        [[ -n "$DROPLET_ID" && "$DROPLET_ID" != null ]] || die "could not read the new droplet id"
        # Record it before anything else can fail. From here on, every abort path
        # -- including a SIGKILL that skips the trap -- can find this droplet,
        # either through cleanup or through `--destroy-only`.
        printf 'DROPLET_ID=%s\nDROPLET_NAME=%s\nDROPLET_IP=\n' \
            "$DROPLET_ID" "$DROPLET_NAME" > "${STATE_DIR}/droplet.env"
        info "droplet ${DROPLET_ID} created in ${REGION} (\$${SIZE_PRICE}/hour, billing from now)"

        printf '    waiting for it to go active '
        wait_for_active || die "droplet ${DROPLET_ID} never went active"
        printf ' active\n'
        DROPLET_IP=$(droplet_ip "$DROPLET_ID")
        [[ -n "$DROPLET_IP" ]] || die "droplet ${DROPLET_ID} has no public IPv4"
        printf 'DROPLET_ID=%s\nDROPLET_NAME=%s\nDROPLET_IP=%s\n' \
            "$DROPLET_ID" "$DROPLET_NAME" "$DROPLET_IP" > "${STATE_DIR}/droplet.env"
        info "public ip ${DROPLET_IP}"
    fi

    printf '    waiting for sshd '
    wait_for_ssh || die "sshd never came up on ${DROPLET_IP}"
    printf ' up\n'

    local gpu
    gpu=$(remote_sh 'nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null || echo NONE')
    [[ "$gpu" != NONE ]] || die "nvidia-smi is not working on the box -- wrong image? (${DROPLET_IMAGE})"
    info "gpu: ${gpu}"
    phase_end "${DROPLET_IP}"
}

# --------------------------------------------------------------------------- #
# Phase 3 -- upload                                                             #
# --------------------------------------------------------------------------- #
#
# Deliberately NOT the whole checkout. vlm-ocr-runs/ is 13 GB of regenerable
# scratch and sources/gpp/ is 1.2 GB of pre-1974 volume PDFs that this run never
# reads. What goes up is the worklist, the PDFs, the code, and any page records
# a previous run already brought home (that is what makes a re-run resume).

phase_upload() {
    phase_begin upload
    # Every destination parent, up front. rsync creates only the LAST component
    # of a destination path, so pushing sources/ocr/ onto a box with no
    # sources/ fails outright rather than creating the tree.
    remote_sh "mkdir -p '${REMOTE_REPO}' '${REMOTE_SIBLING}' '${REMOTE_HF_HOME}' '${REMOTE_REPO}/sources/ocr' '${REMOTE_REPO}/vlm-logs'"

    local rsync_common=(-az --delete --info=stats1 -e "$SSH_CMD")

    info "sibling path dependency -> ${REMOTE_SIBLING}"
    rsync "${rsync_common[@]}" \
        --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' \
        "${SIBLING_DIR}/" "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_SIBLING}/"

    info "code + worklist -> ${REMOTE_REPO}"
    rsync -az --info=stats1 -e "$SSH_CMD" \
        "${REPO_ROOT}/pyproject.toml" "${REPO_ROOT}/uv.lock" "${REPO_ROOT}/README.md" \
        "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_REPO}/"
    rsync "${rsync_common[@]}" --exclude '__pycache__/' \
        "${REPO_ROOT}/src/" "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_REPO}/src/"
    rsync "${rsync_common[@]}" --exclude '__pycache__/' \
        "${REPO_ROOT}/scripts/" "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_REPO}/scripts/"
    rsync "${rsync_common[@]}" \
        "${REPO_ROOT}/corpus/" "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_REPO}/corpus/"

    info "pdfs -> ${REMOTE_REPO}/pdfs ($(du -sh "${REPO_ROOT}/pdfs" | cut -f1))"
    # progress2 draws with carriage returns. That reads well on a terminal and
    # dumps hundreds of KB of one-line spam into a redirected log, so ask for it
    # only when someone is actually watching.
    local pdf_info=--info=stats1
    [[ -t 1 ]] && pdf_info=--info=progress2
    rsync -az "$pdf_info" -e "$SSH_CMD" \
        "${REPO_ROOT}/pdfs/" "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_REPO}/pdfs/"

    if [[ -d "${REPO_ROOT}/sources/ocr" ]]; then
        info "page records from earlier runs -> the box (this is what resumes)"
        rsync -az --info=stats1 -e "$SSH_CMD" \
            "${REPO_ROOT}/sources/ocr/" "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_REPO}/sources/ocr/"
    fi
    phase_end
}

# --------------------------------------------------------------------------- #
# Phase 4 -- install                                                            #
# --------------------------------------------------------------------------- #

phase_install() {
    phase_begin install
    remote_put /root/step-install.sh <<INSTALL
#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export HF_HOME='${REMOTE_HF_HOME}'
export PATH="\$HOME/.local/bin:\$PATH"

if ! command -v uv >/dev/null; then
    echo "--- installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="\$HOME/.local/bin:\$PATH"
uv --version

cd '${REMOTE_REPO}'
echo "--- syncing the vlm-cuda extra (torch, transformers, bitsandbytes)"
uv sync --frozen --extra vlm-cuda

echo "--- torch sees:"
uv run --extra vlm-cuda python -c "import torch; print(torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"

echo "--- prefetching rednote-hilab/dots.ocr into \${HF_HOME}"
uv run --extra vlm-cuda python -c "
from huggingface_hub import snapshot_download
from huggingface_hub.utils import disable_progress_bars
disable_progress_bars()
p = snapshot_download('rednote-hilab/dots.ocr')
print('weights at', p)
"
echo "--- install done"
INSTALL

    # Streams so a long torch download reads as progress, not a hang.
    #
    # The subshell sets pipefail on purpose. A pipeline reports its LAST
    # command's status, and `sed` always succeeds -- so without this a remote
    # install that died would be reported as ok and the run would carry on to
    # OCR with no torch.
    local rc=0
    set +e
    ( set -o pipefail
      ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${DROPLET_IP}" \
          "bash /root/step-install.sh 2>&1" | sed 's/^/    /' )
    rc=$?
    set -e
    [[ $rc -eq 0 ]] || die "install failed on the box (exit ${rc}) -- see the output above"
    phase_end
}

# --------------------------------------------------------------------------- #
# Phases 5-8 -- the pipeline itself                                             #
# --------------------------------------------------------------------------- #
#
# Every remote step runs detached under setsid + nohup and writes to a log on the
# box, and this script tails that log. A dropped ssh connection then costs a
# reconnect, not the run -- which matters when the run is measured in hours.

REMOTE_ENV_PREAMBLE="export PATH=\"\$HOME/.local/bin:\$PATH\"; export HF_HOME='${REMOTE_HF_HOME}'; cd '${REMOTE_REPO}'"

run_remote_step() {  # name, command-string
    local name="$1" cmd="$2"
    local rlog="/root/step-${name}.log" rpid="/root/step-${name}.pid" rrc="/root/step-${name}.rc"
    local llog="${STATE_DIR}/${name}.log"

    remote_put "/root/step-${name}.sh" <<STEP
#!/usr/bin/env bash
# Writes its OWN pid: \$! after setsid can name the wrapper rather than this
# shell, and the tail --pid follower needs the pid that actually lives as long
# as the work does.
echo \$\$ > '${rpid}'
${REMOTE_ENV_PREAMBLE}
rc=0
${cmd} || rc=\$?
echo "\${rc}" > '${rrc}'
exit "\${rc}"
STEP

    remote_sh "rm -f '${rrc}' '${rpid}'; setsid nohup bash '/root/step-${name}.sh' > '${rlog}' 2>&1 < /dev/null & for i in 1 2 3 4 5 6 7 8 9 10; do [ -s '${rpid}' ] && break; sleep 1; done"
    : > "$llog"
    follow_remote_log "$rlog" "$rpid" "$llog"

    local rc
    rc=$(remote_sh "cat '${rrc}' 2>/dev/null || echo 99")
    [[ "$rc" == 0 ]] || return "$rc"
    return 0
}

follow_remote_log() {  # remote-log, remote-pidfile, local-mirror
    local rlog="$1" rpid="$2" llog="$3"
    while :; do
        local offset=$(( $(wc -l < "$llog") + 1 ))
        set +e
        ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${DROPLET_IP}" \
            "tail -n +${offset} -f --pid=\$(cat '${rpid}') '${rlog}' 2>/dev/null" \
            | tee -a "$llog" | sed 's/^/    /'
        set -e
        # The remote process outliving a dropped pipe is the reconnect case.
        if remote_sh "kill -0 \$(cat '${rpid}') 2>/dev/null" >/dev/null 2>&1; then
            warn "log stream dropped; reconnecting (the run on the box is untouched)"
            sleep 5
            continue
        fi
        break
    done
}

phase_ocr() {
    phase_begin ocr
    local args=(--device cuda --quantization "$QUANTIZATION" --workers "$WORKERS"
                --progress-every "$PROGRESS_EVERY")
    [[ -n "$LIMIT" ]] && args+=(--limit "$LIMIT")
    local y; for y in ${YEARS[@]+"${YEARS[@]}"}; do args+=(--year "$y"); done
    local e; for e in ${EO_IDS[@]+"${EO_IDS[@]}"}; do args+=(--eo-id "$e"); done

    info "timeout ${MAX_HOURS}h on: run_post1974_ocr.py ${args[*]}"
    OCR_STARTED=true
    run_remote_step ocr \
        "timeout --signal=INT $(( MAX_HOURS * 3600 ))s uv run --extra vlm-cuda python scripts/run_post1974_ocr.py ${args[*]}" \
        || {
            local rc=$?
            if [[ "$rc" == 124 || "$rc" == 130 ]]; then
                warn "the OCR step hit the ${MAX_HOURS}h cap and stopped. Page records already written are kept; re-run this script to resume."
            else
                die "the OCR step failed (exit ${rc}) -- see ${STATE_DIR}/ocr.log"
            fi
        }

    local done_pages
    done_pages=$(remote_sh "find '${REMOTE_REPO}/sources/ocr' -name 'page_*.json' 2>/dev/null | wc -l")
    phase_end "${done_pages} page record(s) on the box"
}

# --------------------------------------------------------------------------- #
# Phase 6 -- download                                                           #
# --------------------------------------------------------------------------- #

download_outputs() {
    local ok=0
    local pull=(-az --info=stats1 -e "$SSH_CMD")
    # Same rsync rule in reverse: only the last path component gets created.
    mkdir -p "${REPO_ROOT}/sources/ocr" "${REPO_ROOT}/vlm-logs"

    rsync "${pull[@]}" "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_REPO}/sources/ocr/" \
        "${REPO_ROOT}/sources/ocr/" || ok=1
    info "sources/ocr/  ($(find "${REPO_ROOT}/sources/ocr" -name 'page_*.json' 2>/dev/null | wc -l) page records)"

    rsync "${pull[@]}" "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_REPO}/vlm-logs/" \
        "${REPO_ROOT}/vlm-logs/" 2>/dev/null || true
    mkdir -p "${STATE_DIR}/remote-step-logs"
    rsync "${pull[@]}" "${REMOTE_USER}@${DROPLET_IP}:/root/step-ocr.log" \
        "${STATE_DIR}/remote-step-logs/" 2>/dev/null || true

    if $FETCH_RENDERS; then
        rsync "${pull[@]}" "${REMOTE_USER}@${DROPLET_IP}:${REMOTE_REPO}/vlm-ocr-runs/" \
            "${REPO_ROOT}/vlm-ocr-runs/" 2>/dev/null || true
    fi

    [[ "$ok" == 0 ]] && DOWNLOAD_DONE=true
    return "$ok"
}

phase_download() {
    phase_begin download
    download_outputs || die "download failed -- do NOT let this box be destroyed; re-run with --reuse ${DROPLET_ID}"
    phase_end
}

# --------------------------------------------------------------------------- #
# Entry                                                                         #
# --------------------------------------------------------------------------- #

if $DESTROY_ONLY; then
    if $SWEEP; then
        sweep_orphans
        log "remaining ${TAG} droplets:"
        list_orphans | sed 's/^/  /'
        rm -f "${STATE_DIR}/droplet.env"
        exit 0
    fi
    if [[ -n "$REUSE" ]]; then
        DROPLET_ID=$(doctl compute droplet list -o json \
            | jq -r --arg r "$REUSE" '.[] | select((.id|tostring)==$r or .name==$r) | .id' | head -1)
        DROPLET_NAME="$REUSE"
    elif [[ -f "${STATE_DIR}/droplet.env" ]]; then
        # shellcheck disable=SC1091
        source "${STATE_DIR}/droplet.env"
    fi
    [[ -n "$DROPLET_ID" ]] || die "no droplet recorded and none named; pass --destroy-only <id-or-name>"
    destroy_droplet
    exit $?
fi

if $DRY_RUN; then
    phase_preflight
    log ""
    log "${B}dry run: nothing was created.${R} The run would then:"
    log "  upload   ${REPO_ROOT} (code, corpus, pdfs) + ${SIBLING_DIR}"
    log "  install  uv, the vlm-cuda extra, rednote-hilab/dots.ocr weights"
    log "  ocr      run_post1974_ocr.py --device cuda --quantization ${QUANTIZATION} --workers ${WORKERS} $( [[ -n $LIMIT ]] && echo "--limit ${LIMIT}")"
    log "  download sources/ocr, vlm-logs"
    log "  teardown doctl compute droplet delete <id> --force"
    log ""
    log "  then at home: run_parse.py --ocr-engine vlm, then report_post1974_ocr.py"
    exit 0
fi

trap cleanup EXIT INT TERM

phase_preflight
phase_provision
phase_upload
phase_install
phase_ocr
phase_download

log ""
log "${GRN}${B}the GPU step finished.${R} Outputs are in this checkout:"
log "  sources/ocr/              page records (the switch: their presence is the cutover)"
log "  vlm-logs/post1974/        per-worker logs"
log "  ${STATE_DIR}/     this run's step logs and ledger"
if ! $SMOKE; then
    log ""
    log "${B}Now do the rest at home.${R} These need no GPU, and the report needs the"
    log "git history the box does not have:"
    log "  uv run python scripts/rebuild_index_from_corpus.py"
    log "  uv run python scripts/run_parse.py --ocr-engine vlm"
    log "  uv run python scripts/report_post1974_ocr.py --stage diff --samples 20"
    log ""
    log "The last one is the cutover gate. Read post1974_ocr_report.md before you commit."
fi
