# pi-client/client.py

import os
import sys
import time
import json
import base64
import logging
import asyncio
import websockets
import uuid
import signal
import requests
import io
import numpy as np
from PIL import Image
from dotenv import load_dotenv
from camera import PiCamera

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if os.environ.get('DEBUG', 'False').lower() == 'true' else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('pi_client.log')
    ]
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
DEVICE_ID = os.environ.get('DEVICE_ID', f"pi-{uuid.uuid4().hex[:8]}")
API_SERVER = os.environ.get('API_SERVER', 'http://localhost:4000')
WEBSOCKET_SERVER = os.environ.get('WEBSOCKET_SERVER', 'ws://localhost:5001')
API_KEY = os.environ.get('API_KEY', 'default-api-key')

# Frame settings
STREAM_RESOLUTION = (
    int(os.environ.get('STREAM_RESOLUTION_WIDTH', 640)),
    int(os.environ.get('STREAM_RESOLUTION_HEIGHT', 480))
)
STREAM_QUALITY = int(os.environ.get('STREAM_QUALITY', 70))  # JPEG quality (0-100)
STREAM_FPS = int(os.environ.get('STREAM_FPS', 10))  # Target frames per second
STREAM_FRAME_INTERVAL = 1.0 / STREAM_FPS  # Time between frames in seconds

# Global variables
camera = None
websocket = None
token = None
stop_event = asyncio.Event()
active_streams = {}  # Map client_ids to stream control flags

def check_battery():
    """Check battery level if supported"""
    try:
        # If PowerBoost is connected to a GPIO pin with ADC capability
        # You can read battery level - this is just a placeholder
        # Implement actual battery reading based on your hardware setup
        return 100  # Placeholder for battery level
    except:
        return None

async def connect_and_register():
    """Connect to the WebSocket server and register the device"""
    global websocket, token
    
    # If we already have a token, use it, otherwise register
    if not token:
        try:
            # Register with the API server
            logger.info(f"Registering device with API server: {API_SERVER}")
            response = requests.post(
                f"{API_SERVER}/api/register-device",
                json={
                    "device_id": DEVICE_ID,
                    "device_type": "raspberry_pi",
                    "camera_module": "Camera Module 3",
                    "hardware_info": {
                        "model": get_pi_model(),
                        "system_info": get_system_info()
                    }
                },
                headers={"Authorization": f"Bearer {API_KEY}"}
            )
            
            if response.status_code == 200:
                data = response.json()
                token = data.get('token')
                websocket_url = data.get('websocket_url', WEBSOCKET_SERVER)
                
                logger.info(f"Device registered successfully. Using WebSocket server: {websocket_url}")
            else:
                logger.error(f"Failed to register device: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error registering device: {str(e)}")
            return False
    
    # Connect to WebSocket server
    try:
        websocket_url = f"{WEBSOCKET_SERVER}?token={token}"
        logger.info(f"Connecting to WebSocket server: {websocket_url}")
        websocket = await websockets.connect(websocket_url)
        
        # Wait for confirmation
        try:
            response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            response_data = json.loads(response)
            
            if response_data.get('type') == 'connected':
                logger.info("Successfully connected to WebSocket server")
                return True
            else:
                logger.error(f"Unexpected response from WebSocket server: {response_data}")
                return False
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for WebSocket server confirmation")
            return False
            
    except Exception as e:
        logger.error(f"Error connecting to WebSocket server: {str(e)}")
        return False

async def capture_and_send_image(client_id=None):
    """Capture an image and send it to the server
    
    Args:
        client_id (str, optional): ID of the client requesting the capture
        
    Returns:
        bool: True if successful, False otherwise
    """
    global camera, websocket
    
    if not camera or not websocket:
        logger.error("Camera or WebSocket not initialized")
        return False
    
    try:
        # Capture image
        image_path = await camera.capture_image()
        
        if not image_path:
            logger.error("Failed to capture image")
            return False
            
        # Encode image as base64
        with open(image_path, 'rb') as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode('utf-8')
            
        # Send image to server
        await websocket.send(json.dumps({
            "type": "image",
            "device_id": DEVICE_ID,
            "image": encoded_image,
            "timestamp": int(time.time()),
            "requesting_client_id": client_id,
            "battery": check_battery()
        }))
        
        # Remove the temporary image file
        os.remove(image_path)
        
        logger.info(f"Image captured and sent to server" + (f" for client {client_id}" if client_id else ""))
        return True
        
    except Exception as e:
        logger.error(f"Error capturing and sending image: {str(e)}")
        return False

async def start_video_stream(client_id):
    """Start streaming video frames to a specific client"""
    global camera, websocket, stop_event, active_streams
    
    if not camera or not websocket:
        logger.error("Camera or WebSocket not initialized")
        return
    
    logger.info(f"Starting video stream for client {client_id}")
    
    # Set a flag to control this specific stream
    active_streams[client_id] = True
    
    # Track frame timing for consistent frame rate
    last_frame_time = 0
    
    try:
        while active_streams.get(client_id, False) and not stop_event.is_set():
            try:
                # Check if websocket is still open
                if not websocket.open:
                    logger.warning("WebSocket closed, stopping stream")
                    break
                
                # Calculate time to wait for consistent frame rate
                current_time = time.time()
                elapsed = current_time - last_frame_time
                delay = max(0, STREAM_FRAME_INTERVAL - elapsed)
                
                if delay > 0:
                    await asyncio.sleep(delay)
                
                # Update frame time
                last_frame_time = time.time()
                
                # Capture a frame directly to memory
                frame = await camera.capture_frame(resolution=STREAM_RESOLUTION)
                
                if frame is None:
                    logger.warning("Failed to capture frame, retrying...")
                    await asyncio.sleep(0.5)
                    continue
                
                # Compress the image with PIL
                img_bytes = io.BytesIO()
                Image.fromarray(frame).save(img_bytes, format='JPEG', quality=STREAM_QUALITY)
                img_bytes.seek(0)
                
                # Encode as base64
                encoded_frame = base64.b64encode(img_bytes.read()).decode('utf-8')
                
                # Send the frame
                await websocket.send(json.dumps({
                    "type": "frame",
                    "device_id": DEVICE_ID,
                    "image": encoded_frame,
                    "timestamp": int(time.time()),
                    "client_id": client_id,
                    "battery": check_battery()
                }))
                
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed during streaming")
                break
            except Exception as e:
                logger.error(f"Error streaming frame: {str(e)}")
                await asyncio.sleep(0.5)
    
    except Exception as e:
        logger.error(f"Error in video stream: {str(e)}")
    finally:
        # Remove this stream from active streams
        if client_id in active_streams:
            del active_streams[client_id]
        logger.info(f"Video stream ended for client {client_id}")

async def handle_server_messages():
    """Handle messages from the WebSocket server"""
    global websocket, stop_event, active_streams
    
    try:
        while not stop_event.is_set():
            try:
                # Set a timeout for receiving messages
                message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                
                # Parse the message
                data = json.loads(message)
                message_type = data.get('type')
                
                if message_type == 'capture_request':
                    # Server is requesting an image capture
                    client_id = data.get('client_id')
                    logger.info(f"Capture request received from server for client {client_id}")
                    await capture_and_send_image(client_id)
                    
                elif message_type == 'stream_request':
                    # Client is requesting a video stream
                    client_id = data.get('client_id')
                    
                    if client_id:
                        # Start streaming in a separate task
                        asyncio.create_task(start_video_stream(client_id))
                    else:
                        logger.error("Stream request missing client_id")
                
                elif message_type == 'stop_stream':
                    # Client wants to stop streaming
                    client_id = data.get('client_id')
                    if client_id and client_id in active_streams:
                        logger.info(f"Stopping stream for client {client_id}")
                        active_streams[client_id] = False
                    
                elif message_type == 'pong':
                    # Server responded to our ping
                    logger.debug("Received pong from server")
                    
                elif message_type == 'error':
                    # Server sent an error
                    logger.error(f"Server error: {data.get('message')}")
                    
            except asyncio.TimeoutError:
                # No message received within timeout, continue
                pass
                
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed")
                break
                
    except Exception as e:
        logger.error(f"Error in message handler: {str(e)}")
    
    # If we're here, the connection is closed or there was an error
    if not stop_event.is_set():
        logger.info("Reconnecting to server...")
        # Stop all active streams
        active_streams.clear()
        await asyncio.sleep(5)  # Wait before reconnecting
        await main_loop()

async def send_status_updates():
    """Send periodic status updates to the server"""
    global websocket, stop_event
    
    while not stop_event.is_set():
        try:
            if websocket and websocket.open:
                battery = check_battery()
                await websocket.send(json.dumps({
                    "type": "status_update",
                    "device_id": DEVICE_ID,
                    "battery": battery,
                    "uptime": get_uptime(),
                    "timestamp": int(time.time())
                }))
                logger.debug("Status update sent to server")
                
            await asyncio.sleep(60)  # Send status update every minute
            
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed, cannot send status update")
            break
            
        except Exception as e:
            logger.error(f"Error sending status update: {str(e)}")
            await asyncio.sleep(60)

async def ping_server():
    """Send periodic pings to keep the connection alive"""
    global websocket, stop_event
    
    while not stop_event.is_set():
        try:
            if websocket and websocket.open:
                await websocket.send(json.dumps({
                    "type": "ping",
                    "device_id": DEVICE_ID,
                    "timestamp": int(time.time())
                }))
                
                logger.debug("Ping sent to server")
                
            await asyncio.sleep(30)  # Send ping every 30 seconds
            
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed, cannot send ping")
            break
            
        except Exception as e:
            logger.error(f"Error sending ping: {str(e)}")
            await asyncio.sleep(30)

def get_pi_model():
    """Get Raspberry Pi model information"""
    try:
        with open('/proc/device-tree/model', 'r') as f:
            return f.read().strip()
    except:
        return "Unknown Raspberry Pi"

def get_system_info():
    """Get system information"""
    try:
        import platform
        return {
            "os": platform.platform(),
            "python": platform.python_version(),
            "hostname": platform.node()
        }
    except:
        return {}

def get_uptime():
    """Get system uptime in seconds"""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            return int(uptime_seconds)
    except:
        return 0

async def main_loop():
    """Main application loop"""
    global camera, websocket, stop_event
    
    try:
        # Initialize the camera
        if not camera:
            camera = PiCamera()
            if not await camera.initialize():
                logger.error("Failed to initialize camera")
                stop_event.set()
                return
        
        # Connect to server and register device
        if not await connect_and_register():
            logger.error("Failed to connect and register")
            await asyncio.sleep(10)  # Wait before retrying
            return
            
        # Start the message handler
        message_handler = asyncio.create_task(handle_server_messages())
        
        # Start the ping task
        ping_task = asyncio.create_task(ping_server())
        
        # Start the status update task
        status_task = asyncio.create_task(send_status_updates())
        
        # Wait for the handlers to complete
        await asyncio.gather(message_handler, ping_task, status_task)
        
    except Exception as e:
        logger.error(f"Error in main loop: {str(e)}")
    
    finally:
        # Clean up resources
        if websocket and websocket.open:
            await websocket.close()
            
        if camera:
            await camera.cleanup()

async def main():
    """Application entry point"""
    global stop_event
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    for signame in ('SIGINT', 'SIGTERM'):
        loop.add_signal_handler(
            getattr(signal, signame),
            lambda: stop_event.set()
        )
    
    logger.info(f"Starting Pi Camera Client (Device ID: {DEVICE_ID})")
    
    # Run the main loop until stopped
    while not stop_event.is_set():
        try:
            await main_loop()
            
            # If main_loop returns, wait before retrying
            if not stop_event.is_set():
                await asyncio.sleep(5)
                
        except Exception as e:
            logger.error(f"Error in application: {str(e)}")
            await asyncio.sleep(5)
    
    logger.info("Application shutting down")

if __name__ == "__main__":
    asyncio.run(main())