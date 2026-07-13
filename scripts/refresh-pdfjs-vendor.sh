#!/usr/bin/env bash
# Refresh the directly-vendored pdf.js distribution under
# common/static/js/vendor/pdfjs/ from a Mozilla pdf.js GitHub Release.
#
# Why a script and not `copy-node-modules.sh`: the npm-published `pdfjs-dist`
# package does not ship the complete `web/viewer.html` viewer page, only the
# library + a `PDFViewer` component class. Open edX serves the upstream
# viewer page directly (wrapped in `lms/templates/pdf_viewer.html`), so we
# pull the prebuilt zip from the GitHub Release instead.
#
# This script is intentionally simple and re-runnable. Update PDFJS_VERSION
# and PDFJS_LEGACY_ZIP_SHA256 to bump.

set -euo pipefail

PDFJS_VERSION="5.7.284"
PDFJS_LEGACY_ZIP_SHA256="b1edded128a7e50e7818bfe16564eb4012dd3f13f2847f9f94100c96567afbcc"

ZIP_NAME="pdfjs-${PDFJS_VERSION}-legacy-dist.zip"
ZIP_URL="https://github.com/mozilla/pdf.js/releases/download/v${PDFJS_VERSION}/${ZIP_NAME}"
VENDOR_DIR="common/static/js/vendor/pdfjs"

if [[ ! -d common/static ]]; then
    echo "error: run this script from the openedx-platform repo root" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "Downloading ${ZIP_URL}"
curl --fail --silent --show-error --location -o "${WORK_DIR}/${ZIP_NAME}" "${ZIP_URL}"

echo "Verifying SHA-256"
echo "${PDFJS_LEGACY_ZIP_SHA256}  ${WORK_DIR}/${ZIP_NAME}" | sha256sum --check --status

echo "Extracting"
unzip -q "${WORK_DIR}/${ZIP_NAME}" -d "${WORK_DIR}/extracted"

echo "Wiping ${VENDOR_DIR}"
rm -rf "${VENDOR_DIR}"
mkdir -p "${VENDOR_DIR}"

echo "Copying vendored assets"
cp "${WORK_DIR}/extracted/LICENSE" "${VENDOR_DIR}/LICENSE"

# Library: skip sandbox build (we don't run embedded-PDF JS) and source maps.
mkdir -p "${VENDOR_DIR}/build"
cp "${WORK_DIR}/extracted/build/pdf.mjs" "${VENDOR_DIR}/build/pdf.mjs"
cp "${WORK_DIR}/extracted/build/pdf.worker.mjs" "${VENDOR_DIR}/build/pdf.worker.mjs"

# Viewer: skip viewer.html (replaced by lms/templates/pdf_viewer.html), the
# upstream test PDF, the debugger overlay, and the source map.
mkdir -p "${VENDOR_DIR}/web"
cp "${WORK_DIR}/extracted/web/viewer.css" "${VENDOR_DIR}/web/viewer.css"
cp "${WORK_DIR}/extracted/web/viewer.mjs" "${VENDOR_DIR}/web/viewer.mjs"

# Runtime asset directories: copy whole (cmaps, icc profiles, toolbar images,
# Fluent locales, standard PDF fonts, optional wasm modules).
for sub in cmaps iccs images locale standard_fonts wasm; do
    if [[ -d "${WORK_DIR}/extracted/web/${sub}" ]]; then
        cp -r "${WORK_DIR}/extracted/web/${sub}" "${VENDOR_DIR}/web/${sub}"
    fi
done

# Allow `file=` query values from the embedding LMS origin.
#
# Upstream `viewer.mjs` ships a `validateFileURL` helper that rejects any
# `?file=<url>` whose origin differs from the viewer's, unless the viewer
# itself is hosted at one of Mozilla's documented origins. The check exists
# for Mozilla's standalone hosted viewer; in our embed (where the viewer is
# loaded under the LMS host) it would reject every textbook URL that points
# at an external host (CDN, third-party PDF, etc.). Append `window.location.origin`
# to the `HOSTED_VIEWER_ORIGINS` set so the embedding origin is treated as
# trusted and the check passes for any URL.
#
# Security note: this is NOT an SSRF surface. The fetch happens entirely in
# the learner's browser; the LMS server makes no outgoing request on its
# behalf. The check upstream is targeted at a phishing scenario specific to
# Mozilla's hosted-viewer-as-a-service deployment (address bar shows a
# trusted Mozilla origin while displayed content is attacker-controlled).
# In our embed the address bar shows the LMS origin, the PDF URL is set by
# a course-author role that already has wide latitude to embed external
# content (HTML XBlocks, iframes, etc.), and the actual defense against
# malicious-PDF-as-RCE is the parser hardening that comes with the pdf.js
# version bump itself -- not this UI-level same-origin check.
#
# This is the only edit we make to upstream `viewer.mjs`; it must be
# re-applied on every refresh. The change is kept as a checked-in patch
# (next to the vendor dir, so the wipe above doesn't remove it) rather than
# an inline edit, mirroring common/static/js/vendor/CodeMirror/accessible.diff
# -- the diff is reviewable on its own and `git apply` fails loudly if the
# upstream context ever shifts, which is the signal to regenerate it.
PATCH_FILE="$(dirname "${VENDOR_DIR}")/pdfjs-hosted-viewer-origins.diff"
echo "Patching HOSTED_VIEWER_ORIGINS to accept the embedding origin"
git apply --directory="${VENDOR_DIR}" "${PATCH_FILE}"
if ! grep -q 'window.location.origin' "${VENDOR_DIR}/web/viewer.mjs"; then
    echo "error: HOSTED_VIEWER_ORIGINS patch did not apply -- did the upstream literal change?" >&2
    exit 1
fi

# Drop a stamp file so the pinned version is discoverable from inside the tree.
cat > "${VENDOR_DIR}/EDX_VERSION" <<EOF
pdfjs ${PDFJS_VERSION} (legacy build)
Source: ${ZIP_URL}
SHA256: ${PDFJS_LEGACY_ZIP_SHA256}
Refreshed by scripts/refresh-pdfjs-vendor.sh
EOF

echo "Done. ${VENDOR_DIR} now contains pdfjs ${PDFJS_VERSION} (legacy)."
