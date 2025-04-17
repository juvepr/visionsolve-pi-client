# pi-client/camera.py

import os
import time
import uuid
import logging
import asyncio
import sys
import tempfile
import io
import numpy as np
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)

class PiCamera:
    """Interface for the Raspberry Pi Camera Module with enhanced error handling"""
    
    def __init__(self):
        self.camera = None
        self.initialized = False
        self.camera_type = None  # 'picamera2', 'legacy', 'disabled', or None
        self.temp_dir = os.environ.get('TEMP_IMAGE_DIR', 'temp_images')
        
        # Check if camera is disabled via environment variable
        self.no_camera = os.environ.get('NO_CAMERA', '0') == '1'
        if self.no_camera:
            logger.info("Camera support disabled via NO_CAMERA environment variable")
            self.camera_type = 'disabled'
            return
            
        # Ensure temp directory exists
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # Maximum number of initialization attempts
        self.max_init_attempts = 3
        self.current_attempt = 0
        
        # Streaming configuration
        self.streaming_config = None
        
    async def initialize(self):
        """Initialize the camera with retry logic
        
        Returns:
            bool: True if successful, False otherwise
        """
        # If camera is disabled, skip initialization
        if self.no_camera:
            logger.info("Camera initialization skipped (NO_CAMERA=1)")
            return False
            
        self.current_attempt += 1
        
        try:
            logger.info(f"Initializing camera (attempt {self.current_attempt}/{self.max_init_attempts})...")
            
            # First try picamera2 for newer Pi OS
            if await self._try_initialize_picamera2():
                return True
                
            # Fall back to legacy picamera
            if await self._try_initialize_legacy():
                return True
                
            # If both failed, report error
            if self.current_attempt < self.max_init_attempts:
                logger.warning(f"Camera initialization failed. Will retry in 10 seconds...")
                await asyncio.sleep(10)
                return await self.initialize()  # Recursive retry with delay
            else:
                logger.error(f"Failed to initialize camera after {self.max_init_attempts} attempts")
                return False
                
        except Exception as e:
            logger.error(f"Error initializing camera: {str(e)}")
            
            # Check if we should retry
            if self.current_attempt < self.max_init_attempts:
                logger.warning(f"Will retry camera initialization in 10 seconds...")
                await asyncio.sleep(10)
                return await self.initialize()  # Recursive retry with delay
            
            return False
    
    async def _try_initialize_picamera2(self):
        """Try to initialize using picamera2"""
        try:
            from picamera2 import Picamera2
            
            # Check if camera hardware is available
            import subprocess
            result = subprocess.run(['vcgencmd', 'get_camera'], stdout=subprocess.PIPE)
            camera_detected = 'detected=1' in result.stdout.decode('utf-8')
            
            if not camera_detected:
                logger.error("No camera hardware detected by the system")
                return False
            
            self.camera = Picamera2()
            
            # Apply configuration
            config = self.camera.create_still_configuration(
                main={"size": (1920, 1080)},
                lores={"size": (640, 480)},
                display="lores"
            )
            self.camera.configure(config)
            
            # Start the camera
            self.camera.start()
            await asyncio.sleep(2)  # Allow camera to warm up
            
            self.initialized = True
            self.camera_type = 'picamera2'
            logger.info("Camera initialized using picamera2")
            return True
            
        except ImportError:
            logger.info("picamera2 not available, will try legacy picamera")
            return False
        except Exception as e:
            logger.error(f"Error initializing picamera2: {str(e)}")
            return False
    
    async def _try_initialize_legacy(self):
        """Try to initialize using legacy picamera"""
        try:
            from picamera import PiCamera as LegacyPiCamera
            
            self.camera = LegacyPiCamera()
            
            # Apply settings
            self.camera.resolution = (1920, 1080)
            self.camera.framerate = 30
            
            # Allow camera to warm up
            await asyncio.sleep(2)
            
            self.initialized = True
            self.camera_type = 'legacy'
            logger.info("Camera initialized using legacy picamera")
            return True
            
        except ImportError:
            logger.error("Neither picamera2 nor legacy picamera are available")
            return False
        except Exception as e:
            logger.error(f"Error initializing legacy picamera: {str(e)}")
            return False
    
    async def capture_image(self, resolution=(1920, 1080)):
        """Capture an image from the camera with enhanced error handling
        
        Args:
            resolution (tuple, optional): Image resolution (width, height). Defaults to (1920, 1080).
            
        Returns:
            str: Path to the captured image, or None if failed
        """
        if self.no_camera or not self.initialized or not self.camera:
            logger.error("Camera not initialized or disabled")
            return None
            
        try:
            # Generate a unique filename in the temp directory
            filename = os.path.join(self.temp_dir, f"image_{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg")
            
            logger.info(f"Capturing image to {filename}")
            
            # Check which camera version we're using
            if self.camera_type == 'picamera2':
                # Capture image with picamera2
                with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
                    tmp_path = tmp_file.name
                
                try:
                    # First capture to a temporary file to prevent corruption
                    self.camera.capture_file(tmp_path)
                    
                    # Verify the image was captured successfully
                    if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                        # Move to final destination
                        os.rename(tmp_path, filename)
                    else:
                        raise Exception("Failed to capture image (empty file)")
                except Exception as e:
                    # Clean up temp file on error
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    raise e
                
            elif self.camera_type == 'legacy':
                # Set resolution for legacy camera
                self.camera.resolution = resolution
                
                # Capture image with retry on failure
                max_attempts = 3
                for attempt in range(max_attempts):
                    try:
                        self.camera.capture(filename)
                        
                        # Verify the image was captured
                        if os.path.exists(filename) and os.path.getsize(filename) > 0:
                            break
                        else:
                            if attempt < max_attempts - 1:
                                logger.warning(f"Empty capture on attempt {attempt+1}, retrying...")
                                await asyncio.sleep(1)
                            else:
                                raise Exception("Failed to capture image after multiple attempts")
                    except Exception as e:
                        if attempt < max_attempts - 1:
                            logger.warning(f"Capture failed on attempt {attempt+1}: {str(e)}, retrying...")
                            await asyncio.sleep(1)
                        else:
                            raise e
            else:
                raise Exception("Unknown camera type")
                
            # Verify the final image
            if os.path.exists(filename) and os.path.getsize(filename) > 0:
                logger.info("Image captured successfully")
                return filename
            else:
                raise Exception("Image capture verification failed")
            
        except Exception as e:
            logger.error(f"Error capturing image: {str(e)}")
            
            # If camera has failed, try to reinitialize
            try:
                logger.info("Attempting to reinitialize camera...")
                await self.cleanup(partial=True)
                await asyncio.sleep(2)
                await self.initialize()
            except:
                logger.error("Failed to reinitialize camera")
                
            return None
    
    async def capture_frame(self, resolution=(640, 480)):
        """Capture a single frame and return it as a numpy array
        
        Args:
            resolution (tuple): Frame resolution (width, height)
            
        Returns:
            numpy.ndarray: The captured frame, or None if failed
        """
        if self.no_camera or not self.initialized or not self.camera:
            logger.error("Camera not initialized or disabled")
            return None
            
        try:
            # Check which camera version we're using
            if self.camera_type == 'picamera2':
                # Create a configuration for capturing frames if not already created
                if not self.streaming_config or self.streaming_config["main"]["size"] != resolution:
                    self.streaming_config = self.camera.create_still_configuration(
                        main={"size": resolution},
                        display="main"
                    )
                    self.camera.configure(self.streaming_config)
                    # Give camera time to adjust
                    await asyncio.sleep(0.1)
                
                # Capture frame directly
                return self.camera.capture_array()
                
            elif self.camera_type == 'legacy':
                # For legacy PiCamera
                # Create a stream to hold the image data
                stream = io.BytesIO()
                
                # Set resolution
                self.camera.resolution = resolution
                
                # Capture to the stream
                self.camera.capture(stream, format='jpeg')
                
                # Convert to numpy array using PIL
                stream.seek(0)
                image = Image.open(stream)
                return np.array(image)
                
            else:
                raise Exception("Unknown camera type")
                
        except Exception as e:
            logger.error(f"Error capturing frame: {str(e)}")
            # Brief delay to avoid rapid retries on failure
            await asyncio.sleep(0.5)
            return None
    
    async def cleanup(self, partial=False):
        """Clean up resources
        
        Args:
            partial (bool): If True, only clean the camera but not the files
        """
        try:
            logger.info("Cleaning up camera resources")
            
            if self.camera:
                # Check which camera version we're using
                if self.camera_type == 'picamera2':
                    self.camera.stop()
                elif self.camera_type == 'legacy':
                    self.camera.close()
                    
                self.camera = None
                self.initialized = False
                
            # Only clean up files if not partial cleanup and not in no_camera mode
            if not partial and not self.no_camera:    
                # Clean up any temporary images
                for filename in os.listdir(self.temp_dir):
                    filepath = os.path.join(self.temp_dir, filename)
                    if os.path.isfile(filepath):
                        try:
                            os.remove(filepath)
                        except Exception as e:
                            logger.warning(f"Failed to remove temp file {filepath}: {str(e)}")
                    
                logger.info("Camera resources and temporary files cleaned up")
            else:
                logger.info("Camera resources cleaned up (partial cleanup)")
            
        except Exception as e:
            logger.error(f"Error cleaning up camera resources: {str(e)}")
    
    async def check_health(self):
        """Check camera health and reinitialize if needed
        
        Returns:
            bool: True if camera is healthy or was successfully reinitialized
        """
        if self.no_camera:
            logger.info("Camera health check skipped (NO_CAMERA=1)")
            return False
            
        if not self.initialized or not self.camera:
            logger.warning("Camera not initialized during health check")
            return await self.initialize()
            
        try:
            # Try to capture a test image to verify camera is working
            test_image = await self.capture_image(resolution=(640, 480))
            
            if test_image and os.path.exists(test_image):
                os.remove(test_image)  # Clean up test image
                logger.info("Camera health check passed")
                return True
            else:
                logger.warning("Camera health check failed - couldn't capture image")
                
                # Try to reinitialize
                await self.cleanup(partial=True)
                return await self.initialize()
                
        except Exception as e:
            logger.e
