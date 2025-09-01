#!/data/data/com.termux/files/usr/bin/bash

# --- CONFIGURATION & SETUP ---
SOCKET_PATH="/data/data/com.termux/files/usr/tmp/mpv.socket"
ORIGINAL_THUMBNAIL="$1"

# --- DEPENDENCY CHECKS ---
for cmd in jq jp2a convert; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: Required command '$cmd' is not found."
        echo "Please ensure 'jq', 'jp2a', and 'imagemagick' are installed."
        exit 1
    fi
done

if [ -z "$ORIGINAL_THUMBNAIL" ] || [ ! -f "$ORIGINAL_THUMBNAIL" ]; then
    echo "Error: Thumbnail file not provided or not found."
    exit 1
fi

# --- HELPER FUNCTIONS ---

# (get_mpv_property, send_mpv_command, format_time functions remain the same as before)
get_mpv_property() { local p=$1; echo "{ \"command\": [\"get_property\", \"$p\"] }" | socat - "$SOCKET_PATH" | jq -r '.data'; }
send_mpv_command() { echo "{ \"command\": $1 }" | socat - "$SOCKET_PATH" > /dev/null; }
format_time() { local s=${1%.*}; [ -z "$s" ]||[ "$s" -lt 0 ] && s=0; printf "%02d:%02d" $((s/60)) $((s%60)); }


# NEW: Function to generate the full-screen ASCII art background
generate_background_art() {
    local input_image=$1
    local blurred_image="blurred_thumb.jpg"
    local term_w=$(tput cols)
    local term_h=$(tput lines)

    # Blur the image heavily to make it look like a background
    # -resize: Make it small for faster processing
    # -blur 0x8: The first number is radius (0=auto), second is sigma (the blur amount)
    # -resize ${term_w}x${term_h}\!: Force it to the terminal dimensions
    convert "$input_image" -resize 200x -blur 0x8 -resize "${term_w}x${term_h}!" "$blurred_image"

    # Convert the blurred image to colored ASCII art and load into an array
    # mapfile (or readarray) reads stdin into an array. It's very efficient.
    mapfile -t BG_ART_LINES < <(jp2a --color --width="$term_w" --height="$term_h" "$blurred_image")
    
    rm "$blurred_image" # Clean up
}

# NEW: Function to overlay text onto a specific line of an array (our frame buffer)
overlay_text() {
    local line_num=$1
    local text="$2"
    local -n buffer_ref=$3 # A nameref to the array we are modifying

    local term_w=$(tput cols)
    local text_len=${#text}
    local start_col=$(( (term_w - text_len) / 2 ))
    [ $start_col -lt 0 ] && start_col=0

    # Get the original line from the background art
    local original_line="${BG_ART_LINES[$line_num]}"

    # Slice the original line to make space for our new text
    local prefix=$(echo -e "$original_line" | cut -c "-$start_col")
    local suffix_start=$(( start_col + text_len + 1 ))
    local suffix=$(echo -e "$original_line" | cut -c "$suffix_start"-)

    # Combine them and update the buffer
    buffer_ref[$line_num]="${prefix}${text}${suffix}"
}

# --- THE MAIN DRAWING LOOP ---
draw_ui() {
    tput civis
    trap 'tput cnorm; exit' EXIT INT TERM

    # Generate the background art ONCE before the loop starts
    echo "Generating UI... please wait."
    generate_background_art "$ORIGINAL_THUMBNAIL"

    while true; do
        # 1. GET PLAYER STATE
        local media_title=$(get_mpv_property 'media-title')
        local time_pos=$(get_mpv_property 'time-pos')
        local duration=$(get_mpv_property 'duration')
        local is_paused=$(get_mpv_property 'pause')

        if [ "$duration" == "null" ]; then
            tput cup 0 0 # Move cursor to top left
            echo "Loading media..."
            sleep 1
            continue
        fi

        # 2. CREATE A FRESH FRAME BUFFER for this loop iteration
        # This copies our background art so we can draw on it without destroying the original.
        local frame_buffer=("${BG_ART_LINES[@]}")

        # 3. PREPARE UI ELEMENTS
        # Progress Bar
        local bar_width=40
        local int_percent=0
        if (( $(echo "$duration > 0" | bc -l) )); then
            int_percent=$(echo "($time_pos / $duration) * 100" | bc -l)
        fi
        int_percent=${int_percent%.*}
        local filled_len=$(( (int_percent * bar_width) / 100 ))
        local unfilled_len=$(( bar_width - filled_len ))
        local filled_bar=$(printf "%${filled_len}s" | tr ' ' '█')
        local unfilled_bar=$(printf "%${unfilled_len}s" | tr ' ' '─')
        local progress_bar_str="${G}${filled_bar}${W}◉${BL}${unfilled_bar}${N}"

        # Play/Pause Icon
        local play_status_icon="▶ Play"
        [ "$is_paused" == "false" ] && play_status_icon="⏸ Pause"

        # Other text
        local time_str="$(format_time $time_pos) / $(format_time $duration)"
        local controls_str="${C}[<] Prev  [${play_status_icon}]  [>] Next  [q] Quit${N}"

        # 4. "DRAW" ELEMENTS ONTO THE FRAME BUFFER using our overlay function
        # We need to decide on which lines to draw our UI.
        local term_h=$(tput lines)
        local title_line=$(( term_h / 2 - 4 ))
        local progress_line=$(( term_h / 2 - 2 ))
        local time_line=$(( term_h / 2 - 1 ))
        local controls_line=$(( term_h - 3 ))
        
        overlay_text $title_line "$Y$media_title$N" frame_buffer
        overlay_text $progress_line "$progress_bar_str" frame_buffer
        overlay_text $time_line "$time_str" frame_buffer
        overlay_text $controls_line "$controls_str" frame_buffer

        # 5. PRINT THE ENTIRE FRAME BUFFER TO THE SCREEN
        tput cup 0 0 # Move cursor to top-left to prevent flickering
        printf "%s\n" "${frame_buffer[@]}"

        # 6. HANDLE USER INPUT
        read -s -n 1 -t 1 key
        case "$key" in
            'p'|' ') send_mpv_command '["cycle", "pause"]' ;;
            '>'|'n') send_mpv_command '["playlist-next"]' ;;
            '<'|'b') send_mpv_command '["playlist-prev"]' ;;
            'q') send_mpv_command '["quit"]'; break ;;
        esac
    done
}

# --- SCRIPT ENTRY POINT ---
draw_ui
