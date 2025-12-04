import os
import json
import asyncio
import threading
import logging
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- CONFIGURATION (Load from Environment Variables) ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
# Paste the FULL content of your credentials.json into this Env Variable on Render
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS") 
# The Folder ID from the URL of your Drive folder
PARENT_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID") 

# Global Queue
download_queue = asyncio.Queue()

# --- 1. KEEP ALIVE SERVER (Flask) ---
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

# --- 2. GOOGLE DRIVE UPLOAD ---
async def upload_to_drive(file_path, file_name):
    print(f"☁️ Uploading {file_name}...")
    try:
        if not GOOGLE_CREDS_JSON:
            return None
            
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)

        file_metadata = {'name': file_name, 'parents': [PARENT_FOLDER_ID]}
        media = MediaFileUpload(file_path, resumable=True)
        
        # Run blocking upload in a thread
        file = await asyncio.to_thread(
            service.files().create(body=file_metadata, media_body=media, fields='id').execute
        )
        return file.get('id')
    except Exception as e:
        print(f"Drive Upload Error: {e}")
        return None

# --- 3. BROWSER & DOWNLOAD LOGIC ---
def process_video(url):
    """Sync function to handle Browser + FFmpeg"""
    print(f"🕵️ Analyzing: {url}")
    
    # Chrome Options for Server
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    
    output_filename = "video.mp4"
    title = "Unknown Video"
    
    try:
        driver.get(url)
        # Note: You need to implement your Login logic here if not using cookies
        # Or pass cookies via Env Variable if needed for CuriosityStream
        
        # MOCK LOGIC for demo (Replace with your actual finding logic)
        title = driver.title.replace(" ", "_")
        output_filename = f"{title}.mp4"
        
        # Assume we found the stream link (You need your 'get_stream_url' logic here)
        stream_link = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4" # Placeholder
        
        print(f"⬇️ Downloading {title}...")
        # Download using FFmpeg
        cmd = f'ffmpeg -i "{stream_link}" -c copy "{output_filename}" -y -loglevel error'
        os.system(cmd)
        
        return output_filename, title
    except Exception as e:
        print(f"Error: {e}")
        return None, None
    finally:
        driver.quit()

# --- 4. QUEUE WORKER ---
async def queue_worker(application):
    print("👷 Worker started...")
    while True:
        url, chat_id = await download_queue.get()
        try:
            await application.bot.send_message(chat_id, f"🔄 Processing: {url}")
            
            # 1. Download
            filename, title = await asyncio.to_thread(process_video, url)
            
            if filename and os.path.exists(filename):
                # 2. Upload
                await application.bot.send_message(chat_id, "☁️ Uploading to Drive...")
                file_id = await upload_to_drive(filename, filename)
                
                if file_id:
                    await application.bot.send_message(chat_id, f"✅ **Success!**\nFile: {title}\nSaved to Drive.", parse_mode='Markdown')
                else:
                    await application.bot.send_message(chat_id, "❌ Upload Failed (Check Creds).")
                
                # 3. Cleanup
                os.remove(filename)
            else:
                await application.bot.send_message(chat_id, "❌ Download Failed.")
                
        except Exception as e:
            await application.bot.send_message(chat_id, f"Error: {e}")
        finally:
            download_queue.task_done()

# --- 5. TELEGRAM HANDLER ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    chat_id = update.effective_chat.id
    
    # Add to Queue
    await download_queue.put((url, chat_id))
    q_pos = download_queue.qsize()
    await context.bot.send_message(chat_id, f"✅ Added to Queue (Position: {q_pos})")

if __name__ == '__main__':
    # Start Keep Alive
    start_keep_alive()
    
    # Start Bot
    if not TOKEN:
        print("❌ Error: TELEGRAM_TOKEN missing.")
    else:
        app_bot = ApplicationBuilder().token(TOKEN).build()
        app_bot.add_handler(MessageHandler(filters.TEXT, handle_message))
        
        # Start Worker
        loop = asyncio.get_event_loop()
        loop.create_task(queue_worker(app_bot))
        
        print("🤖 Bot is Live!")
        app_bot.run_polling()
