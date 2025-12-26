import os
import base64
import subprocess
import time
import threading
import sys
import httpx
from zai import ZaiClient
from tqdm import tqdm
import psutil
import datetime
import json
import logging

# Configure logging for backend usage
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analyzer")

class FootballGameAnalyzer:
    def __init__(self):
        # Fetch Key from Environment
        api_key = os.getenv("ZAI_API_KEY")
        if not api_key:
            raise ValueError("ZAI_API_KEY not found in environment variables")

        self.client = ZaiClient(
            api_key=api_key,
            timeout=httpx.Timeout(300.0, connect=10.0)
        )
        self.max_size_mb = 9.5  # Target just under 10MB
        self.sys_stats = "CPU: 0%"
        self.stop_monitoring = False
        
        # Retry configuration
        self.max_retries = 1
        self.base_retry_delay = 5  # seconds
        self.max_retry_delay = 120  # 2 minutes max

    def _monitor_system(self):
        """Updates CPU usage for the progress bar display"""
        while not self.stop_monitoring:
            cpu = psutil.cpu_percent(interval=0.5)
            self.sys_stats = f"CPU: {cpu}%"

    def _log(self, message, level="INFO"):
        """Structured logging with timestamps"""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        logger.info(f"[{level}] {message}")

    def _calculate_retry_delay(self, attempt):
        """Exponential backoff with jitter"""
        delay = min(self.base_retry_delay * (2 ** attempt), self.max_retry_delay)
        # Add jitter (±20%)
        import random
        jitter = delay * 0.2 * (random.random() * 2 - 1)
        return delay + jitter

    def compress_video(self, input_path, output_path):
        """Compresses long videos (14min) to be AI-compatible and fast - NO AUDIO"""
        self._log(f"Starting video compression: {input_path}")
        
        # Get duration for progress calculation
        try:
            probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', input_path]
            duration = float(subprocess.check_output(probe_cmd).decode('utf-8').strip())
            self._log(f"Video duration: {duration/60:.2f} minutes")
        except Exception as e:
            self._log(f"Error checking duration: {e}", "ERROR")
            return None
        
        # Calculate bitrate to fit 14 minutes into ~9.5MB (more space without audio)
        bitrate = int((self.max_size_mb * 1024 * 1024 * 8) / duration)
        self._log(f"Target bitrate: {bitrate} bps")

        # FFmpeg command - VIDEO ONLY (no audio track)
        cmd = [
            'ffmpeg', '-y', '-i', input_path,
            '-c:v', 'libx264', 
            '-preset', 'veryfast',  # Better encoding than ultrafast
            '-profile:v', 'baseline',  # Maximum compatibility
            '-level', '3.0',
            '-pix_fmt', 'yuv420p',  # Fixes "Silent Failure" / Unplayable video
            '-b:v', f'{bitrate}',
            '-vf', 'scale=480:270',  # Better resolution, even dimensions
            '-r', '5',  # 5 Frames per second is plenty for AI
            '-an',  # REMOVE AUDIO COMPLETELY
            '-movflags', '+faststart',  # CRITICAL: Moves metadata to start for playability
            '-progress', 'pipe:1', '-nostats', output_path
        ]

        self._log(f"Step 1: Compressing Video ({duration/60:.1f} minutes) - Removing Audio...")
        
        # Start hardware monitoring thread
        self.stop_monitoring = False
        monitor_thread = threading.Thread(target=self._monitor_system, daemon=True)
        monitor_thread.start()

        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        
        # In backend, we might not see tqdm, but we read output to keep buffer clear
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
        
        process.wait()
        self.stop_monitoring = True
        
        # Check if FFmpeg succeeded
        if process.returncode != 0:
            self._log("FFmpeg compression failed!", "ERROR")
            return None
        
        # Verify output file exists and is valid
        if not os.path.exists(output_path):
            self._log("Output file was not created!", "ERROR")
            return None
        
        file_size = os.path.getsize(output_path) / (1024*1024)
        self._log(f"Compression complete! File size: {file_size:.2f} MB")
        
        return output_path

    def _call_api_with_retry(self, video_base64, analysis_prompt):
        """Call API with retry logic"""
        
        for attempt in range(self.max_retries):
            try:
                self._log(f"API call attempt {attempt + 1}/{self.max_retries}")
                start_time = time.time()
                
                response = self.client.chat.completions.create(
                    model="glm-4.6v-flash",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "video_url",
                                    "video_url": {
                                        "url": f"data:video/mp4;base64,{video_base64}"
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": analysis_prompt
                                }
                            ]
                        }
                    ]
                )
                
                elapsed = time.time() - start_time
                self._log(f"API call succeeded in {elapsed:.2f} seconds")
                return {"success": True, "response": response, "attempts": attempt + 1, "time": elapsed}

            except Exception as e:
                error_str = str(e)
                self._log(f"API call failed: {error_str}", "ERROR")
                
                if attempt == self.max_retries - 1:
                    return {"success": False, "error": error_str, "attempts": attempt + 1}
                
                time.sleep(self._calculate_retry_delay(attempt))
                
        return {"success": False, "error": "Unknown error", "attempts": self.max_retries}

    def analyze_game(self, video_path, player_number, team, jersey_color):
        
        if not os.path.exists(video_path):
            return {"status": "failed", "error": f"{video_path} not found"}

        # --- STEP 1: COMPRESSION ---
        temp_video = f"{video_path}_compressed.mp4"
        ready_video = self.compress_video(video_path, temp_video)
        
        if ready_video is None:
            return {"status": "failed", "error": "Compression failed"}

        # --- STEP 2: BASE64 ENCODING ---
        try:
            with open(ready_video, "rb") as video_file:
                video_base64 = base64.b64encode(video_file.read()).decode("utf-8")
        except Exception as e:
            return {"status": "failed", "error": f"Encoding failed: {str(e)}"}

        # --- STEP 3: AI ANALYSIS ---
        analysis_prompt = f"""You are an expert football analyst. Analyze this game footage focusing on player #{player_number} wearing {jersey_color} for {team}.

Provide a comprehensive analysis with scores and details:

1. PLAYER PERFORMANCE SCORES (Rate 1-100):
   TECH: [score] - Technique (ball control, passing, shooting)
   SPD: [score] - Speed (sprint speed, acceleration)
   STA: [score] - Stamina (endurance, work rate)
   IQ: [score] - Game IQ (decision making, positioning)
   GS: [score] - Game Sense (reading the game, anticipation)
   AGI: [score] - Agility (quick turns, balance, body control)
   FOC: [score] - Focus (concentration, composure)

2. KEY HIGHLIGHTS:
   - Major moments involving the player
   - Best plays and contributions
   - Notable strengths shown

3. AREAS FOR IMPROVEMENT:
   - Weaknesses observed
   - Tactical adjustments needed

4. MATCH STATISTICS (estimate):
   - Touches on ball
   - Passes (successful/total)
   - Shots (on target/total)
   - Key passes/Assists
   - Tackles/Interceptions
   - Distance covered

5. OVERALL ASSESSMENT:
   Brief summary of performance and rating out of 10."""
        
        result = self._call_api_with_retry(video_base64, analysis_prompt)

        # Cleanup
        if os.path.exists(temp_video):
            os.remove(temp_video)

        if result["success"]:
            content = result["response"].choices[0].message.content
            return {"status": "success", "analysis": content}
        else:
            return {"status": "failed", "error": result["error"]}
