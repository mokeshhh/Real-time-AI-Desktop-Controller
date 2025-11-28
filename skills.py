import requests
import json
import time
import pyautogui
import webbrowser
import screen_brightness_control as sbc
from pynput.keyboard import Key, Controller
import psutil
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize 
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from . import config 
from . import state 

# --- ROUTER PROMPT (Updated for 'sleep' action) ---
ROUTER_PROMPT = """
You are an Intent Router for a voice assistant. Your task is to analyze the user's query and classify their intent into one of the provided categories.

Output your response ONLY as a single JSON object. DO NOT include any explanatory text or markdown formatting.

Available Intents:
1. SEND_WHATSAPP: Use this if the user is asking to send a message to a person (e.g., "tell Bob I'll be late in 5 minutes").
2. SYSTEM_CONTROL: Use this if the user is asking to control volume, screen brightness, check battery status, or **manage the overall desktop environment (minimize, maximize, switch apps, or tell the assistant to go to sleep)**.
3. SPOTIFY_CONTROL: Use this if the user is asking to control Spotify music playback.
4. LAUNCH_TARGET: Use this if the user is asking to open or search for any application or website.
5. BROWSER_NAVIGATOR: Use this if the user is asking to perform an action *inside* the currently active web browser (e.g., "close this tab", "go back", "click the first link").
6. GENERAL_QUERY: Use this for any question, fact retrieval, or general conversation.
7. CONFIRM: Use this if the user is affirmatively confirming a previous step or action.
8. CANCEL: Use this if the user is canceling the previous action or dialogue.

Output Schema:
If intent is SEND_WHATSAPP:
{"intent": "SEND_WHATSAPP", "slots": {"contact": "contact_name", "message": "full_message_content"}}

If intent is SYSTEM_CONTROL:
{"intent": "SYSTEM_CONTROL", "slots": {"action": "volume_up/volume_down/set_volume/brightness_up/brightness_down/set_brightness/check_status/minimize_window/maximize_window/close_window/switch_app/sleep", "value": "numeric_percentage_or_None/battery/volume/brightness/None"}}

If intent is SPOTIFY_CONTROL:
{"intent": "SPOTIFY_CONTROL", "slots": {"action": "play/pause/next/previous/search_and_play", "query": "optional_song_or_artist_or_playlist_name"}}

If intent is LAUNCH_TARGET:
{"intent": "LAUNCH_TARGET", "slots": {"target": "name_of_app_or_website", "target_type": "app/website", "search_query": "optional_query_if_user_asked_to_search_something_on_the_target"}}

If intent is BROWSER_NAVIGATOR:
{"intent": "BROWSER_NAVIGATOR", "slots": {"action": "back/forward/close_tab/new_tab/switch_tab_next/switch_tab_prev/click_link_1"}}

If intent is GENERAL_QUERY, CONFIRM, or CANCEL:
{"intent": "INTENT_NAME", "slots": {"query": "original_user_query"}}

Analyze the following user query:
"""

# ----------------- VOLUME HELPER FUNCTIONS -----------------

def _get_volume_controller():
    CoInitialize()
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))

def _set_volume_percentage(percent):
    controller = None
    try:
        controller = _get_volume_controller()
        normalized_volume = max(0.0, min(1.0, percent / 100.0))
        controller.SetMasterVolumeLevelScalar(normalized_volume, None)
    finally:
        CoUninitialize()

def _get_volume_percentage():
    controller = None
    try:
        controller = _get_volume_controller()
        volume_scalar = controller.GetMasterVolumeLevelScalar()
        return int(volume_scalar * 100)
    except Exception:
        raise
    finally:
        CoUninitialize()

# ----------------- SYSTEM CONTROL EXECUTION (Updated for 'sleep') --------------------

def handle_system_action(action, value=None):
    
    try:
        step = int(value) if value is not None and str(value).isdigit() else 10
        step = max(1, min(100, step)) 
    except ValueError:
        step = 10 
        
    if action == "volume_up":
        try:
            current_vol = _get_volume_percentage()
            new_vol = min(100, current_vol + step) 
            _set_volume_percentage(new_vol)
            return f"Volume increased by {step} percent to {new_vol} percent."
        except Exception as e:
            return f"I had trouble adjusting the volume with pycaw: {e}"

    elif action == "volume_down":
        try:
            current_vol = _get_volume_percentage()
            new_vol = max(0, current_vol - step)
            _set_volume_percentage(new_vol)
            return f"Volume decreased by {step} percent to {new_vol} percent."
        except Exception as e:
            return f"I had trouble adjusting the volume with pycaw: {e}"

    elif action == "set_volume":
        try:
            val = int(value)
            val = max(0, min(100, val)) 
            _set_volume_percentage(val)
            return f"Volume set accurately to {val} percent."
        except (TypeError, ValueError):
            return "I need a valid number between 0 and 100 to set the volume."
        except Exception as e:
            return f"I had trouble setting the volume with pycaw: {e}"
            
    elif action == "brightness_up":
        try:
            current = sbc.get_brightness()[0]
            new_brightness = min(100, current + step)
            sbc.set_brightness(new_brightness)
            return f"Brightness increased by {step} percent to {new_brightness} percent."
        except Exception:
            return "Sorry, I couldn't increase the screen brightness."

    elif action == "brightness_down":
        try:
            current = sbc.get_brightness()[0]
            new_brightness = max(0, current - step)
            sbc.set_brightness(new_brightness)
            return f"Brightness decreased by {step} percent to {new_brightness} percent."
        except Exception:
            return "Sorry, I couldn't decrease the screen brightness."

    elif action == "set_brightness":
        try:
            val = int(value)
            val = max(0, min(100, val)) 
            sbc.set_brightness(val)
            return f"Screen brightness set to {val} percent."
        except (TypeError, ValueError):
            return "I need a valid number between 0 and 100 to set the brightness."
            
    elif action == "check_status":
        status_target = str(value).lower()
        
        if "volume" in status_target:
            try:
                current = _get_volume_percentage()
                return f"The current volume is {current} percent."
            except Exception:
                return "I had trouble checking the volume percentage."
        
        elif "brightness" in status_target:
            try:
                current = sbc.get_brightness()[0]
                return f"The current screen brightness is {current} percent."
            except Exception:
                return "I had trouble checking the screen brightness."
        
        elif "battery" in status_target:
            try:
                battery = psutil.sensors_battery()
                if battery is None:
                    return "I cannot detect the battery status on this device."
                
                percent = int(battery.percent)
                plugged = "and is currently charging" if battery.power_plugged else "and running on battery"
                
                return f"The battery is at {percent} percent, {plugged}."
            except Exception:
                return "I had trouble checking the battery status."

        else:
            return "I'm not sure what status you want me to check."
            
    # --- WINDOW MANAGEMENT (NEW LOGIC) ---
    elif action == "minimize_window":
        pyautogui.hotkey('win', 'down') 
        return "Minimizing the active window."
        
    elif action == "maximize_window":
        pyautogui.hotkey('win', 'up') 
        return "Maximizing the active window."
        
    elif action == "close_window":
        pyautogui.hotkey('alt', 'f4')
        return "Closing the active window or application."
        
    elif action == "switch_app":
        pyautogui.hotkey('alt', 'tab')
        return "Switching to the previous application."

    # --- NEW: GO TO SLEEP ACTION ---
    elif action == "sleep": 
        # The actual state change is handled in the FireworksResponder thread.
        return "Going to sleep mode now. Say the wake word to wake me up."

    return "I'm not sure how to perform that system action."

# --- BROWSER NAVIGATION EXECUTION (NEW FEATURE) ---

def handle_browser_navigation(action):
    """
    Performs actions specific to the current browser tab/window using hotkeys and clicks.
    """
    
    if action == "back":
        pyautogui.hotkey('alt', 'left') 
        return "Going back in the browser history."
        
    elif action == "forward":
        pyautogui.hotkey('alt', 'right') 
        return "Going forward in the browser history."
        
    elif action == "close_tab":
        pyautogui.hotkey('ctrl', 'w')
        return "Closing the current tab."
        
    elif action == "new_tab":
        pyautogui.hotkey('ctrl', 't')
        return "Opening a new browser tab."

    elif action == "switch_tab_next":
        pyautogui.hotkey('ctrl', 'tab')
        return "Switching to the next browser tab."

    elif action == "switch_tab_prev":
        pyautogui.hotkey('ctrl', 'shift', 'tab')
        return "Switching to the previous browser tab."
        
    elif action == "click_link_1":
        # --- ROBUST JAVASCRIPT HACK ---
        try:
            # 1. JavaScript to find and click the first main link on a Google/Bing/DuckDuckGo page.
            # This targets the anchor tag inside a main result block.
            js_script = "javascript:(function(){var a=document.querySelector('div.g a, #rso a, .b_algo a, .web-result a'); if(a) {a.click();}})();"

            # 2. Use the Chrome hotkey (Ctrl+L) to focus the address bar.
            pyautogui.hotkey('ctrl', 'l')
            time.sleep(0.2)
            
            # 3. Type the JavaScript into the address bar and press Enter.
            pyautogui.write(js_script, interval=0.001) 
            pyautogui.press('enter')
            
            return "Executing script to click the top search result."
            
        except Exception as e:
            print(f"[JavaScript Click Error]: {e}")
            return "I could not execute the script to click the link."
        
    return "I am unable to perform that browser action."


# --- APPLICATION/BROWSER LAUNCH EXECUTION (EXISTING) ---

def handle_launch_target_action(target_query, target_type, search_query=None):
    """
    Launches a local application via the Windows Search bar or opens a browser URL/Search.
    """
    target_lower = target_query.lower().strip()
    target_type_lower = target_type.lower().strip()
    
    # --- 1. APPLICATION LAUNCH PATH ---
    if target_type_lower == "app":
        try:
            pyautogui.press('win') 
            time.sleep(1) 
            
            pyautogui.write(target_query, interval=0.05) 
            time.sleep(1) 
            
            pyautogui.press('enter')
            
            return f"Searching for and launching the '{target_query}' application."
            
        except Exception as e:
            print(f"[Launcher Error] Failed to execute application '{target_query}' via Windows Search: {e}")
            return f"I had trouble launching the application '{target_query}'."

    # --- 2. WEBSITE/SEARCH PATH ---
    elif target_type_lower == "website":
        try:
            chrome_path = config.CHROME_PATH 
            chrome_cmd_list = [
                chrome_path, 
                f'--profile-directory={config.CHROME_PROFILE_DIR_NAME}',
                '%s' 
            ]
            webbrowser.register('chrome_launch', None, webbrowser.BackgroundBrowser(chrome_cmd_list))
            
            # Smart Platform Search Logic
            if search_query:
                platform_name = target_query.replace(' ', '').lower()
                query_encoded = search_query.strip().replace(' ', '+')
                
                final_url = f"https://www.{platform_name}.com/search?q={query_encoded}"
                
                if platform_name == "youtube":
                    final_url = f"[https://www.youtube.com/results?search_query=](https://www.youtube.com/results?search_query=){query_encoded}"
                elif platform_name == "wikipedia":
                    final_url = f"[https://en.wikipedia.org/w/index.php?search=](https://en.wikipedia.org/w/index.php?search=){query_encoded}"

                webbrowser.get('chrome_launch').open(final_url)
                return f"Searching {target_query} for: {search_query}."
                
            # Direct URL / General Search Logic (No search_query)
            if any(ext in target_lower for ext in [".com", ".org", ".net", ".co.uk"]) or target_lower in ["youtube", "reddit", "google"]:
                
                clean_target = target_lower.split()[0].replace('www.', '').replace('https://', '').split('/')[0]
                url = f"https://www.{clean_target}"
                if not any(ext in url for ext in [".com", ".org", ".net"]):
                    url += ".com" 
                
                webbrowser.get('chrome_launch').open(url)
                return f"Opening the web browser to {clean_target}."
            
            # General Google Search fallback
            else:
                search_url = f"[https://www.google.com/search?q=](https://www.google.com/search?q=){target_query.replace(' ', '+')}"
                webbrowser.get('chrome_launch').open(search_url)
                return f"Searching the web for: {target_query}."
                
        except Exception as e:
            print(f"[Launcher Error] Failed to open browser/search: {e}")
            return "I had trouble opening the Chrome browser to complete your request."
    
    else:
        return "I received a launch command, but I couldn't determine its type."


# --- SPOTIFY FALLBACK CONTROL EXECUTION (EXISTING) ---

keyboard = Controller()

def handle_spotify_fallback(action, query=None):
    if action == "play" or action == "pause":
        keyboard.press(Key.media_play_pause)
        keyboard.release(Key.media_play_pause)
        return "Using keyboard controls: Playing or pausing Spotify."
            
    elif action == "next":
        keyboard.press(Key.media_next)
        keyboard.release(Key.media_next)
        return "Using keyboard controls: Skipping to the next track."
        
    elif action == "previous":
        keyboard.press(Key.media_previous)
        keyboard.release(Key.media_previous)
        return "Using keyboard controls: Going back to the previous track."

    elif action == "search_and_play" and query:
        return f"I can only use basic controls (play, pause, skip) without the API. I can't search for '{query}' using the keyboard."
        
    return "Sorry, I can't perform that specific Spotify command using basic controls."


# --- WHATSAPP EXECUTION (EXISTING) ---
def handle_whatsapp_action(contact_name, message, phone_number, action="prepare"):
    
    whatsapp_url = f"[https://web.whatsapp.com/send?phone=](https://web.whatsapp.com/send?phone=){phone_number}"
    
    try:
        chrome_cmd_list = [
            config.CHROME_PATH, 
            f'--profile-directory={config.CHROME_PROFILE_DIR_NAME}', 
            '%s'
        ]
        
        webbrowser.register('chrome_specific', None, 
                             webbrowser.BackgroundBrowser(chrome_cmd_list), 
                             preferred=True)
        chrome_browser = webbrowser.get('chrome_specific')
    except webbrowser.Error as e:
        print(f"\n[ERROR] Chrome registration error or executable path issue: {e}")
        return "I encountered a profile error opening Chrome. Please check the settings."
    
    if action == "prepare":
        
        if not state.DIALOGUE_CONTEXT['slots'].get('opened'):
            chrome_browser.open_new_tab(whatsapp_url)
            time.sleep(5) 
            state.DIALOGUE_CONTEXT['slots']['opened'] = True
            
        time.sleep(1) 

        message_box_x = config.MESSAGE_BOX_X 
        message_box_y = config.MESSAGE_BOX_Y
        
        print(f"\n[ACTION] ⌨️ Simulating typing at X:{message_box_x}, Y:{message_box_y}")

        pyautogui.click(message_box_x, message_box_y) 
        pyautogui.hotkey('ctrl', 'a') 
        pyautogui.press('delete') 
        
        pyautogui.typewrite(message, interval=0.1) 
        
        return f"I have typed the message to {contact_name}. Should I send it?"

    elif action == "send":
        pyautogui.click(config.MESSAGE_BOX_X, config.MESSAGE_BOX_Y) 
        
        time.sleep(1) 
        pyautogui.press('enter')
        
        time.sleep(0.5)
        pyautogui.hotkey('ctrl', 'w')
        
        print(f"\n[ACTION] 🟢 MESSAGE SENT to {contact_name}. Window switched.")
        
        return f"The message has been sent to {contact_name}."