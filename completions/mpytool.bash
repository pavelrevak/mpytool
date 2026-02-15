# Bash completion for mpytool
# Install: source this file or copy to /etc/bash_completion.d/mpytool

# Cache for remote paths
_MPYTOOL_CACHE_FILE="/tmp/mpytool_completion_cache"
_MPYTOOL_CACHE_TIME="/tmp/mpytool_completion_cache_time"
_MPYTOOL_CACHE_PORT="/tmp/mpytool_completion_cache_port"
_MPYTOOL_CACHE_DIR="/tmp/mpytool_completion_cache_dir"

_mpytool_commands="ls tree cat cp mv mkdir rm pwd cd path stop monitor repl exec run reset info flash ota mount ln sleep speedtest"

_mpytool_detect_ports() {
    # Detect serial ports based on platform (same logic as mpytool)
    local -a ports=()
    case "$(uname)" in
        Darwin)
            # macOS: use cu.* (call-up) instead of tty.*
            for p in /dev/cu.usbmodem* /dev/cu.usbserial* /dev/cu.usb*; do
                [[ -e "$p" ]] && ports+=("$p")
            done
            ;;
        Linux)
            for p in /dev/ttyACM* /dev/ttyUSB*; do
                [[ -e "$p" ]] && ports+=("$p")
            done
            ;;
    esac
    # Print sorted unique ports
    printf '%s\n' "${ports[@]}" | sort -u
}

_mpytool_get_port() {
    local port=""
    local i
    for ((i=0; i < ${#COMP_WORDS[@]}; i++)); do
        if [[ "${COMP_WORDS[i]}" == "-p" || "${COMP_WORDS[i]}" == "--port" ]]; then
            port="${COMP_WORDS[i+1]}"
            break
        fi
    done

    # Auto-detect if not specified (first available)
    if [[ -z "$port" ]]; then
        port=$(_mpytool_detect_ports | head -1)
    fi
    echo "$port"
}

_mpytool_complete_remote() {
    # $1 = current word
    # $2 = "dirs" for directories only (optional)
    local cur="$1"
    local dirs_only="$2"

    # Extract dir_query: directory part to list
    local dir_query=""
    if [[ "$cur" == */* ]]; then
        dir_query="${cur%/*}/"
    else
        dir_query=":"
    fi

    local port=$(_mpytool_get_port)
    [[ -z "$port" ]] && return 1

    # Cache per (port, dir_query), 10 seconds
    local now=$(date +%s)
    local cache_time=0
    local cache_port=""
    local cache_dir=""

    [[ -f "$_MPYTOOL_CACHE_TIME" ]] && cache_time=$(cat "$_MPYTOOL_CACHE_TIME")
    [[ -f "$_MPYTOOL_CACHE_PORT" ]] && cache_port=$(cat "$_MPYTOOL_CACHE_PORT")
    [[ -f "$_MPYTOOL_CACHE_DIR" ]] && cache_dir=$(cat "$_MPYTOOL_CACHE_DIR")

    local cache_age=$((now - cache_time))

    if [[ $cache_age -ge 10 || "$cache_port" != "$port" || "$cache_dir" != "$dir_query" || ! -f "$_MPYTOOL_CACHE_FILE" ]]; then
        mpytool -p "$port" _paths "$dir_query" 2>/dev/null > "$_MPYTOOL_CACHE_FILE"
        if [[ $? -ne 0 ]]; then
            rm -f "$_MPYTOOL_CACHE_FILE"
            return 1
        fi
        echo "$now" > "$_MPYTOOL_CACHE_TIME"
        echo "$port" > "$_MPYTOOL_CACHE_PORT"
        echo "$dir_query" > "$_MPYTOOL_CACHE_DIR"
    fi

    # Build completions: prefix each entry with dir_query
    while IFS= read -r e; do
        [[ -z "$e" ]] && continue
        [[ -n "$dirs_only" && "$e" != */ ]] && continue
        local full="${dir_query}${e}"
        if [[ "$full" == "$cur"* ]]; then
            COMPREPLY+=("$full")
        fi
    done < "$_MPYTOOL_CACHE_FILE"
}

_mpytool_clear_cache() {
    rm -f "$_MPYTOOL_CACHE_FILE" "$_MPYTOOL_CACHE_TIME" "$_MPYTOOL_CACHE_PORT" "$_MPYTOOL_CACHE_DIR"
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
            COMPREPLY=($(compgen -W "$(_mpytool_detect_ports)" -- "$cur"))
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
    # pos=2 is first arg after command, pos=3 is second, etc.
    local nargs=$((pos - 2))  # number of already completed args
    case "$cmd" in
        ls|tree)
            # Optional remote path, -- anytime
            if [[ $pos -eq 2 ]]; then
                if [[ "$cur" == :* ]]; then
                    _mpytool_complete_remote "$cur"
                else
                    COMPREPLY=($(compgen -W ":" -- "$cur"))
                fi
            fi
            [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            ;;
        cat)
            # 1+ remote files required, -- after at least one
            if [[ "$cur" == :* ]]; then
                _mpytool_complete_remote "$cur"
            else
                COMPREPLY=($(compgen -W ":" -- "$cur"))
            fi
            [[ $nargs -ge 1 && ( -z "$cur" || "--" == "$cur"* ) ]] && COMPREPLY+=("--")
            ;;
        mkdir|rm)
            # 1+ remote paths required, -- after at least one
            if [[ "$cur" == :* ]]; then
                _mpytool_complete_remote "$cur"
            else
                COMPREPLY=($(compgen -W ":" -- "$cur"))
            fi
            [[ $nargs -ge 1 && ( -z "$cur" || "--" == "$cur"* ) ]] && COMPREPLY+=("--")
            ;;
        mv)
            # 2+ remote paths required, -- after at least two
            if [[ "$cur" == :* ]]; then
                _mpytool_complete_remote "$cur"
            else
                COMPREPLY=($(compgen -W ":" -- "$cur"))
            fi
            [[ $nargs -ge 2 && ( -z "$cur" || "--" == "$cur"* ) ]] && COMPREPLY+=("--")
            ;;
        cd)
            # Exactly 1 remote dir, -- only after it
            if [[ $pos -eq 2 ]]; then
                if [[ "$cur" == :* ]]; then
                    _mpytool_complete_remote "$cur" 'dirs'
                else
                    COMPREPLY=($(compgen -W ":" -- "$cur"))
                fi
            else
                [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            fi
            ;;
        path)
            # path [-f|-a|-d] [paths...]
            # Check if flags already present
            local has_flag=0
            for w in "${COMP_WORDS[@]:cmd_start+1}"; do
                case "$w" in
                    -f|--first|-a|--append|-d|--delete) has_flag=1 ;;
                esac
            done
            # Offer flags first
            if [[ $has_flag -eq 0 && ( "$cur" == -* || -z "$cur" ) ]]; then
                COMPREPLY=($(compgen -W "-f --first -a --append -d --delete" -- "$cur"))
            fi
            # Remote paths (: prefix)
            if [[ "$cur" == :* ]]; then
                _mpytool_complete_remote "$cur"
            else
                COMPREPLY+=($(compgen -W ":" -- "$cur"))
            fi
            # -- after at least one path
            [[ $nargs -ge 1 && ( -z "$cur" || "--" == "$cur"* ) ]] && COMPREPLY+=("--")
            ;;
        cp)
            # n local + 1 remote OR n remote + 1 local
            # Count local and remote args (excluding current word)
            local n_local=0 n_remote=0
            for ((j=cmd_start+1; j < COMP_CWORD; j++)); do
                if [[ "${COMP_WORDS[j]}" == :* ]]; then
                    ((n_remote++))
                else
                    ((n_local++))
                fi
            done
            if [[ $n_local -ge 1 && $n_remote -ge 1 ]]; then
                # Complete, only offer --
                [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            else
                # Need more args, offer files and :
                if [[ "$cur" == :* ]]; then
                    _mpytool_complete_remote "$cur"
                else
                    COMPREPLY=($(compgen -f -- "$cur"))
                    COMPREPLY+=($(compgen -W ":" -- "$cur"))
                fi
            fi
            ;;
        exec)
            # 1 code string, -- after it
            [[ $nargs -ge 1 && ( -z "$cur" || "--" == "$cur"* ) ]] && COMPREPLY+=("--")
            ;;
        run)
            # 1 local .py file, -- after it
            if [[ $pos -eq 2 ]]; then
                COMPREPLY=($(compgen -f -X '!*.py' -- "$cur"))
            fi
            [[ $nargs -ge 1 && ( -z "$cur" || "--" == "$cur"* ) ]] && COMPREPLY+=("--")
            ;;
        pwd)
            # No arguments, -- immediately
            [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            ;;
        reset)
            # Reset flags or --
            local has_mode=0
            for w in "${COMP_WORDS[@]:cmd_start+1}"; do
                case "$w" in
                    --machine|--rts|--raw|--boot|--dtr-boot) has_mode=1 ;;
                esac
            done
            if [[ $has_mode -eq 0 ]]; then
                COMPREPLY=($(compgen -W "--machine --rts --raw --boot --dtr-boot -t --" -- "$cur"))
            else
                [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            fi
            ;;
        stop|info)
            # No arguments, -- immediately
            [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            ;;
        repl|monitor)
            # Interactive, no chaining
            ;;
        flash)
            local flash_subcmd="${COMP_WORDS[cmd_start+1]}"
            if [[ $pos -eq 2 ]]; then
                COMPREPLY=($(compgen -W "read write erase" -- "$cur"))
            elif [[ $pos -eq 3 && "$flash_subcmd" == "erase" ]]; then
                COMPREPLY=($(compgen -W "--full" -- "$cur"))
                [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            elif [[ $pos -eq 3 ]]; then
                # read/write: label (device-specific, no completion)
                :
            elif [[ $pos -eq 4 && "$flash_subcmd" != "erase" ]]; then
                COMPREPLY=($(compgen -f -- "$cur"))
            elif [[ "$flash_subcmd" == "erase" ]]; then
                COMPREPLY=($(compgen -W "--full" -- "$cur"))
                [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            fi
            [[ "$flash_subcmd" != "erase" && $pos -ge 5 && ( -z "$cur" || "--" == "$cur"* ) ]] && COMPREPLY+=("--")
            ;;
        ota)
            # 1 firmware file, -- after it
            if [[ $pos -eq 2 ]]; then
                COMPREPLY=($(compgen -f -X '!*.bin' -- "$cur"))
            else
                [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            fi
            ;;
        sleep)
            # 1 number, -- after it
            [[ $nargs -ge 1 && ( -z "$cur" || "--" == "$cur"* ) ]] && COMPREPLY+=("--")
            ;;
        mount)
            # mount [-m] local_dir [:mount_point], -- for chaining
            # Check if -m flag already present
            local has_m=0
            for w in "${COMP_WORDS[@]:cmd_start+1}"; do
                [[ "$w" == "-m" || "$w" == "--mpy" ]] && has_m=1
            done
            # Offer -m flag first
            if [[ $has_m -eq 0 && ( "$cur" == -* || -z "$cur" ) ]]; then
                COMPREPLY=($(compgen -W "-m --mpy" -- "$cur"))
            fi
            # Local dir and mount point
            if [[ "$cur" == :* ]]; then
                COMPREPLY+=($(compgen -W ":" -- "$cur"))
            else
                COMPREPLY+=($(compgen -d -- "$cur"))
            fi
            # -- for chaining
            [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            ;;
        ln)
            # n local sources + 1 remote dest (: prefix, absolute path)
            local n_local=0 n_remote=0
            for ((j=cmd_start+1; j < COMP_CWORD; j++)); do
                if [[ "${COMP_WORDS[j]}" == :* ]]; then
                    ((n_remote++))
                else
                    ((n_local++))
                fi
            done
            if [[ $n_local -ge 1 && $n_remote -ge 1 ]]; then
                [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            else
                if [[ "$cur" == :* ]]; then
                    _mpytool_complete_remote "$cur"
                else
                    COMPREPLY=($(compgen -f -- "$cur"))
                    COMPREPLY+=($(compgen -W ":" -- "$cur"))
                fi
            fi
            ;;
        speedtest)
            # No arguments, -- immediately
            [[ -z "$cur" || "--" == "$cur"* ]] && COMPREPLY+=("--")
            ;;
        --)
            COMPREPLY=($(compgen -W "$_mpytool_commands" -- "$cur"))
            ;;
        *)
            COMPREPLY=($(compgen -W "$_mpytool_commands" -- "$cur"))
            ;;
    esac
}

complete -o nospace -o default -F _mpytool mpytool
