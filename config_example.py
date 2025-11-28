import pyaudio

# --- API KEYS ---
# NOTE: Replace these with your actual keys.
FIREWORKS_API_KEY = "YOUR_FIREWORKS_API_KEY_HERE"
PICOVOICE_ACCESS_KEY = "YOUR_PICOVOICE_ACCESS_KEY_HERE"
ELEVENLABS_API_KEY = "YOUR_ELEVENLABS_API_KEY_HERE"

# --- CORE ASSISTANT SETTINGS ---
WAKE_WORD = "  "
SAMPLE_RATE = 16000
AUDIO_FORMAT = pyaudio.paInt16
TEST_MODE = False 

# --- NEW: Maximum time to record command after wake word ---
COMMAND_RECORDING_TIME = 8 # seconds

# --- LLM SETTINGS (UPDATED) ---
# NOTE: Replace these with the model paths/names you intend to use.
ROUTER_MODEL = "accounts/fireworks/models/YOUR_ROUTER_MODEL_NAME" # For function calling/routing
LLM_MODEL = "accounts/fireworks/models/YOUR_MAIN_LLM_NAME" # For general instructions/chat
FIREWORKS_URL = "https://api.fireworks.ai/....." # Base URL (usually unchanged)

# --- CONTACTS ---
# NOTE: Replace the placeholders with actual names and phone numbers in E.164 format (e.g., +919964769145).
CONTACT_BOOK = {
    "bob": "+91XXXXXXXXXX", 
    "jane": "+44XXXXXXXXXX",
}

# --- PYAUTOGUI SETTINGS (NO CHANGES) ---
# NOTE: These coordinates may need adjustment based on screen resolution.
MESSAGE_BOX_X = 960 
MESSAGE_BOX_Y = 970

# --- SPOTIFY API SETTINGS (NEW) ---
# NOTE: Replace the placeholders with your full, correct credentials from Spotify Developer Dashboard.
SPOTIFY_CLIENT_ID = "YOUR_SPOTIFY_CLIENT_ID" 
SPOTIFY_CLIENT_SECRET = "YOUR_SPOTIFY_CLIENT_SECRET" 
SPOTIFY_REDIRECT_URI = "http://..."# This must match your Spotify Developer settings
