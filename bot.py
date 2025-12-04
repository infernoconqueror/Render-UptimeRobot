import os
import json
import asyncio
import threading
import time
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
# NEW: OAuth Imports
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
PARENT_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
COOKIES_FILE = "cookies.txt"

# --- NEW: OAUTH VARIABLES ---
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")

download_queue = asyncio.Queue()

# --- 1. KEEP ALIVE SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running 24/7!", 200

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def start_keep_alive():
    t = threading.Thread(target=run_http_server)
    t.daemon = True
    t.start()

# --- 2. GOOGLE DRIVE UPLOAD (UPDATED FOR OAUTH) ---
async def upload_to_drive(file_path, file_name):
    print(f"☁️ Uploading {file_name}...")
    try:
        if not REFRESH_TOKEN:
            print("❌ No Refresh Token found.")
            return None
        
        # Create Credentials from the Environment Variables
        creds = Credentials(
            None, # No access token yet (it will auto-refresh)
            refresh_token=REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
        
        service = build('drive', 'v3', credentials=creds)

        file_metadata = {'name': file_name, 'parents': [PARENT_FOLDER_ID]}
        media = MediaFileUpload(file_path, resumable=True)
        
        file = await asyncio.to_thread(
            service.files().create(body=file_metadata, media_body=media, fields='id').execute
        )
        return file.get('id')
    except Exception as e:
        print(f"Drive Upload Error: {e}")
        return None

# --- 3. COOKIE PARSER ---
def parse_cookies_netscape(path):
    cookies = []
    if not os.path.exists(path): return []
    with open(path, 'r') as f:
        for line in f:
            if line.startswith('#') or not line.strip(): continue
            parts = line.split('\t')
            if len(parts) >= 7:
                cookie = {
                    'domain': parts[0], 'name': parts[5],
                    'value': parts[6].strip(), 'path': parts[2],
                    'expiry': int(parts[4]) if parts[4].isdigit() else None
                }
                cookies.append(cookie)
    return cookies

# --- 4. BROWSER & SNIFFER LOGIC (Updated to Ignore Ads) ---
def get_video_stream(url):
    print(f"🕵️ Analyzing: {url}")
    
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    
    final_stream_url = None
    title = "video_download"
    screenshot_path = None
    
    try:
        # 1. Inject Cookies
        driver.get("https://curiositystream.com/login")
        cookies = parse_cookies_netscape(COOKIES_FILE)
        for c in cookies:
            try: driver.add_cookie(c)
            except: pass
            
        # 2. Load Target URL
        driver.get(url)
        time.sleep(5) 
        
        # FORCE PLAY
        print("▶️ Attempting to force play...")
        try: driver.execute_script("document.querySelector('video').play()")
        except: pass
        try: driver.find_element("tag name", "body").click()
        except: pass
        
        time.sleep(15) # Wait longer for ads to finish or main video to start
        
        # 3. Sniff Logs (And Filter out Ads!)
        logs = driver.get_log('performance')
        candidates = []
        
        for entry in logs:
            message = json.loads(entry['message'])['message']
            if message['method'] == 'Network.requestWillBeSent':
                req_url = message['params']['request']['url']
                
                # Check for stream extensions
                if '.m3u8' in req_url or '.mpd' in req_url:
                    # FILTER: Skip items that look like Ads
                    lower_url = req_url.lower()
                    if 'ad.mp4' in lower_url or 'preroll' in lower_url or 'doubleclick' in lower_url:
                        print(f"⚠️ Ignoring Ad: {req_url[:50]}...")
                        continue
                        
                    candidates.append(req_url)
        
        # We take the LAST candidate found, as the main video usually loads last
        if candidates:
            final_stream_url = candidates[-1]
        
        try:
            raw_title = driver.title.replace('Watch ', '').replace(' | Curiosity Stream', '').strip()
            title = "".join([c for c in raw_title if c.isalpha() or c.isdigit() or c==' ']).rstrip().replace(" ", "_")
        except: pass

        if not final_stream_url:
            print("❌ Stream not found.")
            screenshot_path = "debug_screenshot.png"
            driver.save_screenshot(screenshot_path)

    except Exception as e:
        print(f"Browser Error: {e}")
    finally:
        driver.quit()
        
    return final_stream_url, title, screenshot_path

# --- 5. QUEUE WORKER (Updated to Fix "Overlong Headers") ---
async def queue_worker(application):
    print("👷 Worker started...")
    while True:
        url, chat_id = await download_queue.get()
        try:
            await application.bot.send_message(chat_id, f"🔄 **Processing:**\n{url}", parse_mode='Markdown')
            
            # 1. Find Stream
            stream_link, title, debug_img = await asyncio.to_thread(get_video_stream, url)
            
            if not stream_link:
                msg = "❌ Failed to find stream."
                await application.bot.send_message(chat_id, msg)
                if debug_img and os.path.exists(debug_img):
                    await application.bot.send_photo(chat_id=chat_id, photo=open(debug_img, 'rb'))
                    os.remove(debug_img)
            else:
                filename = f"{title}.mp4"
                
                # --- FIX: Remove Cookie Header from FFmpeg ---
                # The video URL usually has a token built-in (e.g. ?token=xyz).
                # Sending the massive cookie list causes "Overlong headers" crash.
                # We ONLY send the User-Agent now.
                
                ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
                
                cmd = (
                    f'ffmpeg -user_agent "{ua}" '
                    f'-i "{stream_link}" '
                    f'-c copy -bsf:a aac_adtstoasc "{filename}" '
                    f'-y -hide_banner -loglevel error'
                )
                # ----------------------------------------------
                
                await application.bot.send_message(chat_id, f"⬇️ Found Stream. Downloading...", parse_mode='Markdown')
                
                exit_code = await asyncio.to_thread(os.system, cmd)
                
                if exit_code == 0 and os.path.exists(filename):
                    await application.bot.send_message(chat_id, "☁️ Uploading to Drive...")
                    file_id = await upload_to_drive(filename, filename)
                    
                    if file_id:
                        await application.bot.send_message(chat_id, f"✅ **Success!** Saved to Drive.", parse_mode='Markdown')
                    else:
                        await application.bot.send_message(chat_id, "❌ Upload Failed (Check Logs).")
                    os.remove(filename)
                else:
                    await application.bot.send_message(chat_id, "❌ FFmpeg Download Failed.")
                
        except Exception as e:
            await application.bot.send_message(chat_id, f"Error: {e}")
        finally:
            download_queue.task_done()


# --- 6. MAIN ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    chat_id = update.effective_chat.id
    if "curiositystream" not in url:
         await context.bot.send_message(chat_id, "⚠️ Please send a CuriosityStream link.")
         return
    await download_queue.put((url, chat_id))
    q_pos = download_queue.qsize()
    await context.bot.send_message(chat_id, f"✅ Added to Queue (Position: {q_pos})")

if __name__ == '__main__':
    start_keep_alive()
    if not TOKEN:
        print("❌ Error: TELEGRAM_TOKEN missing.")
    else:
        app_bot = ApplicationBuilder().token(TOKEN).build()
        app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        loop = asyncio.get_event_loop()
        loop.create_task(queue_worker(app_bot))
        print("🤖 Bot is Live!")
        app_bot.run_polling()
