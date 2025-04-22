
#!/bin/bash
# pi-client/update.sh

# Print colored messages
print_green() {
    echo -e "\e[32m$1\e[0m"
}

print_yellow() {
    echo -e "\e[33m$1\e[0m"
}

print_red() {
    echo -e "\e[31m$1\e[0m"
}

# Banner
print_green "========================================="
print_green "  VisionSolve Raspberry Pi Client Update  "
print_green "========================================="
echo ""

# Check if running on Raspberry Pi
if ! grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
    print_red "This script must be run on a Raspberry Pi!"
    exit 1
fi

# Get current installed version if available
if [ -f ~/visionsolve-client/version.txt ]; then
    CURRENT_VERSION=$(cat ~/visionsolve-client/version.txt)
    print_yellow "Current installed version: $CURRENT_VERSION"
else
    print_yellow "No existing version found. Will perform a fresh installation."
    CURRENT_VERSION="0.0.0"
fi

# Get server info from existing config or prompt
if [ -f ~/visionsolve-client/.env ]; then
    source ~/visionsolve-client/.env
    SERVER_ADDRESS=$(echo $API_SERVER | sed 's|http://||' | sed 's|:.*||')
    print_yellow "Using existing server configuration: $SERVER_ADDRESS"
else
    print_yellow "Please enter your VisionSolve server information:"
    read -p "Server address (e.g., 192.168.1.10): " SERVER_ADDRESS
    read -p "API key: " API_KEY
    
    if [ -z "$API_KEY" ]; then
        print_red "API key is required for authentication."
        exit 1
    fi
fi

# Create temp directory for downloads
mkdir -p ~/visionsolve-temp
cd ~/visionsolve-temp

# Check for updates
print_yellow "Checking for updates..."

# In a real implementation, this would contact the update server
# For demo purposes, we'll simulate an update check
NEW_VERSION="1.1.0"

if [ "$CURRENT_VERSION" == "$NEW_VERSION" ]; then
    print_green "You already have the latest version installed."
    rm -rf ~/visionsolve-temp
    exit 0
fi

print_green "New version available: $NEW_VERSION"
print_yellow "Downloading update package..."

# In a real implementation, this would download from your server
# For demo, we'll clone the repository
git clone https://github.com/yourusername/visionsolve-pi-client.git

if [ $? -ne 0 ]; then
    print_red "Failed to download update package."
    rm -rf ~/visionsolve-temp
    exit 1
fi

# Stop current service
print_yellow "Stopping VisionSolve service..."
sudo systemctl stop visionsolve.service

# Backup current config
if [ -f ~/visionsolve-client/.env ]; then
    cp ~/visionsolve-client/.env ~/visionsolve-temp/.env.backup
    print_yellow "Current configuration backed up."
fi

# Create or update installation directory
if [ ! -d ~/visionsolve-client ]; then
    mkdir -p ~/visionsolve-client
fi

# Copy new files
print_yellow "Installing new version..."
cp -r ~/visionsolve-temp/visionsolve-pi-client/* ~/visionsolve-client/

# Restore config if it exists
if [ -f ~/visionsolve-temp/.env.backup ]; then
    cp ~/visionsolve-temp/.env.backup ~/visionsolve-client/.env
    print_yellow "Configuration restored."
else
    # Create new config file
    print_yellow "Creating new configuration file..."
    cat > ~/visionsolve-client/.env << EOL
# Server connection
API_SERVER=http://${SERVER_ADDRESS}:4000
WEBSOCKET_SERVER=ws://${SERVER_ADDRESS}:5001

# Device settings
DEVICE_ID=${DEVICE_ID:-$(hostname | md5sum | head -c 8 | xargs echo "pi-")}
API_KEY=${API_KEY}

# Camera settings
NO_CAMERA=${NO_CAMERA:-0}

# Streaming settings
STREAM_RESOLUTION_WIDTH=640
STREAM_RESOLUTION_HEIGHT=480
STREAM_QUALITY=70
STREAM_FPS=10

# Debug
DEBUG=false
EOL
fi

# Update Python dependencies
print_yellow "Updating Python dependencies..."
cd ~/visionsolve-client
pip3 install -r requirements.txt

# Store new version
echo "$NEW_VERSION" > ~/visionsolve-client/version.txt

# Update systemd service
print_yellow "Updating system service..."
sudo bash -c "cat > /etc/systemd/system/visionsolve.service << EOL
[Unit]
Description=VisionSolve Pi Client
After=network.target

[Service]
User=$USER
WorkingDirectory=$HOME/visionsolve-client
ExecStart=$HOME/visionsolve-client/venv/bin/python client.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOL"

# Reload systemd and restart service
sudo systemctl daemon-reload
sudo systemctl enable visionsolve.service
sudo systemctl start visionsolve.service

# Clean up
rm -rf ~/visionsolve-temp

print_green "================================================"
print_green "Update complete! Now running version $NEW_VERSION"
print_green "The service has been restarted automatically."
print_green "To check status: sudo systemctl status visionsolve"
print_green "================================================"
