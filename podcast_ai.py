import os
import logging
import random
import feedparser
import requests
import schedule
import time
import uvicorn
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks
from elevenlabs import Voice, generate, save
from pydantic import BaseModel
import threading
from google.cloud import storage
from feedgen.feed import FeedGenerator

# Load environment variables (both .env and Google Cloud environment)
load_dotenv()

# Logging setup - use structured logging for Google Cloud
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="AI Podcast Generator API")

# Environment variables
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
PODCAST_DIR = os.getenv("PODCAST_DIR", "/tmp/episodes")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")  # Add your GCS bucket name
VOICE_NAME = os.getenv("VOICE_NAME", "Bella") # Elevenlabs voice name
SCHEDULED_TIME = os.getenv("SCHEDULED_TIME", "08:00") # Time for local scheduling
ENABLE_LOCAL_SCHEDULER = os.getenv("ENABLE_LOCAL_SCHEDULER", "false").lower() == "true"
RSS_FEED_PATH = os.getenv("RSS_FEED_PATH", "feed.rss")
PODCAST_TITLE = os.getenv("PODCAST_TITLE", "The Neural Narrative")
PODCAST_AUTHOR = os.getenv("PODCAST_AUTHOR", "AI Podcast Generator")
PODCAST_EMAIL = os.getenv("PODCAST_EMAIL", "ai@example.com")
PODCAST_DESCRIPTION = os.getenv("PODCAST_DESCRIPTION", "An AI-generated podcast about technology.")
PODCAST_BASE_URL = os.getenv("PODCAST_BASE_URL", "https://example.com/podcast")

os.makedirs(PODCAST_DIR, exist_ok=True)

# API Models
class PodcastResponse(BaseModel):
    podcast_text: str
    audio_file: str
    created_at: str

# Keep track of episodes for RSS feed generation
podcast_episodes = []

# AI Guest Personalities
AI_GUESTS = [
    {"name": "Dr. Nova", "style": "formal", "intro": "As an AI researcher, I can tell you..."},
    {"name": "Tech Rebel", "style": "casual", "intro": "Look, the way I see it..."},
    {"name": "ByteBot 3000", "style": "humor", "intro": "Oh great, another AI debate! Let's dive in..."},
    {"name": "LogicCore", "style": "logical", "intro": "Pure logic dictates the outcome."},
    {"name": "Glitch", "style": "chaotic", "intro": "Binary chaos detected, let's analyze..."},
]

# Function to fetch AI & Tech news (Reddit, Twitter, RSS, etc.)
def fetch_tech_news():
    news_sources = [
        "https://news.ycombinator.com/rss",
        "https://www.theverge.com/rss/index.xml",
        "https://www.wired.com/feed/rss"
    ]
    news_list = []

    try:
        for source in news_sources:
            try:
                feed = feedparser.parse(source)
                for entry in feed.entries[:3]:
                    news_list.append({
                        "title": entry.title,
                        "link": entry.link,
                        "summary": entry.get("summary", "No summary available"),
                        "source": source
                    })
                    logger.info(f"Fetched news: {entry.title}")
            except Exception as e:
                logger.error(f"Error fetching from {source}: {str(e)}")
                continue
    except Exception as e:
        logger.error(f"Error in fetch_tech_news: {str(e)}")

    # If we have more than 5 stories, randomly select 5 for variety
    if len(news_list) > 5:
        news_list = random.sample(news_list, 5)
    
    return news_list if news_list else [{"title": "No fresh news found.", "link": "", "summary": "Backup topic: Recent advancements in AI technology", "source": "fallback"}]

# Function to simulate AI-generated conversations
def generate_conversation(topic):
    try:
        guest = random.choice(AI_GUESTS)
        host_intros = [
            "Let's get your thoughts on this.",
            "What's your take on this story?",
            "I'd love to hear your perspective on this.",
            "Any insights to share about this news?",
            "How do you interpret this development?"
        ]
        host_responses = [
            "That's fascinating.",
            "I hadn't thought of it that way.",
            "Interesting perspective!",
            "You make a good point there.",
            "That's quite insightful."
        ]
        
        host_intro = random.choice(host_intros)
        host_response = random.choice(host_responses)
        
        # Format the conversation with more natural podcast structure
        conversation = f"\nHost: Let's talk about {topic['title']}. {host_intro}\n\n"
        conversation += f"{guest['name']} ({guest['style']}): {guest['intro']} "
        conversation += f"I was reading about {topic['title']}. "
        conversation += f"The article mentions {topic['summary'][:100]}... "
        conversation += f"This is particularly interesting because it highlights the rapid pace of technological change.\n\n"
        conversation += f"Host: {host_response} What implications do you think this has for the future?\n\n"
        conversation += f"{guest['name']}: Well, if we extrapolate from current trends, "
        conversation += f"we might see significant changes in how we interact with technology. "
        conversation += f"This development could potentially impact various sectors including "
        conversation += random.choice(["healthcare", "education", "finance", "entertainment", "transportation"]) + ".\n"
        
        return conversation
    except Exception as e:
        logger.error(f"Error in generate_conversation: {str(e)}")
        return f"\nPodcast Topic: {topic['title']}\nGuest: Let me share my thoughts on this topic. {topic['summary'][:100]}..."

# ElevenLabs Text-to-Speech Integration
def text_to_speech(text, filename=None):
    if not ELEVENLABS_API_KEY:
        logger.warning("ElevenLabs API Key is missing. Audio generation skipped.")
        return "audio_generation_skipped.mp3"

    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(PODCAST_DIR, f"podcast_episode_{timestamp}.mp3")

    try:
        audio = generate(
            text=text,
            voice=Voice(name=VOICE_NAME),
            api_key=ELEVENLABS_API_KEY
        )
        save(audio, filename)
        logger.info(f"Audio saved to {filename}")
        return filename
    except Exception as e:
        logger.error(f"Error in text_to_speech: {str(e)}")
        return "audio_generation_failed.mp3"

# Function to upload to Google Cloud Storage
def upload_to_gcs(bucket_name, source_file_name, destination_blob_name):
    if not bucket_name:
        logger.warning("GCS_BUCKET_NAME is not set. Skipping GCS upload.")
        return None

    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(source_file_name)
        
        # Make the blob publicly accessible
        blob.make_public()
        public_url = blob.public_url
        
        logger.info(f"File {source_file_name} uploaded to gs://{bucket_name}/{destination_blob_name}")
        logger.info(f"Public URL: {public_url}")
        return public_url
    except Exception as e:
        logger.error(f"Error uploading to GCS: {str(e)}")
        return None

# Function to generate RSS Feed
def generate_rss_feed(episodes):
    try:
        fg = FeedGenerator()
        fg.id(PODCAST_BASE_URL)
        fg.title(PODCAST_TITLE)
        fg.author({'name': PODCAST_AUTHOR, 'email': PODCAST_EMAIL})
        fg.link(href=PODCAST_BASE_URL, rel='alternate')
        fg.link(href=f"{PODCAST_BASE_URL}/{RSS_FEED_PATH}", rel='self')
        fg.description(PODCAST_DESCRIPTION)
        fg.language('en')
        fg.lastBuildDate(datetime.now())
        
        for episode in episodes:
            fe = fg.add_entry()
            fe.id(episode['audio_url'])
            fe.title(f"Episode {episode['timestamp']}")
            fe.link(href=episode['audio_url'])
            fe.description(episode['podcast_text'][:500] + "...")  # Truncate for description
            fe.enclosure(episode['audio_url'], 0, 'audio/mpeg')
            fe.pubDate(datetime.fromisoformat(episode['created_at']))
        
        fg.rss_file(os.path.join(PODCAST_DIR, RSS_FEED_PATH))
        
        # Upload RSS feed to GCS if configured
        if GCS_BUCKET_NAME:
            upload_to_gcs(
                GCS_BUCKET_NAME, 
                os.path.join(PODCAST_DIR, RSS_FEED_PATH), 
                RSS_FEED_PATH
            )
            
        logger.info(f"RSS feed generated at {os.path.join(PODCAST_DIR, RSS_FEED_PATH)}")
    except Exception as e:
        logger.error(f"Error generating RSS feed: {str(e)}")

# Function to generate an AI Podcast Episode
def generate_podcast():
    try:
        logger.info("Fetching tech news...")
        topics = fetch_tech_news()

        if not topics:
            logger.error("No topics found. Skipping podcast generation.")
            return {"podcast_text": "No topics available for this episode.",
                    "audio_file": "no_episode.mp3",
                    "created_at": datetime.now().isoformat()}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        episode_content = f"Welcome to {PODCAST_TITLE}. Episode {timestamp}.\n"
        episode_content += f"Today we'll be discussing the latest in AI and tech news.\n\n"

        for topic in topics:
            episode_content += generate_conversation(topic) + "\n\n"

        episode_content += f"\nThanks for listening to this episode of {PODCAST_TITLE}. Join us next time for more AI discussions!"

        logger.info("Generating audio...")
        audio_filename = text_to_speech(episode_content, os.path.join(PODCAST_DIR, f"podcast_episode_{timestamp}.mp3"))
        
        # Upload to GCS if configured
        audio_url = None
        if GCS_BUCKET_NAME:
            gcs_path = f"episodes/podcast_episode_{timestamp}.mp3"
            audio_url = upload_to_gcs(GCS_BUCKET_NAME, audio_filename, gcs_path)
        
        final_url = audio_url if audio_url else audio_filename
        
        # Add to episodes list for RSS
        episode_data = {
            "podcast_text": episode_content,
            "audio_file": audio_filename,
            "audio_url": final_url,
            "timestamp": timestamp,
            "created_at": datetime.now().isoformat()
        }
        
        global podcast_episodes
        podcast_episodes.append(episode_data)
        
        # Keep only the last 10 episodes in memory
        if len(podcast_episodes) > 10:
            podcast_episodes = podcast_episodes[-10:]
            
        # Generate RSS feed
        generate_rss_feed(podcast_episodes)

        logger.info("Podcast generation completed successfully!")
        
        return {"podcast_text": episode_content,
                "audio_file": final_url,
                "created_at": datetime.now().isoformat()}

    except Exception as e:
        logger.error(f"Error generating podcast: {str(e)}")
        return {"podcast_text": "Error generating podcast.",
                "audio_file": "error.mp3",
                "created_at": datetime.now().isoformat()}

# API Endpoint to generate a podcast episode
@app.post("/generate", response_model=PodcastResponse, status_code=201)
async def generate_episode(background_tasks: BackgroundTasks):
    """
    Generates an AI podcast episode and returns the text content and audio URL.
    """
    try:
        result = generate_podcast()
        return result
    except Exception as e:
        logger.error(f"Error in generate_episode endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# API Endpoint to get the latest episode
@app.get("/latest", response_model=PodcastResponse)
async def get_latest_episode():
    """
    Returns the latest podcast episode.
    """
    try:
        if not podcast_episodes:
            raise HTTPException(status_code=404, detail="No episodes found")
        
        latest = podcast_episodes[-1]
        return {
            "podcast_text": latest["podcast_text"],
            "audio_file": latest["audio_url"],
            "created_at": latest["created_at"]
        }
    except Exception as e:
        logger.error(f"Error in get_latest_episode endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# API Endpoint to list all episodes
@app.get("/episodes")
async def list_episodes():
    """
    Returns a list of all podcast episodes.
    """
    try:
        return podcast_episodes
    except Exception as e:
        logger.error(f"Error in list_episodes endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}

# Scheduler for generating podcasts (Cloud Scheduler or local)
def schedule_podcast_generation():
    schedule.every().day.at(SCHEDULED_TIME).do(generate_podcast)
    logger.info(f"Podcast generation scheduled for {SCHEDULED_TIME} every day.")
    while True:
        schedule.run_pending()
        time.sleep(60)

if ENABLE_LOCAL_SCHEDULER:
    # Run scheduler in a separate thread
    scheduler_thread = threading.Thread(target=schedule_podcast_generation)
    scheduler_thread.daemon = True
    scheduler_thread.start()

# For local testing
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
