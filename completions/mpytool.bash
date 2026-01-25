# Bash completion for mpytool
# Install: source this file or copy to /etc/bash_completion.d/mpytool

# Cache for remote files (using temp file)
_MPYTOOL_CACHE_FILE="/tmp/mpytool_completion_cache"
_MPYTOOL_CACHE_TIME="/tmp/mpytool_completion_cache_time"
_MPYTOOL_CACHE_PORT="/tmp/mpytool_completion_cache_port"

_mpytool_commands="ls dir tree get cat put cp mv mkdir delete del rm monitor follow repl exec reset info"

_mpytool_get_port() {
    local port=""
    local i
    for ((i=0; i < ${#COMP_WORDS[@]}; i++)); do
        if [[ "${COMP_WORDS[i]}" == "-p" || "${COMP_WORDS[i]}" == "--port" ]]; then
            port="${COMP_WORDS[i+1]}"
            break
        fi
    done

    # Auto-detect if not specified
    if [[ -z "$port" ]]; then
        for p in /dev/tty.usbmodem* /dev/tty.usbserial* /dev/ttyACM* /dev/ttyUSB*; do
            if [[ -e "$p" ]]; then
                port="$p"
                break
            fi
        done
    fi
    echo "$port"
}

_mpytool_fetch_paths() {
    local port=$(_mpytool_get_port)
    [[ -z "$port" || ! -e "$port" ]] && return 1

    local now=$(date +%s)
    local cache_time=0
    local cache_port=""

    [[ -f "$_MPYTOOL_CACHE_TIME" ]] && cache_time=$(cat "$_MPYTOOL_CACHE_TIME")
    [[ -f "$_MPYTOOL_CACHE_PORT" ]] && cache_port=$(cat "$_MPYTOOL_CACHE_PORT")

    local cache_age=$((now - cache_time))

    # Use cache if less than 60 seconds old and same port
    if [[ $cache_age -lt 60 && "$cache_port" == "$port" && -f "$_MPYTOOL_CACHE_FILE" ]]; then
        return 0
    fi

    # Fetch from device
    mpytool -p "$port" _paths 2>/dev/null > "$_MPYTOOL_CACHE_FILE"
    if [[ $? -ne 0 ]]; then
        rm -f "$_MPYTOOL_CACHE_FILE"
        return 1
    fi

    echo "$now" > "$_MPYTOOL_CACHE_TIME"
    echo "$port" > "$_MPYTOOL_CACHE_PORT"
    return 0
}

_mpytool_complete_remote() {
    local colon_prefix="$1"
    local cur="$2"

    _mpytool_fetch_paths || return 1

    # Remove leading : for processing
    cur="${cur#:}"

    local prefix=""
    # Handle absolute vs relative paths
    if [[ "$cur" == /* ]]; then
        prefix="/"
        cur="${cur#/}"
    fi

    # Extract directory part
    local dir_part=""
    if [[ "$cur" == */* ]]; then
        dir_part="${cur%/*}/"
    fi

    # Read cache and filter
    local -a matches=()
    local -A seen=()
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue

        # Check if in target directory
        if [[ -n "$dir_part" ]]; then
            [[ "$f" != "$dir_part"* ]] && continue
            local child="${f#$dir_part}"
        else
            local child="$f"
        fi

        # If child contains /, show parent dir
        if [[ "$child" == */* ]]; then
            child="${child%%/*}/"
        fi

        local full="${colon_prefix}${prefix}${dir_part}${child}"

        # Avoid duplicates
        if [[ -z "${seen[$full]}" ]]; then
            seen[$full]=1
            matches+=("$full")
        fi
    done < "$_MPYTOOL_CACHE_FILE"

    # Filter by current word and add to COMPREPLY
    local word
    for word in "${matches[@]}"; do
        if [[ "$word" == "${colon_prefix}${prefix}${cur}"* ]]; then
            COMPREPLY+=("$word")
        fi
    done
}

_mpytool_clear_cache() {
    rm -f "$_MPYTOOL_CACHE_FILE" "$_MPYTOOL_CACHE_TIME" "$_MPYTOOL_CACHE_PORT"
}

_mpytool() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    COMPREPLY=()

    # Find last -- separator
    local cmd_start=1
    local i
    for ((i=1; i < COMP_CWORD; i++)); do
        if [[ "${COMP_WORDS[i]}" == "--" ]]; then
            cmd_start=$((i + 1))
        fi
    done

    local cmd="${COMP_WORDS[cmd_start]}"
    local pos=$((COMP_CWORD - cmd_start + 1))

    # Handle options
    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "-V --version -p --port -a --address -b --baud -d --debug -v --verbose -q --quiet -f --force -e --exclude-dir" -- "$cur"))
        return
    fi

    # Handle option arguments
    case "$prev" in
        -p|--port)
            COMPREPLY=($(compgen -f -G "/dev/tty*" -- "$cur"))
            return
            ;;
        -b|--baud)
            COMPREPLY=($(compgen -W "9600 19200 38400 57600 115200 230400 460800 921600" -- "$cur"))
            return
            ;;
        -e|--exclude-dir)
            COMPREPLY=($(compgen -d -- "$cur"))
            return
            ;;
        -a|--address)
            return
            ;;
    esac

    # At command position
    if [[ $COMP_CWORD -eq $cmd_start ]]; then
        COMPREPLY=($(compgen -W "$_mpytool_commands" -- "$cur"))
        return
    fi

    # Command-specific completions
    case "$cmd" in
        ls|dir|tree|mkdir|delete|del|rm|get|cat)
            # Remote paths without : prefix
            _mpytool_complete_remote '' "$cur"
            ;;
        put)
            if [[ $pos -eq 2 ]]; then
                # First arg: local file
                COMPREPLY=($(compgen -f -- "$cur"))
            else
                # Second arg: remote path
                _mpytool_complete_remote '' "$cur"
            fi
            ;;
        cp)
            if [[ "$cur" == :* ]]; then
                _mpytool_complete_remote ':' "$cur"
            else
                COMPREPLY=($(compgen -f -- "$cur"))
                COMPREPLY+=($(compgen -W ":" -- "$cur"))
            fi
            ;;
        mv)
            if [[ "$cur" == :* ]]; then
                _mpytool_complete_remote ':' "$cur"
            else
                COMPREPLY=($(compgen -W ":" -- "$cur"))
            fi
            ;;
        exec|repl|monitor|follow|reset|info)
            ;;
        --)
            COMPREPLY=($(compgen -W "$_mpytool_commands" -- "$cur"))
            ;;
        *)
            COMPREPLY=($(compgen -W "$_mpytool_commands" -- "$cur"))
            ;;
    esac

    # Always offer -- for chaining
    if [[ -z "$cur" || "--" == "$cur"* ]]; then
        COMPREPLY+=("--")
    fi
}

complete -o nospace -o default -F _mpytool mpytool
