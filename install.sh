#!/bin/bash
# VisionSolve Raspberry Pi Client Installer

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
print_green "  VisionSolve Raspberry Pi Client Setup  "
print_green "========================================="
echo ""

# Check if running on Raspberry Pi
if ! grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
    print_red "This script must be run on a Raspberry Pi!"
    exit 1
fi

# Detect camera status
if [ "$NO_CAMERA" == "1" ]; then
    print_yellow "Camera support is DISABLED via NO_CAMERA environment variable"
    CAMERA_STATUS="disabled"
else
    # Try to detect camera hardware
    if vcgencmd get_camera | grep -q "detected=1"; then
        print_green "Camera hardware detected!"
        CAMERA_STATUS="enabled"
    else
        print_yellow "No camera hardware detected. Running in camera-less mode."
        CAMERA_STATUS="disabled"
        NO_CAMERA="1"
    fi
fi

# Prompt for configuration
echo "Please enter your VisionSolve server information:"
read -p "Server address (e.g., 192.168.1.10): " SERVER_ADDRESS
read -p "API key: " API_KEY
read -p "Device name (default: Raspberry Pi Camera): " DEVICE_NAME

# Set default device name if not provided
if [ -z "$DEVICE_NAME" ]; then
    if [ "$CAMERA_STATUS" == "disabled" ]; then
        DEVICE_NAME="Raspberry Pi (No Camera)"
    else
        DEVICE_NAME="Raspberry Pi Camera"
    fi
fi

# Generate a unique device ID if not provided
if [ -z "$DEVICE_ID" ]; then
    HOSTNAME=$(hostname)
    DEVICE_ID="pi-$(echo $HOSTNAME | md5sum | head -c 8)"
fi

# Install dependencies
print_yellow "Installing dependencies..."
sudo apt update
sudo apt install -y python3-pip python3-venv git

# If camera is enabled, install camera libraries
if [ "$CAMERA_STATUS" == "enabled" ]; then
    print_yellow "Installing camera libraries..."
    sudo apt install -y python3-picamera2 libopenjp2-7 libcamera-apps
fi

# Create project directory
print_yellow "Setting up project directory..."
mkdir -p ~/visionsolve-client
cd ~/visionsolve-client

# Set up virtual environment
print_yellow "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
print_yellow "Installing Python packages..."
pip install websockets requests python-dotenv pillow numpy

# Download client code from GitHub
print_yellow "Downloading client code..."
curl -O https://raw.githubusercontent.com/juvepr/visionsolve-pi-client/main/camera.py
curl -O https://raw.githubusercontent.com/juvepr/visionsolve-pi-client/main/client.py

# Create .env configuration file
print_yellow "Creating configuration file..."
cat > .env << EOL
# Server connection
API_SERVER=http://${SERVER_ADDRESS}:4000
WEBSOCKET_SERVER=ws://${SERVER_ADDRESS}:5001

# Device settings
DEVICE_ID=${DEVICE_ID}
DEVICE_NAME="${DEVICE_NAME}"
API_KEY=${API_KEY}

# Camera settings
NO_CAMERA=${NO_CAMERA}

# Streaming settings
STREAM_RESOLUTION_WIDTH=640
STREAM_RESOLUTION_HEIGHT=480
STREAM_QUALITY=70
STREAM_FPS=10

# Debug
DEBUG=true
EOL

# Create systemd service for auto-start
print_yellow "Setting up auto-start service..."
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

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable visionsolve.service
sudo systemctl start visionsolve.service

if [ "$CAMERA_STATUS" == "disabled" ]; then
    print_green "================================================"
    print_green "Installation complete! Your device ID is: $DEVICE_ID"
    print_green "The service is running in CAMERA-LESS MODE."
    print_green "The service is configured to start automatically on boot."
    print_green "To check status: sudo systemctl status visionsolve"
    print_green "To view logs: sudo journalctl -u visionsolve -f"
    print_green "================================================"
else
    print_green "================================================"
    print_green "Installation complete! Your device ID is: $DEVICE_ID"
    print_green "The service is now running and will start automatically on boot."
    print_green "To check status: sudo systemctl status visionsolve"
    print_green "To view logs: sudo journalctl -u visionsolve -f"
    print_green "================================================"
fi
