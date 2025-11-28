# spotify_api.py (Aggressive Match & No Ambiguous Fallback)
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from . import config
import os
import json
import subprocess
import time

# Define the scope needed for playback control and reading state
SCOPE = "user-read-playback-state,user-modify-playback-state,playlist-read-private,user-library-read"
CACHE_FILE = ".spotify_token_cache"

# --- Global Client (Initialized at Startup) ---
SPOTIFY_CLIENT = None

# --- Helper Function: Launch Spotify ---
def _launch_spotify_app():
    """Attempts to launch the Spotify desktop application."""
    print("Attempting to launch the Spotify desktop application...")
    try:
        # Use 'start spotify:' which is the most reliable way to launch the Windows desktop app
        subprocess.run(['start', 'spotify:'], check=True, shell=True)
        return True
    except Exception as e:
        print(f"🚨 Failed to launch Spotify app automatically: {e}")
        return False

# --- Helper Function: Find and Activate Device (Runs only ON COMMAND) ---
def _find_and_activate_device(client, max_retries=6): 
    """
    Finds a running device, attempting to launch Spotify if necessary.
    Returns the device ID or None.
    """
    device_id = None
    launched_in_this_cycle = False 

    for attempt in range(max_retries):
        try:
            devices = client.devices()
            
            if devices and devices.get('devices'):
                # 1. Look for the actively playing device (highest priority)
                for device in devices['devices']:
                    if device['is_active']:
                        print(f"✅ Found active Spotify device: {device['name']}")
                        return device['id']
                
                # 2. If no active device, find a desktop device and transfer playback
                for device in devices['devices']:
                    if device['type'] in ['Computer', 'Desktop', 'Windows', 'Mac']:
                        device_id = device['id']
                        print(f"⚠️ Transferring playback to device: {device['name']}")
                        # Force_play=True ensures playback starts on this device
                        client.transfer_playback(device_id=device_id, force_play=True) 
                        return device_id
            
            # --- Device Not Found - Logic for Auto-Launch and Retry ---
            if device_id is None:
                if attempt == 0 and not launched_in_this_cycle:
                    print("❌ No Spotify devices found. Attempting to launch the Spotify application...")
                    
                    if _launch_spotify_app():
                        launched_in_this_cycle = True
                        print("Waiting for Spotify to register...")
                        time.sleep(5) # Initial wait for app to load
                        continue # Go to the next retry attempt

                elif launched_in_this_cycle and attempt < max_retries - 1:
                    print(f"Waiting for device registration (Attempt {attempt+1}/{max_retries-1})....")
                    time.sleep(2) # Shorter wait for subsequent retries
                    continue
            
            # If we reach here, we've exhausted all options or retries
            return None 

        except Exception as e:
            print(f"🚨 Error during device activation: {e}")
            return None
    
    return None # Device not found after all retries

# --- Authentication and Initialization (Simplified for Startup) ---

def get_spotify_client():
    global SPOTIFY_CLIENT
    
    if config.SPOTIFY_CLIENT_ID in ["YOUR_SPOTIFY_CLIENT_ID", "336a2312bafaf383b169"]:
        print("⚠️ Spotify API not fully configured. API control disabled.")
        return None

    try:
        # Client initialization (Authorization)
        client = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
            redirect_uri=config.SPOTIFY_REDIRECT_URI,
            scope=SCOPE,
            cache_path=CACHE_FILE,
            show_dialog=False
        ))
        
        # Test basic connection without affecting playback state
        client.current_user()
        print("✅ Spotify API authentication successful. Device check will happen on first command.")
        return client
            
    except Exception as e:
        print(f"🚨 Spotify API Authentication Error. Check keys/network: {e}")
        return None

# --- API Control Functions (Final Fix for Playlist Search) ---

# --- API Control Functions (Final Fix for Playlist Search) ---

def api_control_playback(client, action, query=None):
    """
    Uses the Spotipy client to execute playback commands.
    """
    if not client:
        return None 

    # *** ACTIVATE DEVICE ON DEMAND ***
    device_id = _find_and_activate_device(client)
    
    if not device_id:
        print("❌ Could not locate or activate a Spotify device. Falling back to media keys.")
        return None 

    try:
        # --- Simple Playback Controls (No Change) ---
        if action in ["play", "pause"]:
            if action == "play":
                client.start_playback(device_id=device_id)
                return "Resuming Spotify playback via API."
            else:
                client.pause_playback(device_id=device_id)
                return "Pausing Spotify playback via API."
                
        elif action == "next":
            client.next_track(device_id=device_id)
            return "Skipping to the next track via API."
            
        elif action == "previous":
            client.previous_track(device_id=device_id)
            return "Returning to the previous track via API."

        # --- Search and Play (New Dedicated User Playlist Logic) ---
        elif action == "search_and_play" and query:
            
            normalized_query = query.lower().strip().replace("playlist", "").strip()
            
            # --- 1. DEDICATED LIKED SONGS CHECK (The new fix) ---
            if normalized_query in ["liked songs", "my liked songs", "liked tracks", "liked", "like songs"]:
                try:
                    # To play Liked Songs, we use the saved tracks context
                    # The URI for "Liked Songs" is typically spotify:user:<user_id>:collection:tracks
                    # However, simply playing the user's saved tracks is the most reliable method
                    current_user_id = client.current_user()['id']
                    liked_songs_uri = f"spotify:user:{current_user_id}:collection"
                    
                    client.start_playback(device_id=device_id, context_uri=liked_songs_uri)
                    return "Playing your Liked Songs via API."
                except Exception as e:
                    print(f"❌ Failed to play Liked Songs via dedicated URI. Error: {e}")
                    return "I found your Liked Songs library but the Spotify service failed to start playback."


            # 2. SEARCH ALL USER PLAYLISTS (For "English", "Download", etc.)
            playlists = client.current_user_playlists(limit=50)['items'] 
            uri_to_play = None
            item_name = None
            
            # Search for a direct, case-insensitive, and cleaned match
            for playlist in playlists:
                normalized_item_name = playlist['name'].lower().strip()
                
                # Check for an EXACT name match after normalizing
                if normalized_item_name == normalized_query:
                    uri_to_play = playlist['uri']
                    item_name = playlist['name']
                    print(f"✅ Found EXACT user playlist match: {item_name} with URI: {uri_to_play}")
                    break
            
            # 3. Attempt to play the matched User Playlist URI
            if uri_to_play:
                try:
                    # Attempting to play the specific context URI
                    client.start_playback(device_id=device_id, context_uri=uri_to_play)
                    return f"Playing your playlist '{item_name}' via API."
                except spotipy.SpotifyException as se:
                    # If playing the user playlist fails, give explicit error message
                    print(f"❌ Failed to play user playlist '{item_name}' (SpotifyException). Error: {se}")
                    return f"I found your playlist '{item_name}' but the Spotify service failed to start playback on the device. Please check the Spotify app status."
                except Exception as e:
                    print(f"❌ Failed to play user playlist '{item_name}' (General Error). Error: {e}")
                    return f"I found your playlist '{item_name}' but encountered an error trying to play it."


            # 4. Fallback to general SPOTIFY search for a specific track (if no user playlist was found)
            search_results = client.search(q=query, limit=1, type='track')
            
            if search_results and search_results.get('tracks', {}).get('items'):
                # Play the best matching track
                item = search_results['tracks']['items'][0]
                uri_to_play = item['uri']
                item_name = item['name']
                client.start_playback(device_id=device_id, uris=[uri_to_play])
                return f"I am playing the song '{item_name}' via API."
            
            # 5. Final Fallback: The item simply does not exist.
            return f"I searched for '{query}' but couldn't find a matching playlist or song in your library, nor could I find a matching song."

        return None 
        
    except spotipy.SpotifyException as se:
        print(f"🚨 Spotify API Playback Error (General): {se}")
        return None 
    except Exception as e:
        print(f"🚨 General API Control Error: {e}")
        return None