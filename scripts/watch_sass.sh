#!/usr/bin/env bash

# Wait for changes to Sass, and recompile.
# Invoke from repo root as `npm run watch-sass`.

# By default, only watches default Sass.
# To watch themes too, provide colon-separated paths in the COMPREHENSIVE_THEME_DIRS environment variable.
# Each path will be treated as a "theme dir", which means that every immediate child directory is watchable as a theme.
# For example:
#
#   COMPREHENSIVE_THEME_DIRS=/openedx/themes:./themes npm run watch-sass
#
# would watch default Sass as well as /openedx/themes/indigo, /openedx/themes/mytheme, ./themes/red-theme, etc.

set -euo pipefail

COL_SECTION="\e[1;36m"  # Section header color (bold cyan)
COL_LOG="\e[36m"        # Log color (cyan)
COL_WARNING="\e[1;33m"  # Warning (bold yellow)
COL_ERROR="\e[1;31m"    # Error (bold red)
COL_CMD="\e[35m"        # Command echoing (magenta)
COL_OFF="\e[0m"         # Normal color

section() {
    # Print a header in bold cyan to indicate sections of output.
    echo -e "${COL_SECTION}$*${COL_OFF}"
}
log() {
    # Info line. Indented by one space so that it appears as nested under section headers.
    echo -e " ${COL_LOG}$*${COL_OFF}"
}
warning() {
    # Bright yellow warning message.
    echo -e "${COL_WARNING}WARNING: $*${COL_OFF}"
}
error() {
    # Bright red error message.
    echo -e "${COL_ERROR}ERROR: $*${COL_OFF}"
}
echo_quoted_cmd() {
    # Echo args, each single-quoted, so that the user could copy-paste and run them as a command.
    # Indented by two spaces so it appears as nested under log lines.
    echo -e "  ${COL_CMD}$(printf "'%s' " "$@")${COL_OFF}"
}

start_sass_watch() {
    # Start a watch for .scss files in a particular dir. Run in the background.
    # Usage: start_sass_watch NAME_FOR_LOGGING HANDLER_COMMAND WATCH_ROOT_PATHS...
    local name="$1"
    local handler="$2"
    shift 2
    local paths=("$@")
    section "Starting Sass watchers for $name:"
    # Note: --drop means that we should ignore any change events that happen during recompilation.
    #       This is good because it means that if you edit 3 files, we won't fire off three simultaneous compiles.
    #       It's not perfect, though, because if you change 3 files, only the first one will trigger a recompile,
    #       so depending on the timing, your latest changes may or may not be picked up. We accept this as a reasonable
    #       tradeoff. Some watcher libraries are smarter than watchdog, in that they wait until the filesystem "settles"
    #       before firing off a the recompilation. This sounds nice but did not seem worth the effort for legacy assets.
    local watch_cmd=(watchmedo shell-command -v --patterns '*.scss' --recursive --drop --command "$handler" "${paths[@]}")
    echo_quoted_cmd "${watch_cmd[@]}"
    "${watch_cmd[@]}" &
}

# Verify execution environment.
if [[ ! -d common/static/sass ]] ; then
    error 'This command must be run from the root of edx-platform!'
    exit 1
fi
if ! type watchmedo 1>/dev/null 2>&1 ; then
    error "command not found: watchmedo"
    log "The \`watchdog\` Python package is probably not installed. You can install it with:"
    log "  uv sync --group development"
    exit 1
fi

# Reliably kill all child processes when script is interrupted (Ctrl+C) or otherwise terminated.
trap "exit" INT TERM
trap "kill 0" EXIT

# Watch default Sass.
# If it changes, then recompile the default theme *and* all custom themes,
# as custom themes' Sass is based on the default Sass.
start_sass_watch "default theme" \
    'npm run compile-sass-dev' \
    lms/static/sass \
    lms/static/certificates/sass \
    cms/static/sass \
    common/static \
    node_modules \
    xmodule/assets

# Watch each theme's Sass.
# If it changes, only recompile that theme.
export IFS=":"
for theme_dir in ${COMPREHENSIVE_THEME_DIRS:-} ; do
    for theme_path in "$theme_dir"/* ; do
        theme_name="${theme_path#"$theme_dir/"}"
        lms_sass="$theme_path/lms/static/sass"
        cert_sass="$theme_path/lms/static/certificates/sass"
        cms_sass="$theme_path/cms/static/sass"
        theme_watch_dirs=()
        if [[ -d "$lms_sass" ]] ; then
            theme_watch_dirs+=("$lms_sass")
        fi
        if [[ -d "$cert_sass" ]] ; then
            theme_watch_dirs+=("$cert_sass")
        fi
        if [[ -d "$cms_sass" ]] ; then
            theme_watch_dirs+=("$cms_sass")
        fi
        # A directory is a theme if it as LMS Sass *and/or* CMS Sass *and/or* certificate Sass.
        if [[ -n "${theme_watch_dirs[*]}" ]] ; then
            start_sass_watch "theme '$theme_name'" \
                "npm run compile-sass-dev -- -T $theme_dir -t $theme_name --skip-default" \
                "${theme_watch_dirs[@]}"
        fi
    done
done

# Wait until interrupted/terminated.
sleep infinity &
echo
echo "Watching Open edX LMS & CMS Sass for changes."
echo "Use Ctrl+c to exit."
echo
echo
wait $!

