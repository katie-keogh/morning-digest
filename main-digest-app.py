#!/usr/bin/env python3
"""
morning-digest: fetches weather, calendar, commute, news, and renders HTML email.
Designed to be run from CI (GitHub Actions). Config via environment variables / secrets.
"""

import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from dateutil import parser
import pytz
from jinja2 import Environment, FileSystemLoader, select_autoescape
from caldav import DAVClient

from dotenv import load_dotenv
load_dotenv()

# -------------------------
# CONFIG (edit via env / secrets)
# -------------------------
# Required: SMTP settings
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")           # e.g. your.email@gmail.com
SMTP_PASS = os.getenv("SMTP_PASS")           # app password or SMTP password
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.getenv("EMAIL_TO", SMTP_USER)  # default to same as sender

# Timezone & schedule
TZ = os.getenv("TZ", "Europe/Dublin")

# Weather - OpenWeatherMap (recommended)
OWM_KEY = os.getenv("OWM_KEY")  # get from https://openweathermap.org/
OWM_CITY = os.getenv("OWM_CITY")  # e.g. "Dublin,IE" or lat/lon usage later

ICLOUD_USER = os.getenv("ICLOUD_USER")
ICLOUD_PASS = os.getenv("ICLOUD_PASS")

LAT = os.getenv("LOCATION_LAT", "53.3498")  # Dublin latitude
LON = os.getenv("LOCATION_LON", "-6.2603")  # Dublin longitude

# Connect to iCloud CalDAV server
client = DAVClient(
    url="https://caldav.icloud.com/",
    username=ICLOUD_USER,
    password=ICLOUD_PASS
)

# Commute (Google Maps Directions API) optional
GMAPS_KEY = os.getenv("GMAPS_KEY")
ORIGIN = os.getenv("ORIGIN")   # e.g. "Home address, City"
DESTINATION = os.getenv("DESTINATION")  # e.g. "Office address, City"

# News (NewsAPI)
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
NEWS_SOURCES = os.getenv("NEWS_SOURCES", "news")  # or leave blank to use top headlines
MAX_HEADLINES = 5

# Tasks: simple CSV/TXT file in repo or Todoist token (optional)
TODOIST_TOKEN = os.getenv("TODOIST_TOKEN")  # optional

# Quote source: builtin fallback
QUOTES = [
    "The journey of a thousand miles begins with a single step.",
    "Small daily improvements are the key to staggering long-term results.",
    "Do something today that your future self will thank you for."
]

# Template directory
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
TEMPLATE_FILE = "template.html"

# -------------------------
# Helper functions
# -------------------------
def render_template(context):
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"])
    )
    tpl = env.get_template(TEMPLATE_FILE)
    return tpl.render(**context)

def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    part = MIMEText(html_body, "html")
    msg.attach(part)
    print("Connecting to SMTP...", SMTP_HOST, SMTP_PORT)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        if SMTP_PORT == 587:
            server.starttls()
            server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
    print("Email sent to", EMAIL_TO)

def parse_google_weather(json_data):
    """
    Extracts simple weather info from Google Weather API JSON.
    Returns a string like "13°C, Partly sunny".
    """
    try:
        forecast = json_data["forecastDays"][0]  # today's forecast
        max_temp = forecast.get("maxTemperature", {}).get("degrees", "")
        min_temp = forecast.get("minTemperature", {}).get("degrees", "")
        daytime_condition = forecast.get("daytimeForecast", {}).get("weatherCondition", {}).get("description", {}).get("text", "")

        if max_temp != "" and min_temp != "":
            temp_str = f"{round(min_temp)}°C - {round(max_temp)}°C"
        else:
            temp_str = ""

        if daytime_condition:
            print("Parsed weather:", temp_str, daytime_condition)
            return f"{temp_str}, {daytime_condition}"
        else:
            return temp_str or "Weather data unavailable"

    except Exception as e:
        print("Error parsing weather JSON:", e)
        return "Weather data unavailable"

# Example usage:
# json_data = response.json()  # from your Google Weather API request
# weather_summary = parse_google_weather(json_data)
# print(weather_summary)  # e.g. "1°C - 13°C, Partly sunny"

def get_weather():
    if not GMAPS_KEY:
        return None

    try:
        # Google Weather API endpoint
        url = "https://weather.googleapis.com/v1/forecast/days:lookup"

        # Pass parameters
        params = {
            "key": GMAPS_KEY,
            "location.latitude": LAT,
            "location.longitude": LON,
            "days": 1
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data  # <-- just return the full JSON

    except Exception as e:
        print("Google Weather error:", e)
        return None

def get_news(country=None, sources=None):
    """
    Fetch top headlines.
    country: 'ie' for Ireland, None for worldwide
    sources: optional comma-separated NewsAPI source IDs
    """
    if not NEWSAPI_KEY:
        return []

    url = "https://newsapi.org/v2/top-headlines"
    params = {
        "apiKey": NEWSAPI_KEY,
        "pageSize": MAX_HEADLINES,
        "language": "en"
    }

    if country:
        params["country"] = country
    if sources:
        params["sources"] = sources

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        articles = data.get("articles", [])
        news_list = []
        for article in articles:
            title = article.get("title", "")
            url = article.get("url", "")
            source = article.get("source", {}).get("name", "")
            news_list.append({"title": title, "url": url, "source": source})
        print(f"Fetched {len(news_list)} news articles")
        return news_list
    except Exception as e:
        print("News fetch error:", e)
        return []

# Simple quote/fact
import random
def get_quote():
    return random.choice(QUOTES)

def get_calendar_events(max_events=6):
    """
    Fetches events from your iCloud calendar for today.
    Returns a list of dicts: [{"start": "HH:MM", "summary": "Event title"}, ...]
    """
    if not ICLOUD_USER or not ICLOUD_PASS:
        return []

    try:
        client = DAVClient(
            url="https://caldav.icloud.com/",
            username=ICLOUD_USER,
            password=ICLOUD_PASS
        )
        principal = client.principal()
        calendars = principal.calendars()
        if not calendars:
            return []

        calendar = calendars[0]  # pick the first calendar
        today = datetime.now()
        tomorrow = today + timedelta(days=1)
        events = calendar.search(start=today, end=tomorrow)

        event_list = []
        for event in events[:max_events]:
            vevent = event.vobject_instance.vevent
            start_time = vevent.dtstart.value.strftime("%H:%M")
            summary = vevent.summary.value
            event_list.append({"start": start_time, "summary": summary})

        print(f"Fetched {len(event_list)} events from iCloud Calendar")

        if not event_list:
            return [{"start": "", "summary": "No events today"}]
    
        return event_list

    except Exception as e:
        print("Apple Calendar error:", e)
        return []

# Commute via Google Maps
def get_commute_time():
    if not GMAPS_KEY or not ORIGIN or not DESTINATION:
        return "Commute info not set"

    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": ORIGIN,
        "destination": DESTINATION,
        "key": GMAPS_KEY,
        "departure_time": "now",
        "mode": "driving"
    }

    resp = requests.get(url, params=params)
    data = resp.json()

    if data["status"] != "OK":
        return f"Maps error: {data.get('status')}"

    routes = data.get("routes")
    if not routes:
        return "No routes found"

    legs = routes[0].get("legs")
    if not legs:
        return "No route legs found"

    leg = legs[0]
    duration = leg["duration"]["text"]
    duration_in_traffic = leg.get("duration_in_traffic", {}).get("text", duration)
    print(f"{ORIGIN} → {DESTINATION}: {duration_in_traffic}")
    return f"{duration_in_traffic}"

# Tasks: simple local file fallback
def get_tasks():
    # If TODOIST_TOKEN present, you can add Todoist API fetch here
    # Fallback: look for tasks.txt in repo root (one task per line)
    tasks = []
    try:
        path = os.path.join(os.path.dirname(__file__), "tasks.txt")
        if os.path.exists(path):
            with open(path, "r") as fh:
                for line in fh:
                    s = line.strip()
                    if s:
                        tasks.append({"title": s})
    except Exception as e:
        print("Tasks error:", e)
    return tasks

import requests

def get_word_of_the_day():
    print("Fetching word of the day...")
    url = "https://api.wordnik.com/v4/words.json/wordOfTheDay"
    params = {"api_key": os.getenv("WORDNIK_KEY")}
    try:
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        print("Fetched word of the day:", data['word'])
        return f"{data['word']}: {data['definitions'][0]['text']}"
    except Exception as e:
        print("Error fetching word of the day:", e)
        return "Word of the day unavailable"

def get_history_today():
    today = datetime.now()
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{today.month}/{today.day}"
    headers = {"User-Agent": "morning-digest-app/1.0 (kkeogh123@gmail.com)"}
    try:
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("events", [])
        # pick up to 3 events
        print(f"Fetched {len(events)} historical events")
        return [f"{e['year']}: {e['text']}" for e in events[:3]]
    except Exception as e:
        print("Error fetching historical events :", e)
        return ["No historical events available"]

# -------------------------
# Main
# -------------------------
def main():
    tz = pytz.timezone(TZ)
    now = datetime.now(tz)
    # Fetch data
    # Use them together
    weather_json = get_weather()
    weather = parse_google_weather(weather_json) if weather_json else "Weather data unavailable"
    events = get_calendar_events()
    tasks = get_tasks()
    commute = get_commute_time()
    news_ire = get_news(sources="the-irish-times,independent-ie,irish-examiner")
    news = get_news()
    quote = get_quote()
    wotd = get_word_of_the_day()
    history = get_history_today()
    # Build context for template
    context = {
        "generated_at": now.strftime("%A, %d %B %Y %H:%M"),
        "weather": weather,
        "events": events,
        "tasks": tasks,
        "commute": commute,
        "news_ire": news_ire,
        "news": news,
        "quote": quote,
        "word_of_the_day": wotd,
        "history_today": history
    }
    html = render_template(context)
    subject = f"Morning Digest — {now.strftime('%A %d %b')}"
    send_email(subject, html)

if __name__ == "__main__":
    main()
