# Colors
R='\e[01;91m'
G='\e[01;92m'
Y='\e[01;93m'
W='\e[01;97m'
N='\e[0m'

# Clear screen
clear

# Check if yt-dlp is installed
if ! command -v yt-dlp &>/dev/null; then
    echo -e "$R[ERROR] yt-dlp not found! Installing...$N"
    pkg install yt-dlp jq -y || { echo -e "$R[ERROR] Installation failed!$N"; exit 1; }
fi

# Prompt for URL
echo -e "$G Enter the XHamster video URL: $N"
read -r URL

if [[ -z "$URL" ]]; then
    echo -e "$R[ERROR] URL cannot be empty!$N"
    exit 1
fi

# Show available formats
echo -e "$Y Fetching available formats...$N"
yt-dlp --list-formats "$URL"

# Prompt for format selection
echo -e "$G Enter the format code you want to download: $N"
read -r FORMAT_CODE

# Set output directory
OUTPUT_DIR="/data/data/com.termux/files/home/storage/xhamster_videos"
mkdir -p "$OUTPUT_DIR"

# Start downloading in selected format
echo -e "$Y Downloading in format: $FORMAT_CODE...$N"
yt-dlp -k -f "$FORMAT_CODE" -o "$OUTPUT_DIR/%(title)s.%(ext)s" "$URL"

echo -e "$G\n Download completed! Saved in: $OUTPUT_DIR $N"

